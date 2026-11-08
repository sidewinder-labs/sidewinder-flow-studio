"""Memory budget: stop runs before the machine runs out of memory.

A watcher thread samples the backend's memory (app.core.memory) four times a
second while a flow runs. Above the budget (setting SFS_MEMORY_BUDGET) the
running flows are stopped:

  * DuckDB queries are interrupted;
  * fast_write, streamed DuckDB output and the executor stop at their next
    batch or node (check() raises MemoryBudgetExceeded);
  * a pandas or Polars node in the middle of one call can't be stopped from
    outside (Python can't kill a thread). If memory keeps climbing past the
    hard limit — budget + 5% of memory, at least 512 MB — SFS restarts itself
    where something starts it again (Docker, the desktop app), after
    recording which runs it stopped so their history says why.

It's a soft limit, sampled: a sudden huge allocation can overshoot it. A hard
guarantee needs the container's own limit (Docker mem_limit).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime

from app.core import memory

log = logging.getLogger("sfs.memory")
INTERVAL_S = 0.25
STOP_FILE = "memory_stop.json"


class MemoryBudgetExceeded(RuntimeError):
    pass


_lock = threading.Lock()
_exceeded: str | None = None                  # the stop message while runs are being stopped
_duckdb: set = set()                          # open DuckDB connections, to interrupt
_thread: threading.Thread | None = None
_hard_logged = False


def budget_bytes() -> int | None:
    """The budget in bytes, or None when it's off or memory can't be measured."""
    from app.core import app_settings
    total = memory.total_bytes()
    raw = app_settings.get("SFS_MEMORY_BUDGET")
    if raw == "off":
        return None
    try:
        if not raw:
            return int(total * 0.8) if total else None
        if str(raw).endswith("%"):
            return int(total * float(str(raw)[:-1]) / 100) if total else None
        return memory.parse_size(raw)
    except (ValueError, TypeError):
        return int(total * 0.8) if total else None


def hard_limit(budget: int) -> int:
    total = memory.total_bytes() or budget
    return budget + max(int(total * 0.05), 512 << 20)


def duckdb_default_limit() -> str | None:
    """DuckDB's memory limit when none is set: half the budget, so it spills
    to disk before the budget stops the run."""
    b = budget_bytes()
    return f"{max(b // 2, 256 << 20) // (1 << 20)}MB" if b else None


def check() -> None:
    """Called between batches and nodes: raise if runs are being stopped."""
    if _exceeded:
        raise MemoryBudgetExceeded(_exceeded)


def exceeded() -> str | None:
    return _exceeded


def register_duckdb(con) -> None:
    with _lock:
        _duckdb.add(con)


def unregister_duckdb(con) -> None:
    with _lock:
        _duckdb.discard(con)


def _message(used: int, budget: int) -> str:
    return (f"Stopped: SFS was using {memory.fmt_size(used)} of its {memory.fmt_size(budget)} memory budget. "
            "Use Polars or DuckDB nodes for large data (a pandas node loads everything into memory), "
            "or raise the budget in Meta → Settings.")


def _trip(used: int, budget: int) -> None:
    global _exceeded
    with _lock:
        if _exceeded:
            return
        _exceeded = _message(used, budget)
        cons = list(_duckdb)
    log.warning(_exceeded)
    for con in cons:
        try:
            con.interrupt()
        except Exception:
            pass


def _hard_stop(used: int, budget: int) -> None:
    """Memory is still climbing in code that can't be interrupted: restart,
    leaving a note so the stopped runs are recorded with the reason."""
    global _hard_logged
    from app.core import restart
    from app.engine import active_runs
    msg = (_exceeded or _message(used, budget)) + " SFS restarted itself to free the memory."
    if not restart.can_restart():                # nothing would start SFS again: keep waiting
        if not _hard_logged:
            _hard_logged = True
            log.error("Memory is past the hard limit and can't be freed without a restart: %s",
                      memory.fmt_size(used))
        return
    log.error(msg)
    try:
        from app.packs.store import data_dir
        (data_dir() / STOP_FILE).write_text(json.dumps({
            "at": datetime.now(UTC).isoformat(), "message": msg,
            "flows": [list(k) for k in active_runs.all_running()]}))
    except Exception:
        pass
    import os
    os._exit(restart.EXIT_CODE)


def _watch() -> None:
    global _exceeded, _hard_logged
    from app.engine import active_runs
    was_running = False
    while True:
        running = active_runs.any_running()
        if running:
            was_running = True
            budget, used = budget_bytes(), memory.process_bytes()
            if budget and used is not None:
                if used > hard_limit(budget) and _exceeded:
                    _hard_stop(used, budget)
                elif used > budget:
                    _trip(used, budget)
            time.sleep(INTERVAL_S)
        else:
            if was_running:                      # runs ended: hand memory back, reset
                was_running = False
                memory.release()
                with _lock:
                    _exceeded = None
                _hard_logged = False
            time.sleep(1.0)


def start() -> None:
    global _thread
    if _thread is None and memory.process_bytes() is not None:
        _thread = threading.Thread(target=_watch, name="sfs-memory-guard", daemon=True)
        _thread.start()


def take_stop_note() -> dict | None:
    """The note a hard stop left, removed once read (at startup)."""
    try:
        from app.packs.store import data_dir
        p = data_dir() / STOP_FILE
        note = json.loads(p.read_text())
        p.unlink(missing_ok=True)
        return note
    except (OSError, ValueError):
        return None
