"""Baseline-vs-AI recovery comparison (buildathon finalization).

Answers "what would a naive, pre-AI recovery approach have done, under
the SAME deterministic simulated environment this system already uses?"
-- NOT a randomized control/treatment experiment. No counterfactual
population, execution, or claim is introduced here; KI-006's frozen
position (no causal/incremental estimate exists in this system) is
unchanged. See ``app.measurement.schema.BASELINE_METHODOLOGY`` for the
full, user-facing account of the two fairness rules this module applies
(exclude already-resolved `no_action` cases; give the baseline the same
``RETRY_CAP`` attempt budget the real executor uses) -- both were added
after a 2026-09-04 red-team audit found the first version of this module
compared the two policies on an unfair population/budget and produced an
inflated apparent AI advantage.

This module never schedules, executes, or persists a second action. The
baseline outcome is computed by calling
``app.decision.providers.simulated_payment_provider.attempt(...)`` --
the exact function ``app.decision.executors`` calls for the real
pipeline -- directly, as a pure function of the case's own
``failure_reason``. No database write happens as a result.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schema import RecoveryStrategy
from app.decision.policy import RETRY_CAP
from app.decision.providers import SimulationOutcome, simulated_payment_provider
from app.decision.schema import DecisionStatus
from app.decision.service import get_decision_for_case
from app.measurement.schema import BaselineComparisonReport, CurrencyAmount
from app.measurement.service import _eligible_cases
from app.models.payment import Payment
from app.outcome.schema import ObservedOutcome
from app.outcome.service import get_outcome_for_case


def _bucket(rows: list[tuple[str, Decimal]]) -> list[CurrencyAmount]:
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    counts: dict[str, int] = defaultdict(int)
    for currency, amount in rows:
        totals[currency] += amount
        counts[currency] += 1
    return [
        CurrencyAmount(currency=currency, amount=totals[currency], case_count=counts[currency])
        for currency in sorted(totals)
    ]


def _baseline_recovers(failure_reason: str | None, case_id_for_correlation: str) -> bool:
    """Simulate up to RETRY_CAP blind-retry attempts -- the SAME bounded
    budget the real executor gives itself (app.decision.actions), so a
    case is never scored as a baseline failure purely because it needed a
    second attempt. Stops early on a permanent failure (no further
    attempts would help, mirroring the real executor's own early-stop).
    """
    for attempt_no in range(1, RETRY_CAP + 1):
        result = simulated_payment_provider.attempt(
            channel="retry",
            failure_reason=failure_reason,
            attempt_no=attempt_no,
            correlation_id=f"baseline:{case_id_for_correlation}",
        )
        if result.outcome is SimulationOutcome.SUCCESS:
            return True
        if result.outcome is SimulationOutcome.PERMANENT_FAILURE:
            return False
    return False


async def get_baseline_comparison_report(session: AsyncSession) -> BaselineComparisonReport:
    """Live-computed, mirroring ``app.measurement.service.get_revenue_report``'s
    own "compute from source tables, never a stored report" convention.
    """
    cases = await _eligible_cases(session)
    total_eligible_count = len(cases)

    ai_recovered_rows: list[tuple[str, Decimal]] = []
    baseline_recovered_rows: list[tuple[str, Decimal]] = []
    ai_recovered_count = 0
    baseline_recovered_count = 0
    escalated_count = 0
    already_resolved_excluded_count = 0
    compared_count = 0

    for case in cases:
        decision = await get_decision_for_case(session, case.id)
        approved_strategy = decision.approved_strategy if decision is not None else None

        if decision is not None and decision.decision_status == DecisionStatus.ESCALATED.value:
            escalated_count += 1

        # Excluded from the comparison entirely: a `no_action` decision
        # here can only come from app.decision.policy's already-paid
        # short-circuit (the mock diagnosis provider never itself
        # recommends `no_action` -- see app/ai/providers/mock.py's
        # _STRATEGY_BY_OUTCOME). Neither policy took, or would take, any
        # action for these -- see BASELINE_METHODOLOGY.
        if approved_strategy == RecoveryStrategy.NO_ACTION.value:
            already_resolved_excluded_count += 1
            continue

        compared_count += 1

        payment = await session.get(Payment, case.payment_id)
        assert payment is not None  # guaranteed by RecoveryCase.payment_id's FK
        currency = payment.currency
        amount = payment.amount

        outcome = await get_outcome_for_case(session, case.id)
        ai_status = outcome.outcome if outcome is not None else ObservedOutcome.UNRESOLVED.value
        if ai_status == ObservedOutcome.RECOVERED.value:
            ai_recovered_rows.append((currency, amount))
            ai_recovered_count += 1

        if _baseline_recovers(payment.failure_reason, str(case.id)):
            baseline_recovered_rows.append((currency, amount))
            baseline_recovered_count += 1

    ai_rate = (ai_recovered_count / compared_count) if compared_count else 0.0
    baseline_rate = (baseline_recovered_count / compared_count) if compared_count else 0.0

    return BaselineComparisonReport(
        total_eligible_case_count=total_eligible_count,
        already_resolved_excluded_count=already_resolved_excluded_count,
        compared_case_count=compared_count,
        ai_gated_observed_recovered=_bucket(ai_recovered_rows),
        baseline_simulated_recovered=_bucket(baseline_recovered_rows),
        ai_gated_recovery_rate=ai_rate,
        baseline_simulated_recovery_rate=baseline_rate,
        cases_where_ai_gate_avoided_a_blind_retry=escalated_count,
    )


__all__ = ["get_baseline_comparison_report"]
