"""Baseline-vs-AI recovery comparison (buildathon finalization).

Answers "what would a naive, pre-AI recovery approach have done, under
the SAME deterministic simulated environment this system already uses?"
-- NOT a randomized control/treatment experiment. No counterfactual
population, execution, or claim is introduced here; KI-006's frozen
position (no causal/incremental estimate exists in this system) is
unchanged. See ``app.measurement.schema.BASELINE_METHODOLOGY`` for the
full, user-facing account.

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


async def get_baseline_comparison_report(session: AsyncSession) -> BaselineComparisonReport:
    """Live-computed, mirroring ``app.measurement.service.get_revenue_report``'s
    own "compute from source tables, never a stored report" convention.
    """
    cases = await _eligible_cases(session)

    ai_recovered_rows: list[tuple[str, Decimal]] = []
    baseline_recovered_rows: list[tuple[str, Decimal]] = []
    ai_recovered_count = 0
    baseline_recovered_count = 0
    escalated_count = 0

    for case in cases:
        payment = await session.get(Payment, case.payment_id)
        assert payment is not None  # guaranteed by RecoveryCase.payment_id's FK
        currency = payment.currency
        amount = payment.amount

        outcome = await get_outcome_for_case(session, case.id)
        ai_status = outcome.outcome if outcome is not None else ObservedOutcome.UNRESOLVED.value
        if ai_status == ObservedOutcome.RECOVERED.value:
            ai_recovered_rows.append((currency, amount))
            ai_recovered_count += 1

        decision = await get_decision_for_case(session, case.id)
        if decision is not None and decision.decision_status == DecisionStatus.ESCALATED.value:
            escalated_count += 1

        baseline_result = simulated_payment_provider.attempt(
            channel="retry",
            failure_reason=payment.failure_reason,
            attempt_no=1,
            correlation_id=f"baseline:{case.id}",
        )
        if baseline_result.outcome is SimulationOutcome.SUCCESS:
            baseline_recovered_rows.append((currency, amount))
            baseline_recovered_count += 1

    eligible_count = len(cases)
    ai_rate = (ai_recovered_count / eligible_count) if eligible_count else 0.0
    baseline_rate = (baseline_recovered_count / eligible_count) if eligible_count else 0.0

    return BaselineComparisonReport(
        eligible_case_count=eligible_count,
        ai_gated_observed_recovered=_bucket(ai_recovered_rows),
        baseline_simulated_recovered=_bucket(baseline_recovered_rows),
        ai_gated_recovery_rate=ai_rate,
        baseline_simulated_recovery_rate=baseline_rate,
        cases_where_ai_gate_avoided_a_blind_retry=escalated_count,
    )


__all__ = ["get_baseline_comparison_report"]
