"""Per-application rate limiting (sliding one-minute window, in process memory).

Good for a single API process. With several workers or servers each enforces its own window;
Phase 7 moves the counters to Redis so the limit is shared.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window: float = 60.0, now: float | None = None) -> tuple[bool, int, int]:
        """Record a request. Returns (allowed, remaining, seconds until a slot frees)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return False, 0, max(1, int(q[0] + window - now) + 1)
            q.append(now)
            return True, limit - len(q), 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()
