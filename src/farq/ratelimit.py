"""Small request throttles.

SlidingWindow is in-process memory: on Vercel every warm function instance keeps its own
counts and a cold start forgets them, so the effective limit is "per instance" and a
determined client spread over many instances gets more. That is acceptable for the cheap
search endpoints it guards (it stops one client hammering Haraj through one instance).
Login throttling is stricter and lives in the database instead (Store.login_failures), so
it holds across instances.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import Request


def client_ip(request: Request) -> str:
    """Vercel overwrites x-forwarded-for / x-real-ip with the caller's address, so the first
    hop is trustworthy there. Locally it falls back to the socket peer."""
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class SlidingWindow:
    def __init__(self, limit: int, window_seconds: float, max_keys: int = 10_000):
        self.limit = limit
        self.window = window_seconds
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        if self.limit <= 0:
            return True
        now = time.monotonic() if now is None else now
        cutoff = now - self.window
        with self._lock:
            if len(self._hits) >= self.max_keys and key not in self._hits:
                # Forget idle keys so memory stays bounded.
                for stale in [name for name, hits in self._hits.items() if not hits or hits[-1] < cutoff]:
                    del self._hits[stale]
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] < cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True
