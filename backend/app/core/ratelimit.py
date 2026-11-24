"""Per-application rate limiting over a sliding one-minute window.

With ``ERP_REDIS_URL`` set the window lives in Redis (a sorted set per application), so the
limit is shared by every API process and server. Without Redis, or if Redis is unreachable,
each process keeps its own window in memory (fail-open to the local limit, never to unlimited).
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


# Runs atomically inside Redis: trim the window, check, and record in one step, so concurrent
# requests from many API processes can never slip past the limit together.
_SLIDING_WINDOW = """
local key, now, window, limit, member = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3]), ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  return {0, 0, oldest[2] or tostring(now)}
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, math.ceil(window) + 5)
return {1, limit - count - 1, '0'}
"""


class RedisRateLimiter:
    def __init__(self, fallback: RateLimiter) -> None:
        self.fallback = fallback
        self._script = None

    def hit(self, key: str, limit: int, window: float = 60.0, now: float | None = None) -> tuple[bool, int, int]:
        import uuid

        import redis

        from app.core.redis import get_redis

        client = get_redis()
        if client is None:
            return self.fallback.hit(key, limit, window, now)
        now = time.time() if now is None else now
        try:
            if self._script is None:
                self._script = client.register_script(_SLIDING_WINDOW)
            allowed, remaining, oldest = self._script(
                keys=[f"erp:ratelimit:{key}"], args=[now, window, limit, f"{now}:{uuid.uuid4().hex[:8]}"],
                client=client)
        except redis.RedisError:
            return self.fallback.hit(key, limit, window, now)
        if allowed:
            return True, int(remaining), 0
        return False, 0, max(1, int(float(oldest) + window - now) + 1)

    def reset(self) -> None:
        self.fallback.reset()
        from app.core.redis import get_redis

        client = get_redis()
        if client is not None:
            for k in client.scan_iter("erp:ratelimit:*"):
                client.delete(k)


limiter = RedisRateLimiter(RateLimiter())
