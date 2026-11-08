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
    would otherwise show as running forever. Mark it as an error, with the
    memory guard's reason when the guard caused the restart.

    Assumes one SFS process per metadata database (true today)."""
    from datetime import UTC, datetime

    import sqlalchemy as sa

    from app.core.database import AsyncSessionLocal
    from app.core.memory_guard import take_stop_note
    from app.models.run_history import RunHistory

    note = take_stop_note()
    reason = (note or {}).get("message") or (
        "Interrupted: SFS stopped while this flow was running (restart, crash or out of memory).")
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(sa.select(RunHistory).where(RunHistory.status == "running"))).scalars().all()
        now = datetime.now(UTC)
        for r in rows:
            r.status, r.finished_at, r.error = "error", now, reason
        await db.commit()
    return len(rows)
