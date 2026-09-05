"""Strategy analytics domain contracts (Phase 9: Recovery Strategy Learning
-- scoped).

Phase 9's master plan (``docs/master-loop-engineering-prompt.md``, Section
28) calls for a "historical strategy dataset," "strategy analytics," an
"ML recovery model," "recovery probability," and "strategy optimization".
This module implements only the first two.

The remaining three are deliberately NOT implemented here, for the same
reason Phase 8 did not compute incremental/causal recovered revenue: this
system has no real-world payment-failure/outcome data at any volume that
could train or validate a genuine predictive model (KI-007 -- the only
"evaluation" data in this repository is synthetic and generator-labeled,
scored 1.0 by construction against the mock provider it was designed to
match). Presenting a probability score, an "optimized" strategy ranking,
or a trained model computed from a handful of demo cases would either be
statistically meaningless or would misrepresent synthetic-data
correlation as a real predictive signal -- exactly what ADR-006 already
forbids at the policy layer ("model confidence is not a deterministic
policy threshold") and KI-007 already forbids at the evaluation layer
("must never be read as evidence of ... production conversion rates").
This module extends that same discipline to strategy learning: every
number here is an observed, disclosed frequency over a stated sample
size, never a prediction.

Reuses ``app.outcome.schema.ObservedOutcome`` -- no new outcome taxonomy.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.outcome.schema import ObservedOutcome

#: A case counts toward a rate's sample size only once it has a
#: conclusive Phase 7 outcome (recovered or not_recovered) -- `unresolved`
#: cases are excluded from the rate itself (there is nothing to count yet)
#: but are still visible in the raw dataset and in each stat's
#: `unresolved_count`.
LOW_SAMPLE_THRESHOLD = 5

ML_MODEL_LIMITATION = (
    "No ML recovery-probability model or strategy optimizer is implemented. "
    "This system has no real-world payment-failure/outcome data at a volume "
    "that could train or validate one (KI-007) -- every number below is an "
    "observed, disclosed frequency over the stated sample size, never a "
    "prediction, and must not be read as one."
)


class StrategyDatasetRow(BaseModel):
    """One historical case's strategy/disposition/outcome -- the
    "historical strategy dataset". Raw, auditable, one row per case that
    has reached at least a policy-approved decision (the same "eligible"
    set ``app.measurement.service._eligible_cases`` uses).
    """

    model_config = {"extra": "forbid"}

    case_id: UUID
    strategy: str
    disposition: str
    outcome: ObservedOutcome | None
    currency: str


class StrategyStat(BaseModel):
    """Empirical recovery frequency for one strategy or disposition value.

    ``empirical_recovery_rate`` is ``recovered_count / observed_count`` --
    a case-count ratio, computed the same currency-safe way as
    ``app.measurement.schema.RevenueReport.observed_recovery_rate``.
    ``None`` when ``observed_count`` is 0 (never divides by zero, never
    fabricates a rate from no evidence).
    """

    model_config = {"extra": "forbid"}

    key: str
    total_case_count: int
    observed_count: int
    recovered_count: int
    not_recovered_count: int
    unresolved_count: int
    empirical_recovery_rate: float | None
    low_sample: bool


class StrategyAnalyticsReport(BaseModel):
    """The full Phase 9 strategy-analytics report."""

    model_config = {"extra": "forbid"}

    dataset_size: int
    low_sample_threshold: int = LOW_SAMPLE_THRESHOLD
    by_strategy: list[StrategyStat]
    by_disposition: list[StrategyStat]
    ml_model_status: Literal["not_implemented"] = "not_implemented"
    ml_model_limitation: str = ML_MODEL_LIMITATION
