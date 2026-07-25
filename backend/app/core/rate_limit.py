"""Redis-backed sliding-bucket rate limiter.

We use **fixed windows** keyed on `(scope, identifier, route, window_index)`.
Compared to a true sliding window this is slightly less smooth at the boundary
between windows, but the implementation is two Redis commands per request and
the burst it allows (≤2× the limit at the cusp) is acceptable for auth routes.

The key TTL matches the window so old buckets vacate themselves without a
cleanup pass. Redis being temporarily unavailable degrades to "allow" rather
than locking out every customer — a tradeoff we accept for an auth surface.
"""
from __future__ import annotations

import ipaddress
import logging
import time
from functools import lru_cache
from typing import Optional

import redis

from app.core.config import settings
from app.db.redis import get_redis
from app.core.exceptions import TooManyRequestsError

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _trusted_networks() -> tuple[ipaddress._BaseNetwork, ...]:
    """Parse settings.TRUSTED_PROXIES into networks once.

    Each entry may be a bare IP ("127.0.0.1") or CIDR ("172.16.0.0/12"); a bare
    IP becomes a /32 (or /128) host network. Behind Docker the nginx container's
    bridge IP is dynamic, so the deployment trusts the bridge subnet as a CIDR —
    without this every request collapses to the proxy IP and all per-IP limits
    share one global bucket. Invalid entries are skipped with a warning.
    """
    nets: list[ipaddress._BaseNetwork] = []
    for entry in settings.TRUSTED_PROXIES:
        try:
            nets.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.warning("ignoring invalid TRUSTED_PROXIES entry: %r", entry)
    return tuple(nets)


def _is_trusted_proxy(peer: str) -> bool:
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(addr in net for net in _trusted_networks())


class RateLimiter:
    """Singleton-ish: a fresh Redis client per instance, but the calling site
    keeps one instance around per request to share the connection."""

    def __init__(self, client: Optional[redis.Redis] = None):
        self.redis = client or get_redis()

    def enforce(
        self,
        *,
        scope: str,
        identifier: str,
        limit: int,
        window_sec: int,
    ) -> None:
        """Increment the bucket and raise `TooManyRequestsError` when the limit
        is exceeded. `scope` is a short label (e.g. "login.ip"), `identifier`
        is the variable part (an IP, an email, a user id)."""
        if not settings.RATE_LIMIT_ENABLED or limit <= 0:
            return

        # Fixed window — keys live for the window length and then vanish.
        bucket = int(time.time() // window_sec)
        key = f"rl:{scope}:{identifier}:{bucket}"
        try:
            count = self.redis.incr(key)
            if count == 1:
                # First hit of the bucket — give it an expiry so the key
                # doesn't linger forever if traffic stops.
                self.redis.expire(key, window_sec)
        except redis.RedisError as exc:
            logger.warning("rate-limit redis call failed for %s: %s", key, exc)
            return

        if count > limit:
            ttl = self.redis.ttl(key)
            retry = ttl if (isinstance(ttl, int) and ttl > 0) else window_sec
            raise TooManyRequestsError(
                "Too many requests. Try again shortly.",
                retry_after=retry,
            )

    # ---- Helpers used by AuthService for account lockout ----

    def increment_failure(self, identifier: str, *, window_sec: int) -> int:
        """Bump the consecutive-failed-login counter. Returns the new count."""
        if not settings.RATE_LIMIT_ENABLED:
            return 0
        key = f"failed:login:{identifier}"
        try:
            count = self.redis.incr(key)
            if count == 1:
                self.redis.expire(key, window_sec)
            return int(count)
        except redis.RedisError as exc:
            logger.warning("failure-counter redis call failed: %s", exc)
            return 0

    def clear_failures(self, identifier: str) -> None:
        try:
            self.redis.delete(f"failed:login:{identifier}")
            self.redis.delete(f"lockout:{identifier}")
        except redis.RedisError as exc:
            logger.warning("clear-failures redis call failed: %s", exc)

    def set_lockout(self, identifier: str, *, minutes: int) -> None:
        if minutes <= 0:
            return
        try:
            self.redis.setex(f"lockout:{identifier}", minutes * 60, "1")
        except redis.RedisError as exc:
            logger.warning("set-lockout redis call failed: %s", exc)

    def check_lockout(self, identifier: str) -> None:
        """Raise 429 if the identifier is currently locked out."""
        if not settings.RATE_LIMIT_ENABLED:
            return
        key = f"lockout:{identifier}"
        try:
            if self.redis.exists(key):
                ttl = self.redis.ttl(key)
                retry = ttl if (isinstance(ttl, int) and ttl > 0) else 60
                raise TooManyRequestsError(
                    "Account temporarily locked due to repeated failed logins. "
                    "Try again later or reset your password.",
                    retry_after=retry,
                )
        except redis.RedisError as exc:
            logger.warning("lockout check redis call failed: %s", exc)


def get_client_ip(request) -> str:
    """Extract the originating client IP.

    Trusts `X-Forwarded-For` only when the connecting peer is in
    `settings.TRUSTED_PROXIES` (nginx in this stack), matched by IP or CIDR.
    Otherwise falls back to the raw connection peer. This blocks spoofed headers
    from arbitrary clients while still giving us the real IP behind the reverse
    proxy.
    """
    peer = (request.client.host if request.client else "") or "unknown"
    if _is_trusted_proxy(peer):
        xff = request.headers.get("x-forwarded-for")
        if xff:
            # First entry is the original client; the rest are intermediate
            # proxies appended by each hop.
            return xff.split(",")[0].strip() or peer
    return peer
