"""Revenue measurement domain contracts (Phase 8: Recovered Revenue
Measurement).

Reuses ``app.outcome.schema.ObservedOutcome`` as the measurement status
vocabulary -- Phase 8 introduces no new recovered/not-recovered/unresolved
taxonomy.

Hard distinction this module exists to enforce (see the Phase 8
architecture note this session's owner wrote into the prompt): an
OBSERVED recovered amount ("this case's originally at-risk payment has a
later successful payment as evidence") is never the same claim as an
INCREMENTAL/causally-attributed recovered amount ("this amount would not
have happened without the intervention"). This repository has no
randomized control group, no historical untreated cohort, and no other
counterfactual design -- so it cannot compute the second number, and must
not pretend to. Every type below is named and documented to make that
distinction impossible to blur: nothing here is called "incremental",
"impact", "uplift", or "caused by AI", because none of those are
measurable from this data.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

#: Fixed, explanatory measurement basis carried on every report. Not a
#: config value or a threshold -- a documentation string, so a consumer
#: (UI, API client, judge) sees the epistemic status of every number
#: without having to already know this project's caveats.
MEASUREMENT_BASIS: Literal["observed_evidence"] = "observed_evidence"

#: Why an incremental/causal estimate is not attempted -- read by the API
#: response and the frontend so this limitation is visible wherever the
#: report is, not only in documentation a reader might not open.
COUNTERFACTUAL_LIMITATION = (
    "No randomized control group, historical untreated cohort, or other "
    "counterfactual design exists in this system. Every recovered amount "
    "below is an OBSERVED fact (a later successful payment exists as "
    "evidence for this case) -- it is not, and must not be read as, an "
    "estimate of revenue that would not have happened without the "
    "intervention."
)


class CurrencyAmount(BaseModel):
    """One currency's total. Never combined with another currency's total
    -- KI-006 remains unresolved (no FX/cross-currency conversion source
    exists), so every monetary aggregate in this module is a list of
    these, one per currency, never a single cross-currency sum.
    """

    model_config = {"extra": "forbid"}

    currency: str
    amount: Decimal
    case_count: int


class BreakdownEntry(BaseModel):
    """One (dimension value, currency) slice of observed recovered value
    -- e.g. ``key="retry", currency="INR"``. Same currency-safety
    discipline as :class:`CurrencyAmount`.
    """

    model_config = {"extra": "forbid"}

    key: str
    currency: str
    amount: Decimal
    case_count: int


class RevenueReport(BaseModel):
    """The full Phase 8 measurement report. Every monetary field is a
    per-currency list -- there is no single "total recovered revenue"
    number anywhere in this type, deliberately.
    """

    model_config = {"extra": "forbid"}

    measurement_basis: Literal["observed_evidence"] = MEASUREMENT_BASIS
    counterfactual_available: Literal[False] = False
    counterfactual_limitation: str = COUNTERFACTUAL_LIMITATION

    eligible_case_count: int
    eligible_at_risk: list[CurrencyAmount]

    observed_recovered: list[CurrencyAmount]
    observed_not_recovered: list[CurrencyAmount]
    unresolved: list[CurrencyAmount]

    recovered_case_count: int
    #: recovered_case_count / eligible_case_count, a case-count ratio --
    #: not a monetary figure, so it needs no currency and is safe to
    #: compute even across a multi-currency case set. 0.0 when there are
    #: no eligible cases (never divides by zero).
    observed_recovery_rate: float

    recovered_by_strategy: list[BreakdownEntry]
    recovered_by_disposition: list[BreakdownEntry]
