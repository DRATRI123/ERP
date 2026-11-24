"""Optional Redis connection (``ERP_REDIS_URL``)."""

from __future__ import annotations

from functools import lru_cache

import redis

from app.core.config import get_settings


@lru_cache
def get_redis() -> redis.Redis | None:
    url = get_settings().redis_url
    if not url:
        return None
    return redis.Redis.from_url(url, socket_timeout=5, socket_connect_timeout=5, health_check_interval=30)
