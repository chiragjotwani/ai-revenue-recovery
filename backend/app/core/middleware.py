"""Request-scoped correlation and operational HTTP metrics (Phase 14).

``RequestContextMiddleware`` assigns/propagates a ``request_id`` (from
an inbound ``X-Request-Id`` header, or a fresh one), binds it into
``app.core.logging.request_id_var`` for the duration of the request (so
every log line emitted while handling it -- including this
middleware's own start/end lines and every domain-service log point
downstream -- carries the same id), echoes it back on the response, and
records the operational metrics Phase 14 asks for: request count,
latency, and error rate by route and status code (``app.core.metrics``).

The ``request_id`` contextvar is reset only after this middleware's own
completion log line is emitted -- resetting it earlier (e.g. in a bare
``finally`` wrapping just ``call_next``) would make that log line lose
its own correlation id.
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import request_id_var
from app.core.metrics import HTTP_REQUEST_DURATION_SECONDS, HTTP_REQUESTS_TOTAL

REQUEST_ID_HEADER = "X-Request-Id"

logger = logging.getLogger("app.http")


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else request.url.path


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming if incoming else str(uuid.uuid4())
        token = request_id_var.set(request_id)

        start = time.monotonic()
        try:
            try:
                response = await call_next(request)
            except Exception:
                duration_ms = round((time.monotonic() - start) * 1000, 1)
                route_template = _route_template(request)
                HTTP_REQUESTS_TOTAL.labels(
                    method=request.method, route=route_template, status="500"
                ).inc()
                logger.exception(
                    "request failed",
                    extra={
                        "method": request.method,
                        "route": route_template,
                        "duration_ms": duration_ms,
                    },
                )
                raise

            duration = time.monotonic() - start
            route_template = _route_template(request)
            HTTP_REQUEST_DURATION_SECONDS.labels(
                method=request.method, route=route_template
            ).observe(duration)
            HTTP_REQUESTS_TOTAL.labels(
                method=request.method, route=route_template, status=str(response.status_code)
            ).inc()
            logger.info(
                "request completed",
                extra={
                    "method": request.method,
                    "route": route_template,
                    "status": response.status_code,
                    "duration_ms": round(duration * 1000, 1),
                },
            )
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            request_id_var.reset(token)
