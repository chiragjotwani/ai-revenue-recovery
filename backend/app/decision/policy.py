"""Deterministic policy engine (Phase 5B).

``evaluate(...)`` is the sole authority for turning a Phase 4 diagnosis plus
deterministic context into a policy verdict. It is a **pure function**:
given the same ``PolicyInput`` it always returns the same ``PolicyOutcome``.
It never touches a database, network, clock, or random source -- the
caller supplies ``now`` explicitly so tests (and 5C) get fully
deterministic, repeatable behaviour.

Scope boundary (see the Phase 5 Architecture Revision): this module
classifies recoverability and decides whether/how a strategy is admissible.
It does not assign decision identity (case_id/diagnosis_id -- that is 5C's
job when it wraps a ``PolicyOutcome`` into the persisted
``app.decision.schema.DecisionResult``), does not persist anything, does
not call an API, and does not execute anything.

Hard invariant (AI Trust Boundary): this module never reads
``diagnosis.reasoning`` or any other free-text/natural-language field. Every
branch below is decided exclusively from typed fields on ``PolicyInput``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from app.ai.schema import DiagnosisDisposition, RecoveryStrategy
from app.decision.schema import DecisionRationaleEntry, DecisionStatus, Recoverability

# Existing, documented value (app/ai/context_builder.py's applicable_policies:
# "A payment may be retried at most 3 times."). Not invented here.
RETRY_CAP = 3

# Strategies for which recommended_delay_hours has timing meaning. A
# downgrade to MANUAL_REVIEW or a resolved NO_ACTION never gets a future
# schedule -- there is nothing to wait for.
_TIMED_STRATEGIES = frozenset(
    {
        RecoveryStrategy.RETRY,
        RecoveryStrategy.REQUEST_PAYMENT_METHOD_UPDATE,
        RecoveryStrategy.CONTACT_CUSTOMER,
    }
)

# Strategies Phase 4 may legitimately propose when disposition is
# CUSTOMER_ACTION_REQUIRED (app/ai/schema.py's RecoveryStrategy vocabulary).
_CUSTOMER_ACTION_COMPATIBLE_STRATEGIES = frozenset(
    {
        RecoveryStrategy.REQUEST_PAYMENT_METHOD_UPDATE,
        RecoveryStrategy.CONTACT_CUSTOMER,
    }
)


class PolicyInput(BaseModel):
    """Everything ``evaluate`` needs, and nothing it must not have.

    Deliberately excludes ``diagnosis.reasoning`` (free text) and any
    amount/currency field (KI-006 remains unresolved for cross-currency
    thresholds; Phase 5 introduces no monetary rule).
    """

    model_config = {"extra": "forbid"}

    disposition: DiagnosisDisposition
    candidate_strategy: RecoveryStrategy
    recommended_delay_hours: int | None = Field(default=None, ge=0, le=720)
    evidence_sufficiency: Literal["sufficient", "sparse"]
    signals_conflict: bool
    retry_count: int = Field(ge=0)
    already_paid: bool
    now: datetime


class PolicyOutcome(BaseModel):
    """The pure policy verdict for one ``PolicyInput``.

    Deliberately has no identity (case_id/diagnosis_id) and no
    ``decision_engine_version`` -- 5C combines this with both to build the
    persisted ``app.decision.schema.DecisionResult``. Keeping this type
    identity-free is what keeps ``evaluate`` a pure function of its typed
    inputs alone.
    """

    model_config = {"extra": "forbid"}

    recoverability: Recoverability
    candidate_strategy: RecoveryStrategy
    approved_strategy: RecoveryStrategy
    decision_status: DecisionStatus
    rationale: list[DecisionRationaleEntry]
    scheduled_not_before: datetime | None


def _classify_recoverability(policy_input: PolicyInput) -> Recoverability:
    """Pure function of (disposition, evidence_sufficiency, signals_conflict).

    Exhaustive over DiagnosisDisposition's 4 members combined with the two
    evidence signals. Deliberately NOT affected by ``already_paid`` or
    ``retry_count`` -- recoverability describes whether the diagnosed
    failure was, in principle, the kind of thing this system can act on;
    already-paid and retry-cap are separate, strategy-level facts about
    whether we *should* act now, not about what the diagnosis was.

    Precedence (most specific signal first):
    1. suspected_fraud always classifies as not_recoverable_automatically,
       even if evidence also happens to be sparse/conflicting -- a fraud
       flag is itself a strong, sufficient signal, not an absence of one.
    2. sparse evidence or conflicting signals -> insufficient_evidence,
       regardless of the remaining dispositions.
    3. retriable_transient -> likely_recoverable.
    4. customer_action_required -> conditionally_recoverable.
    5. unknown (with otherwise sufficient, non-conflicting evidence) ->
       insufficient_evidence (the model itself could not identify a cause).
    """
    if policy_input.disposition is DiagnosisDisposition.SUSPECTED_FRAUD:
        return Recoverability.NOT_RECOVERABLE_AUTOMATICALLY
    if policy_input.evidence_sufficiency == "sparse" or policy_input.signals_conflict:
        return Recoverability.INSUFFICIENT_EVIDENCE
    if policy_input.disposition is DiagnosisDisposition.RETRIABLE_TRANSIENT:
        return Recoverability.LIKELY_RECOVERABLE
    if policy_input.disposition is DiagnosisDisposition.CUSTOMER_ACTION_REQUIRED:
        return Recoverability.CONDITIONALLY_RECOVERABLE
    return Recoverability.INSUFFICIENT_EVIDENCE  # DiagnosisDisposition.UNKNOWN


def _scheduled_not_before(
    approved_strategy: RecoveryStrategy,
    recommended_delay_hours: int | None,
    now: datetime,
) -> datetime | None:
    if approved_strategy in _TIMED_STRATEGIES and recommended_delay_hours is not None:
        return now + timedelta(hours=recommended_delay_hours)
    return None


def _outcome(
    policy_input: PolicyInput,
    recoverability: Recoverability,
    approved_strategy: RecoveryStrategy,
    decision_status: DecisionStatus,
    rationale: list[DecisionRationaleEntry],
) -> PolicyOutcome:
    return PolicyOutcome(
        recoverability=recoverability,
        candidate_strategy=policy_input.candidate_strategy,
        approved_strategy=approved_strategy,
        decision_status=decision_status,
        rationale=rationale,
        scheduled_not_before=_scheduled_not_before(
            approved_strategy, policy_input.recommended_delay_hours, policy_input.now
        ),
    )


def evaluate(policy_input: PolicyInput) -> PolicyOutcome:
    """The deterministic policy pipeline. See module docstring for the
    purity contract. Rule order below is a deliberate safety ordering
    (most-overriding condition first), not the presentation order of the
    Phase 5B rule list: already-paid, then fraud, then evidence
    sufficiency, then retry-cap / strategy-specific admissibility, then a
    safe default. Evidence sufficiency is checked before retry-cap because
    a retry-cap decision is meaningless to make confidently on sparse or
    conflicting evidence -- rule 4's own requirement ("do not allow an AI
    recommendation to override insufficient evidence") only holds if
    evidence is checked before any strategy-specific rule is allowed to
    approve something.
    """
    recoverability = _classify_recoverability(policy_input)
    rationale: list[DecisionRationaleEntry] = []

    # Rule 1 -- already paid. Short-circuits everything else. This means
    # "no further recovery action is applicable", not "the original
    # payment was recovered" -- Phase 5 never marks anything recovered;
    # only Phase 7's observed-outcome link does that.
    if policy_input.already_paid:
        rationale.append(
            DecisionRationaleEntry(
                rule_id="already_paid", outcome="failed", reason_code="customer_already_paid"
            )
        )
        return _outcome(
            policy_input,
            recoverability,
            RecoveryStrategy.NO_ACTION,
            DecisionStatus.APPROVED,
            rationale,
        )
    rationale.append(DecisionRationaleEntry(rule_id="already_paid", outcome="passed"))

    # Rule 2 -- suspected fraud. Never auto-retried, regardless of the AI's
    # candidate strategy. Decided solely from the typed disposition.
    if policy_input.disposition is DiagnosisDisposition.SUSPECTED_FRAUD:
        rationale.append(
            DecisionRationaleEntry(
                rule_id="fraud_manual_review", outcome="failed", reason_code="suspected_fraud"
            )
        )
        return _outcome(
            policy_input,
            recoverability,
            RecoveryStrategy.MANUAL_REVIEW,
            DecisionStatus.ESCALATED,
            rationale,
        )
    rationale.append(DecisionRationaleEntry(rule_id="fraud_manual_review", outcome="passed"))

    # Rule 4 -- insufficient evidence. Checked before any strategy-specific
    # rule (see function docstring): sparse evidence or conflicting signals
    # always escalate, regardless of what the AI recommended.
    if policy_input.evidence_sufficiency == "sparse" or policy_input.signals_conflict:
        reason = (
            "sparse_evidence"
            if policy_input.evidence_sufficiency == "sparse"
            else "signals_conflict"
        )
        rationale.append(
            DecisionRationaleEntry(
                rule_id="insufficient_evidence", outcome="failed", reason_code=reason
            )
        )
        return _outcome(
            policy_input,
            recoverability,
            RecoveryStrategy.MANUAL_REVIEW,
            DecisionStatus.ESCALATED,
            rationale,
        )
    rationale.append(DecisionRationaleEntry(rule_id="insufficient_evidence", outcome="passed"))

    # Rule 3 -- retry cap. Only applicable when the candidate is `retry`.
    if policy_input.candidate_strategy is RecoveryStrategy.RETRY:
        if policy_input.disposition is not DiagnosisDisposition.RETRIABLE_TRANSIENT:
            # retry proposed for a disposition retry doesn't apply to --
            # an unsafe/inconsistent combination; fall back to the safest
            # outcome rather than trusting an ill-fitting recommendation.
            rationale.append(
                DecisionRationaleEntry(
                    rule_id="retry_disposition_mismatch",
                    outcome="failed",
                    reason_code="retry_not_applicable_for_disposition",
                )
            )
            return _outcome(
                policy_input,
                recoverability,
                RecoveryStrategy.MANUAL_REVIEW,
                DecisionStatus.ESCALATED,
                rationale,
            )
        if policy_input.retry_count >= RETRY_CAP:
            rationale.append(
                DecisionRationaleEntry(
                    rule_id="retry_cap", outcome="failed", reason_code="retry_cap_exceeded"
                )
            )
            # A retry-cap downgrade is still an APPROVED decision (the
            # system approved routing to manual review), never a rejection.
            return _outcome(
                policy_input,
                recoverability,
                RecoveryStrategy.MANUAL_REVIEW,
                DecisionStatus.APPROVED,
                rationale,
            )
        rationale.append(DecisionRationaleEntry(rule_id="retry_cap", outcome="passed"))
        return _outcome(
            policy_input, recoverability, RecoveryStrategy.RETRY, DecisionStatus.APPROVED, rationale
        )
    rationale.append(DecisionRationaleEntry(rule_id="retry_cap", outcome="not_applicable"))

    # Rule 5 -- customer-action-required compatibility.
    if policy_input.disposition is DiagnosisDisposition.CUSTOMER_ACTION_REQUIRED:
        if policy_input.candidate_strategy in _CUSTOMER_ACTION_COMPATIBLE_STRATEGIES:
            rationale.append(
                DecisionRationaleEntry(rule_id="customer_action_compatibility", outcome="passed")
            )
            return _outcome(
                policy_input,
                recoverability,
                policy_input.candidate_strategy,
                DecisionStatus.APPROVED,
                rationale,
            )
        rationale.append(
            DecisionRationaleEntry(
                rule_id="customer_action_compatibility",
                outcome="failed",
                reason_code="strategy_incompatible",
            )
        )
        return _outcome(
            policy_input,
            recoverability,
            RecoveryStrategy.MANUAL_REVIEW,
            DecisionStatus.ESCALATED,
            rationale,
        )
    rationale.append(
        DecisionRationaleEntry(rule_id="customer_action_compatibility", outcome="not_applicable")
    )

    # Rule 6 -- default safe behaviour. manual_review / no_action candidates
    # (e.g. proposed for an UNKNOWN disposition) pass through unchanged;
    # anything else reaching here is a combination no rule above admits,
    # so it degrades to the universally safe manual_review/escalated
    # outcome rather than being approved on an unrecognised basis.
    if policy_input.candidate_strategy is RecoveryStrategy.NO_ACTION:
        rationale.append(DecisionRationaleEntry(rule_id="default_pass_through", outcome="passed"))
        return _outcome(
            policy_input,
            recoverability,
            RecoveryStrategy.NO_ACTION,
            DecisionStatus.APPROVED,
            rationale,
        )
    if policy_input.candidate_strategy is RecoveryStrategy.MANUAL_REVIEW:
        rationale.append(DecisionRationaleEntry(rule_id="default_pass_through", outcome="passed"))
        return _outcome(
            policy_input,
            recoverability,
            RecoveryStrategy.MANUAL_REVIEW,
            DecisionStatus.ESCALATED,
            rationale,
        )
    rationale.append(
        DecisionRationaleEntry(
            rule_id="default_safe_fallback", outcome="failed", reason_code="unhandled_combination"
        )
    )
    return _outcome(
        policy_input,
        recoverability,
        RecoveryStrategy.MANUAL_REVIEW,
        DecisionStatus.ESCALATED,
        rationale,
    )
