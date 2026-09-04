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


#: Read by the API response and frontend, mirroring COUNTERFACTUAL_LIMITATION's
#: role above -- this is what makes app.measurement.baseline's comparison
#: an "observed vs. simulated-under-the-same-model" comparison, never a
#: causal/RCT claim, wherever the report surfaces.
#:
#: Two fairness fixes from the 2026-09-04 red-team audit are baked into
#: this text because they change what the numbers below actually mean,
#: not just how they are computed:
#:
#: 1. Cases resolved via `no_action` (app.decision.policy's already-paid
#:    short-circuit -- the customer's payment already succeeded through
#:    some independent, unrelated event before any decision ran) are
#:    EXCLUDED from both the AI-gated and baseline populations entirely.
#:    Neither policy took, or would have taken, any action for these --
#:    including them would credit "AI" for a recovery neither policy
#:    caused, which is exactly the kind of inflated number this audit
#:    exists to catch.
#: 2. The baseline gets the SAME bounded retry budget (RETRY_CAP attempts)
#:    the real executor gives itself, not a single attempt -- otherwise a
#:    temporary-failure-then-success profile would be scored as a
#:    baseline failure purely because of attempt count, not decision
#:    quality.
BASELINE_METHODOLOGY = (
    "Baseline = 'blind retry': attempt a retry (up to the same RETRY_CAP "
    "attempt budget the real executor uses) for every eligible case that "
    "genuinely required a recovery decision, regardless of diagnosis, "
    "disposition, fraud signal, or evidence sufficiency -- the simplest "
    "pre-AI approach a naive recovery system might use. Cases resolved via "
    "`no_action` (the customer's payment already succeeded independently, "
    "before any decision ran) are excluded from BOTH populations: neither "
    "policy took or would take any action for them, so including them would "
    "credit either policy with a recovery it did not cause. Every remaining "
    "eligible case's outcome is computed by calling the SAME deterministic "
    "simulated provider (app.decision.providers) the real AI-gated pipeline "
    "uses, as a pure, side-effect-free function of the case's own "
    "failure_reason -- never a second execution, never persisted. "
    "'AI-gated' recovered value is the real, already-OBSERVED Phase 7/8 "
    "outcome for this same filtered population. This is a same-population, "
    "same-environment-model, same-attempt-budget comparison of two decision "
    "policies, NOT a randomized control/treatment experiment and NOT a "
    "causal or incremental-lift estimate -- no counterfactual population "
    "exists (see COUNTERFACTUAL_LIMITATION, unchanged)."
)


class BaselineComparisonReport(BaseModel):
    """Baseline ('blind retry', simulated) vs. AI-gated (real, observed)
    recovery, over the SAME eligible case population, EXCLUDING cases
    resolved before any decision ran (see ``BASELINE_METHODOLOGY``).
    Every field here is named to keep the same "observed vs.
    simulated-under-one-model" distinction ``RevenueReport`` keeps for
    "observed vs. causal": nothing here is called "lift", "improvement",
    or "impact".
    """

    model_config = {"extra": "forbid"}

    methodology: str = BASELINE_METHODOLOGY
    counterfactual_available: Literal[False] = False

    #: Total cases with a DecisionResult, before excluding already-resolved
    #: (`no_action`) ones -- for comparison against
    #: ``RevenueReport.eligible_case_count``, which this intentionally does
    #: NOT match (see ``already_resolved_excluded_count``).
    total_eligible_case_count: int
    #: Cases excluded because they resolved via `no_action` before any
    #: decision-driven action ran -- see ``BASELINE_METHODOLOGY``.
    already_resolved_excluded_count: int
    #: total_eligible_case_count - already_resolved_excluded_count -- the
    #: actual denominator for both rates below.
    compared_case_count: int

    ai_gated_observed_recovered: list[CurrencyAmount]
    baseline_simulated_recovered: list[CurrencyAmount]

    ai_gated_recovery_rate: float
    baseline_simulated_recovery_rate: float

    #: Cases where the AI-gated policy escalated to manual_review (fraud
    #: suspected, sparse evidence, or conflicting signals) -- exactly the
    #: cases a blind-retry baseline would have retried anyway, with no
    #: safety check at all. A behavioral safety count, not a revenue
    #: figure -- never inflated into a recovery-amount claim.
    cases_where_ai_gate_avoided_a_blind_retry: int
