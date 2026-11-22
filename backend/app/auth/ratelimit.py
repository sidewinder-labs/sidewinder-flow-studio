"""Slows down password guessing.

Each failed sign-in from one address doubles the wait before that address may
try again (after the first few), up to 15 minutes. Across all addresses, too
many failures in a minute pause sign-in for everyone briefly, so rotating
addresses doesn't help. In memory: a restart clears it, which is fine for
slowing guessing down.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

FREE_TRIES = 5            # failures allowed before waits start
MAX_WAIT_S = 15 * 60
GLOBAL_FAILS_PER_MIN = 30
GLOBAL_PAUSE_S = 60


@dataclass
class _State:
    fails: int = 0
    until: float = 0.0


_by_key: dict[str, _State] = {}
_recent: deque[float] = deque()
_global_until = 0.0


def wait_seconds(key: str) -> int:
    """Seconds this address must wait before trying again (0 = may try now)."""
    now = time.monotonic()
    s = _by_key.get(key)
    w = max(s.until - now if s else 0.0, _global_until - now)
    return int(w + 0.999) if w > 0 else 0


def failed(key: str) -> None:
    global _global_until
    now = time.monotonic()
    s = _by_key.setdefault(key, _State())
    s.fails += 1
    if s.fails >= FREE_TRIES:
        s.until = now + min(2 ** (s.fails - FREE_TRIES), MAX_WAIT_S)
    _recent.append(now)
    while _recent and now - _recent[0] > 60:
        _recent.popleft()
    if len(_recent) >= GLOBAL_FAILS_PER_MIN:
        _global_until = now + GLOBAL_PAUSE_S
    if len(_by_key) > 10_000:                     # don't grow without bound
        for k in [k for k, v in _by_key.items() if v.until < now][:5_000]:
            _by_key.pop(k, None)


def succeeded(key: str) -> None:
    _by_key.pop(key, None)


def reset() -> None:
    global _global_until
    _by_key.clear()
    _recent.clear()
    _global_until = 0.0


def client_key(conn) -> str:
    """The client's address. Behind SFS's nginx the direct peer is the proxy,
    which passes the real one in X-Real-IP; an address set by a client who
    talks to the backend directly gains nothing, because of the global limit."""
    real = conn.headers.get("x-real-ip")
    return real or (conn.client.host if conn.client else "unknown")
