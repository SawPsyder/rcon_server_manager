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
# key -> (window_start_epoch, count, window_seconds)
_hits: dict[str, tuple[float, int, int]] = {}
# Bound the dict so a flood of distinct keys cannot grow it without limit.
_MAX_KEYS = 4096
# Backstop for entries whose stored window is missing or huge.
_STALE_AFTER = 3600.0


def check(key: str, limit: int, window_seconds: int) -> bool:
    """Record an attempt. False means the caller is over the limit."""
    now = time.time()
    with _lock:
        if len(_hits) >= _MAX_KEYS:
            expired = [
                k
                for k, (start, _, window) in _hits.items()
                if now - start >= window or start < now - _STALE_AFTER
            ]
            for stale in expired:
                del _hits[stale]
            # Still full: refuse new keys rather than wiping every counter.
            # Clearing the map used to reset login:email:victim after a flood
            # of unique keys, which is exactly the stuffing defence we need.
            if key not in _hits and len(_hits) >= _MAX_KEYS:
                return False

        start, count, _ = _hits.get(key, (now, 0, window_seconds))
        if now - start >= window_seconds:
            start, count = now, 0
        count += 1
        _hits[key] = (start, count, window_seconds)
        return count <= limit


def reset(key: str) -> None:
    """Clear a counter, e.g. after a successful login."""
    with _lock:
        _hits.pop(key, None)


def reset_all() -> None:
    """Test helper."""
    with _lock:
        _hits.clear()
