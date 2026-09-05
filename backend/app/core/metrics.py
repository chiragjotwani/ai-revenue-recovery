"""Operational and business metrics (Phase 14: Production Observability).

Exposed at ``GET /metrics`` in the standard Prometheus text format
(``app.api.observability``). Two kinds of series, deliberately kept in
separate registries-by-convention (same names, different section of this
module) rather than blurred together:

* **Operational** -- request count/latency/error rate by route+status
  (``app.core.middleware.RequestContextMiddleware`` records these on
  every request), consumer lag/dead-letter counts (Phase 12's
  ``scripts/event_consumer.py``).
* **Business** -- revenue at risk / observed recovered revenue / recovery
  attempts, refreshed from ``app.warehouse.service`` on each ``/metrics``
  scrape (a Gauge, not a Counter -- it reports the warehouse's current
  state, not an event count). Every business number here reuses Phase 8/
  13's own OBSERVED-evidence definitions unchanged -- this module
  computes nothing new, it only re-exposes those reports as gauges.
  KI-006 remains in force: revenue gauges are labeled by currency, never
  summed across currencies.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# --- operational: HTTP ------------------------------------------------------

HTTP_REQUESTS_TOTAL = Counter(
    "arr_http_requests_total",
    "Total HTTP requests handled, by method/route template/status code.",
    labelnames=("method", "route", "status"),
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "arr_http_request_duration_seconds",
    "HTTP request latency in seconds, by method/route template.",
    labelnames=("method", "route"),
)

# --- operational: Phase 12 event pipeline -----------------------------------

EVENTS_CONSUMED_TOTAL = Counter(
    "arr_events_consumed_total",
    "Domain events consumed, by event_type and outcome "
    "(handled/duplicate/dead_lettered -- app.events.consumer.EventOutcome).",
    labelnames=("event_type", "outcome"),
)

EVENTS_RELAYED_TOTAL = Counter(
    "arr_events_relayed_total",
    "Domain events published from the outbox to Kafka, by event_type.",
    labelnames=("event_type",),
)

# --- business: revenue (Phase 8/13 OBSERVED semantics, unchanged) ----------

REVENUE_AT_RISK = Gauge(
    "arr_revenue_at_risk",
    "Current eligible at-risk revenue, by currency (never summed across "
    "currencies -- KI-006). Same definition as "
    "app.measurement.schema.RevenueReport.eligible_at_risk.",
    labelnames=("currency",),
)

OBSERVED_RECOVERED_REVENUE = Gauge(
    "arr_observed_recovered_revenue",
    "Observed recovered revenue, by currency. Same OBSERVED-evidence "
    "definition as app.measurement.schema.RevenueReport.observed_recovered "
    "-- never a causal/incremental estimate (Phase 8's "
    "COUNTERFACTUAL_LIMITATION).",
    labelnames=("currency",),
)

RECOVERY_ATTEMPTS_TOTAL_GAUGE = Gauge(
    "arr_recovery_attempts_total",
    "Total recorded recovery-action execution attempts across all cases "
    "(app.warehouse.schema.AnalyticsWarehouseReport.total_recovery_attempts).",
)
