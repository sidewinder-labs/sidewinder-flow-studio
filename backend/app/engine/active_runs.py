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
