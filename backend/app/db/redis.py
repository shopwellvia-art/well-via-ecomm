"""Process-wide shared Redis client.

Every service used to call `redis.Redis.from_url(...)` in its own __init__, and
each call builds a brand-new ConnectionPool. A single request instantiates
SettingsService, SessionService, CartService, RateLimiter and more, so under
load the backend churned many short-lived TCP connections to Redis per request
across all workers.

redis-py's client is thread-safe and owns an internal connection pool, so the
whole process can share ONE client. Services still accept an injected client
(tests pass a fake); they just default to this shared instance instead of
opening a fresh pool. All call sites use decode_responses=True, so the shared
client does too.
"""
from __future__ import annotations

import redis

from app.core.config import settings

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        # Bounded socket timeouts so a wedged Redis fails fast (raising
        # RedisError, which every call site already handles) instead of hanging
        # a request thread or the /ready probe indefinitely.
        _client = redis.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
    return _client
