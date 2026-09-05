"""Analytics data platform domain contracts (Phase 13: Analytics Data
Platform -- scoped).

Phase 13's master plan calls for an analytical model covering "revenue at
risk, recovered revenue, natural recovery, incremental recovery, recovery
attempts, strategy performance, failure categories, customer segments,
model performance, and experiment performance."

Everything except "incremental recovery" and "experiment performance" is
implemented. Those two require a randomized control group or another
counterfactual design to compute honestly -- this system has neither
(Phase 8's ``COUNTERFACTUAL_LIMITATION``, extended by Phase 9's
``ML_MODEL_LIMITATION``) -- so, per explicit owner decision (asked
mid-session via AskUserQuestion, given the same class of tension Phase
9/10/11 each hit and were scoped down for), they are NOT implemented
here. This module extends the exact same discipline: every number below
is OBSERVED evidence, never a causal or predictive estimate.

"Natural recovery" (a payment recovered with no recorded intervention)
is NOT implemented, for a reason discovered while building this phase,
not merely a stylistic choice: Phase 7's ``observe_outcome`` -- and
therefore ``get_outcome_for_case``, which
``app.measurement.service.get_revenue_report`` (Phase 8) and this
module's own ETL both use for ``outcome_status`` -- only ever produces
an observation for a case with an *executed* ``RecoveryAction``
(``NoExecutedActionError`` otherwise). A case this system never
scheduled an action for can therefore never be classified ``recovered``
under Phase 8's own frozen semantics, no matter what the raw payment
evidence shows -- it is definitionally ``unresolved`` here. Computing
"natural recovery" honestly would require classifying outcome from raw
payment evidence for action-less cases too (``classify_outcome`` can do
this), which would silently give those cases a DIFFERENT, broader
outcome definition than Phase 8's own report uses for the identical
case -- exactly the "redefine the financial semantics already
established in Phase 8" this phase must not do. So this module keeps
Phase 8's outcome definition byte-for-byte identical instead, and simply
does not offer a metric that definition cannot support.

Reuses ``app.outcome.schema.ObservedOutcome`` -- no new outcome taxonomy.
Reuses the exact per-currency, never-summed-across-currencies discipline
Phase 8 established (KI-006 remains open) for every monetary field.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

EXPERIMENT_LIMITATION = (
    "No experiment/incremental-recovery analytics are computed. This system "
    "has no randomized control group, historical untreated cohort, or other "
    "counterfactual design (see app.measurement.schema.COUNTERFACTUAL_LIMITATION "
    "and app.analytics.schema.ML_MODEL_LIMITATION) -- there is nothing to "
    "attribute an incremental effect to, so none is fabricated."
)

NATURAL_RECOVERY_LIMITATION = (
    "No 'natural recovery' metric is computed. Phase 7's outcome "
    "observation (which Phase 8's own revenue report also depends on) "
    "only ever classifies a case 'recovered' when it has an executed "
    "RecoveryAction -- a case this system never actioned is always "
    "'unresolved' here, regardless of what the raw payment evidence "
    "shows. Classifying such cases from raw evidence anyway would give "
    "them a different outcome definition than Phase 8's own report uses "
    "for the same case, which would redefine Phase 8's financial "
    "semantics -- not permitted. See app.warehouse.schema's module "
    "docstring for the full explanation."
)


class CurrencyAmount(BaseModel):
    """One currency's total -- never combined with another currency's
    total (KI-006). Identical shape to
    ``app.measurement.schema.CurrencyAmount``, duplicated rather than
    imported to keep this module's contract independent of Phase 8's.
    """

    model_config = {"extra": "forbid"}

    currency: str
    amount: Decimal
    case_count: int


class KeyedAmount(BaseModel):
    """One (dimension value, currency) slice -- e.g.
    ``key="retry", currency="INR"``.
    """

    model_config = {"extra": "forbid"}

    key: str
    currency: str
    amount: Decimal
    case_count: int


class RecoveryRateStat(BaseModel):
    """Empirical recovery frequency for one dimension value (a strategy,
    a failure category, a customer segment, ...). Same shape and same
    never-divide-by-zero discipline as
    ``app.analytics.schema.StrategyStat``.
    """

    model_config = {"extra": "forbid"}

    key: str
    total_case_count: int
    observed_count: int
    recovered_count: int
    not_recovered_count: int
    unresolved_count: int
    empirical_recovery_rate: float | None


class ModelPerformanceStat(BaseModel):
    """Observed diagnosis stats for one (model_name, model_version) pair.

    ``avg_confidence`` is a descriptive statistic over the model's own
    self-reported, uncalibrated confidence field -- it is presented for
    monitoring only and must never be read as, or used as, a policy
    threshold (ADR-006 already forbids this at the decision layer; this
    is the same discipline applied to reporting).
    """

    model_config = {"extra": "forbid"}

    model_name: str
    model_version: str
    diagnosis_count: int
    avg_confidence: float | None
    avg_latency_ms: float | None
    router_escalated_count: int
    router_escalation_rate: float


class AnalyticsWarehouseReport(BaseModel):
    """The full Phase 13 analytics warehouse report. Computed from the
    ``case_analytics_facts`` materialization (``app.warehouse.etl``), not
    live from the operational tables -- the whole point of separating the
    analytical workload from OLTP (see ``app.warehouse`` module
    docstring).
    """

    model_config = {"extra": "forbid"}

    fact_count: int
    warehouse_built_at: datetime | None

    revenue_at_risk: list[CurrencyAmount]
    observed_recovered: list[CurrencyAmount]

    natural_recovery_status: Literal["not_measurable"] = "not_measurable"
    natural_recovery_limitation: str = NATURAL_RECOVERY_LIMITATION

    total_recovery_attempts: int

    by_strategy: list[RecoveryRateStat]
    by_failure_reason: list[RecoveryRateStat]
    by_customer_segment: list[RecoveryRateStat]

    model_performance: list[ModelPerformanceStat]

    experiment_status: Literal["not_implemented"] = "not_implemented"
    experiment_limitation: str = EXPERIMENT_LIMITATION


class CaseAnalyticsFactRow(BaseModel):
    """One denormalized fact row -- the raw materialization, exposed
    read-only for audit/debugging (mirrors
    ``app.analytics.schema.StrategyDatasetRow``'s raw-dataset role).
    """

    model_config = {"extra": "forbid"}

    case_id: UUID
    customer_id: UUID
    currency: str
    amount: Decimal
    eligible: bool
    outcome_status: str
    has_action: bool
    action_type: str | None
    attempt_count: int
    disposition: str | None
    model_name: str | None
    model_version: str | None
    confidence: Decimal | None
    router_escalated: bool | None
    failure_reason: str | None
    customer_case_segment: str
    computed_at: datetime
