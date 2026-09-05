"""Baseline HTTP security response headers (Phase 15: Security &
Fintech Hardening).

A fixed, deliberately small set -- this is a JSON API with no browser
templates rendered server-side, so a full Content-Security-Policy has
nothing meaningful to restrict here beyond what these headers already
cover:

* ``X-Content-Type-Options: nosniff`` -- stops a browser from
  MIME-sniffing a JSON response body as something executable.
* ``X-Frame-Options: DENY`` -- this API is never meant to be framed.
* ``Referrer-Policy: no-referrer`` -- API responses should never leak
  the calling URL (which may embed identifiers) via a Referer header
  on any subsequent navigation.
* ``Strict-Transport-Security`` -- production only. Meaningless (and
  actively wrong -- it would break local HTTP development) outside a
  real TLS deployment, gated the same way ``docs_url`` already is via
  ``Settings.is_production``.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings

_BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

_HSTS_HEADER = {"Strict-Transport-Security": "max-age=63072000; includeSubDomains"}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for name, value in _BASE_HEADERS.items():
            response.headers[name] = value
        if get_settings().is_production:
            for name, value in _HSTS_HEADER.items():
                response.headers[name] = value
        return response
