"""Redis-backed fixed-window rate limiting (Phase 15: Security &
Fintech Hardening).

Keyed by the caller's API key when present (``X-API-Key`` header,
matching ``app.core.auth``'s own header name -- deliberately not
re-validated here, an invalid key is still a stable rate-limit bucket
key, and auth itself rejects it with 401 downstream) or by client IP
when absent, so an unauthenticated caller cannot dodge the limit simply
by omitting a key.

Fixed-window rather than a sliding/token-bucket algorithm: one
``INCR``+``EXPIRE`` round trip per request, no Lua script, no extra
dependency beyond the ``redis`` client this backend already uses
(Phase 0). A fixed window can allow a short burst at the boundary
between two windows -- an accepted, documented trade-off for this
system's actual threat model (protecting against runaway/misbehaving
callers and brute-force key guessing, not a precision traffic-shaping
requirement).

Redis being unreachable fails OPEN, not closed: this mirrors
ADR-007's own readiness principle (an infrastructure dependency that
is not the system of record must never block request-serving) --
logged as a warning, never silently swallowed.
"""

from __future__ import annotations

import logging
import time

from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.auth import API_KEY_HEADER_NAME
from app.core.config import get_settings

logger = logging.getLogger("app.rate_limit")

_WINDOW_SECONDS = 60


def _client_identity(request: Request) -> str:
    api_key = request.headers.get(API_KEY_HEADER_NAME)
    if api_key:
        return f"key:{api_key}"
    client = request.client
    return f"ip:{client.host}" if client else "ip:unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Skips ``/health``, ``/health/ready``, and ``/metrics`` --
    infrastructure probes/scrapers must never be rate-limited against
    the same budget as API callers, or a busy scrape window could make
    the platform report itself unhealthy.
    """

    _EXEMPT_PATHS = frozenset({"/health", "/health/ready", "/metrics"})

    def __init__(self, app: object, redis_client: Redis | None = None) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._redis = redis_client

    def _get_redis(self) -> Redis:
        if self._redis is None:
            self._redis = Redis.from_url(get_settings().redis_url)
        return self._redis

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in self._EXEMPT_PATHS:
            return await call_next(request)

        limit = get_settings().rate_limit_requests_per_minute
        identity = _client_identity(request)
        window = int(time.time()) // _WINDOW_SECONDS
        bucket_key = f"arr:ratelimit:{identity}:{window}"

        try:
            redis_client = self._get_redis()
            count = await redis_client.incr(bucket_key)
            if count == 1:
                await redis_client.expire(bucket_key, _WINDOW_SECONDS)
        except Exception:
            logger.warning("rate limiter unavailable, failing open", exc_info=True)
            return await call_next(request)

        if count > limit:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": str(_WINDOW_SECONDS)},
            )
        return await call_next(request)
