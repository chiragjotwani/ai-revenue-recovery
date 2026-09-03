"""Structured logging and request/domain correlation (Phase 14:
Production Observability).

Two correlation dimensions, deliberately kept distinct rather than
collapsed into one:

* ``request_id`` -- one per inbound HTTP call (``RequestContextMiddleware``
  in ``app.core.middleware``). Traces a single API request through the
  logs and back to the client via the ``X-Request-Id`` response header.
* ``correlation_id`` on ``app.events.schema.DomainEvent`` (Phase 12) --
  scoped to a recovery case (defaults to the case's own id), not to any
  one HTTP call, because a case's lifecycle (event -> case -> AI ->
  policy -> action -> outcome) spans many separate requests over time.
  Grepping logs/events for one ``case_id`` is the actual cross-request
  trace Phase 14 asks for; a single request-scoped id could not do that.

Every log record emitted through the root logger is rendered as one JSON
object (`_JSONFormatter`) carrying: timestamp, level, logger name,
message, the current ``request_id`` (if inside a request), and any
extra fields the caller passed via ``logging.Logger.info(..., extra=...)``
(e.g. ``case_id``, ``action_id``, ``event_id``, ``model_name``,
``model_version``, ``prompt_version``, ``schema_version``,
``latency_ms`` -- exactly the audit fields Section 51 / Phase 14 ask
for).

Deliberately never logs: customer email, payment external_reference, AI
reasoning free text, or a raw request/response body -- every field above
is a bounded identifier or a numeric/enum audit fact, the same
"identifiers and audit facts only, never PII or free text" boundary
``app.events.handlers.EventAuditProjector`` already established for the
Phase 12 event log.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

#: Standard ``logging.LogRecord`` attributes -- anything else on a record
#: was passed via ``extra=`` and belongs in the JSON output as a field.
_STANDARD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and key != "message":
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger. Idempotent -- safe
    to call more than once (replaces handlers rather than accumulating
    them), which matters under uvicorn's reload/multi-worker startup.
    """
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JSONFormatter())
    root.addHandler(handler)
