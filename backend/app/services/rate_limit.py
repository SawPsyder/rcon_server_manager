"""In-process fixed-window rate limiting for the unauthenticated endpoints.

Deliberately simple: this app is a single uvicorn process with a handful of
operators, so a dict guarded by a lock is enough. It is not shared across
replicas - if this ever runs behind more than one worker, move the counters
into the database or Redis.

Keys are caller-supplied strings, e.g. "login:198.51.100.7".
"""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
# key -> (window_start_epoch, count)
_hits: dict[str, tuple[float, int]] = {}
# Bound the dict so a flood of distinct keys cannot grow it without limit.
_MAX_KEYS = 4096


def check(key: str, limit: int, window_seconds: int) -> bool:
    """Record an attempt. False means the caller is over the limit."""
    now = time.time()
    with _lock:
        if len(_hits) > _MAX_KEYS:
            cutoff = now - window_seconds
            for stale in [k for k, (start, _) in _hits.items() if start < cutoff]:
                del _hits[stale]
            if len(_hits) > _MAX_KEYS:
                _hits.clear()

        start, count = _hits.get(key, (now, 0))
        if now - start >= window_seconds:
            start, count = now, 0
        count += 1
        _hits[key] = (start, count)
        return count <= limit


def reset(key: str) -> None:
    """Clear a counter, e.g. after a successful login."""
    with _lock:
        _hits.pop(key, None)


def reset_all() -> None:
    """Test helper."""
    with _lock:
        _hits.clear()
