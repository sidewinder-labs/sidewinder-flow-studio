"""Which flows are executing right now, in this process.

Run history can't answer that: a row stays "running" forever if the backend
dies mid-run. Manual and scheduled runs both go through execute_flow, which
registers itself here for its duration.
"""
from __future__ import annotations

import threading
from collections import Counter
from contextlib import contextmanager

_lock = threading.Lock()
_runs: Counter[tuple[str, str]] = Counter()       # (project_id, flow_id) → live runs


@contextmanager
def tracking(project_id: str, flow_id: str):
    key = (project_id, flow_id)
    with _lock:
        _runs[key] += 1
    try:
        yield
    finally:
        with _lock:
            _runs[key] -= 1
            if _runs[key] <= 0:
                del _runs[key]


_nodes: dict[tuple[str, str], Counter[str]] = {}   # (project_id, flow_id) → labels running now


def node_started(project_id: str, flow_id: str, label: str) -> None:
    with _lock:
        _nodes.setdefault((project_id, flow_id), Counter())[label] += 1


def node_finished(project_id: str, flow_id: str, label: str) -> None:
    with _lock:
        c = _nodes.get((project_id, flow_id))
        if c is not None:
            c[label] -= 1
            if c[label] <= 0:
                del c[label]
            if not c:
                del _nodes[(project_id, flow_id)]


def snapshot() -> list[dict]:
    """Every live run with the nodes it is executing (the guard's heartbeat)."""
    with _lock:
        return [{"project_id": p, "flow_id": f, "nodes": sorted(_nodes.get((p, f), {}))}
                for (p, f), n in _runs.items() if n > 0]


def running_flows(project_id: str) -> set[str]:
    """Ids of the project's flows that are executing now."""
    with _lock:
        return {f for (p, f), n in _runs.items() if p == project_id and n > 0}


def any_running() -> bool:
    with _lock:
        return any(n > 0 for n in _runs.values())


def all_running() -> list[tuple[str, str]]:
    """(project_id, flow_id) of every flow executing now."""
    with _lock:
        return [k for k, n in _runs.items() if n > 0]


async def close_interrupted_runs() -> int:
    """At startup: a run still marked "running" belonged to the previous
    process, which stopped mid-run (crash, out of memory, restart), so it
    would otherwise show as running forever. Mark it "crashed", naming the
    node it was running and, when memory was nearly full, saying it most
    likely ran out of memory (from the memory guard's heartbeat).

    Assumes one SFS process per metadata database (true today)."""
    from datetime import UTC, datetime

    import sqlalchemy as sa

    from app.core.database import AsyncSessionLocal
    from app.core.memory_guard import take_heartbeat, take_stop_note
    from app.models.run_history import RunHistory

    note, beat = take_stop_note(), take_heartbeat()
    nodes = {(r["project_id"], r["flow_id"]): r.get("nodes") or [] for r in (beat or {}).get("runs", [])}
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(sa.select(RunHistory).where(RunHistory.status == "running"))).scalars().all()
        now = datetime.now(UTC)
        for r in rows:
            r.status, r.finished_at = "crashed", now
            r.error = crash_reason(note, beat, nodes.get((r.project_id, r.flow_id), []))
        await db.commit()
    return len(rows)


def crash_reason(note: dict | None, beat: dict | None, nodes: list[str]) -> str:
    from app.core.memory import fmt_size
    where = (f" while running node {', '.join(repr(n) for n in nodes)}" if nodes else "")
    if note and note.get("message"):
        return note["message"] + (f" (node {', '.join(repr(n) for n in nodes)})" if nodes else "")
    used, total, budget = ((beat or {}).get(k) for k in ("used", "total", "budget"))
    ceiling = min(x for x in (total, budget) if x) if (total or budget) else None
    if used and ceiling and used >= 0.85 * ceiling:
        return (f"Crashed{where}: SFS was using {fmt_size(used)} of {fmt_size(total or ceiling)} memory, so it most "
                "likely ran out of memory. A pandas node loads its whole input: use Polars or DuckDB nodes "
                "for large data (Help → The five engines), or lower the memory budget in Meta → Settings so "
                "SFS stops the run before the system does.")
    return f"Crashed: SFS stopped{where} (restart, crash or the machine shutting down)."
