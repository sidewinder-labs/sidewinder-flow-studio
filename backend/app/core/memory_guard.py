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
HEARTBEAT_FILE = "running.json"         # live runs, their nodes and memory; gone when idle


class MemoryBudgetExceeded(RuntimeError):
    pass


class InputTooLarge(MemoryBudgetExceeded):
    """A pandas node's input won't fit: refused before anything is loaded."""


def room() -> tuple[int | None, str]:
    """How much more memory a node may take now, and what limits it."""
    used = memory.process_bytes() or 0
    avail = memory.available_bytes()
    budget = budget_bytes()
    options = []
    if budget:
        options.append((budget - used, "left in the memory budget"))
    if avail is not None:
        options.append((avail, "free on this machine"))
    if not options:
        return None, ""
    n, why = min(options, key=lambda o: o[0])
    return max(n, 0), why


def check_pandas_load(load_bytes: int, rows: int, label: str) -> None:
    """Refuse a pandas node whose input can't fit, before loading it. Off
    together with the budget (SFS_MEMORY_BUDGET=off)."""
    from app.core import app_settings
    if app_settings.get("SFS_MEMORY_BUDGET") == "off":
        return
    free, why = room()
    if free is None or load_bytes <= free:
        return
    raise InputTooLarge(
        f"Not run: loading this node's input ({rows:,} rows) into pandas needs about "
        f"{memory.fmt_size(load_bytes)}, and only {memory.fmt_size(free)} is {why}. "
        "A pandas node loads its whole input into memory. Use a Polars or DuckDB node instead: "
        "both work on data larger than memory (Help → The five engines). "
        "Or raise the memory budget in Meta → Settings.")


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


def _interrupt_duckdb() -> None:
    with _lock:
        cons = list(_duckdb)
    for con in cons:
        try:
            con.interrupt()
        except Exception:
            pass


def _trip(used: int, budget: int) -> None:
    global _exceeded
    with _lock:
        if _exceeded:
            return
        _exceeded = _message(used, budget)
    log.warning(_exceeded)
    _interrupt_duckdb()


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


def _heartbeat(used, budget) -> None:
    """Record what is running and the memory in use, so a crash the guard
    didn't see coming (the OS killing SFS) can be explained at the next start."""
    from app.engine import active_runs
    try:
        from app.packs.store import data_dir
        p = data_dir() / HEARTBEAT_FILE
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"at": datetime.now(UTC).isoformat(), "used": used, "budget": budget,
                                   "total": memory.total_bytes(), "runs": active_runs.snapshot()}))
        tmp.replace(p)
    except Exception:
        pass


def _clear_heartbeat() -> None:
    try:
        from app.packs.store import data_dir
        (data_dir() / HEARTBEAT_FILE).unlink(missing_ok=True)
    except Exception:
        pass


def _watch() -> None:
    global _exceeded, _hard_logged
    from app.engine import active_runs
    was_running = False
    last_beat = 0.0
    while True:
        running = active_runs.any_running()
        if running:
            was_running = True
            budget, used = budget_bytes(), memory.process_bytes()
            if time.monotonic() - last_beat >= 1.0:
                _heartbeat(used, budget)
                last_beat = time.monotonic()
            if _exceeded:                        # again each time: a query that was only
                _interrupt_duckdb()              # about to start when it tripped is caught too
            if budget and used is not None:
                if used > hard_limit(budget) and _exceeded:
                    _hard_stop(used, budget)
                elif used > budget:
                    _trip(used, budget)
            time.sleep(INTERVAL_S)
        else:
            if was_running:                      # runs ended: hand memory back, reset
                was_running = False
                _clear_heartbeat()
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


def take_heartbeat() -> dict | None:
    """The last heartbeat of the previous process, removed once read (at startup)."""
    try:
        from app.packs.store import data_dir
        p = data_dir() / HEARTBEAT_FILE
        beat = json.loads(p.read_text())
        p.unlink(missing_ok=True)
        return beat
    except (OSError, ValueError):
        return None
