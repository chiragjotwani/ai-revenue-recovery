"""Strategy analytics service (Phase 9, scoped -- see
``app.analytics.schema`` module docstring for what this deliberately does
NOT implement).

Both entry points compute LIVE from the source tables (``RecoveryCase`` +
``DecisionResult`` + ``RecoveryAction`` + ``Diagnosis`` + the current
``RecoveryOutcomeObservation`` per case), the same "no second source of
truth" pattern ``app.measurement.service.get_revenue_report`` and
``app.risk.service.get_risk_summary`` already use. No new table is
introduced.

Attribution is exclusively the Phase 7 outcome (or its absence -- a case
with no observation yet is `unresolved` for these purposes, exactly as
Phase 8's report already treats it). No fuzzy matching, no new
correlation rule, no AI reasoning enters any number here.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.schema import (
    LOW_SAMPLE_THRESHOLD,
    StrategyAnalyticsReport,
    StrategyDatasetRow,
    StrategyStat,
)
from app.decision.actions import get_action_for_case
from app.models.decision import DecisionResult
from app.models.payment import Payment
from app.models.recovery import RecoveryCase
from app.outcome.schema import ObservedOutcome
from app.outcome.service import get_outcome_for_case
from app.services.diagnosis import get_latest_diagnosis


async def _eligible_cases(session: AsyncSession) -> list[RecoveryCase]:
    """Cases with a policy-approved decision that actually reached a
    scheduled/executed action -- the same eligibility Phase 9's dataset
    needs a `strategy` for (an escalated/rejected decision never has an
    action, so it has no strategy to analyze here). Mirrors
    ``app.measurement.service._eligible_cases``'s query shape.
    """
    result = await session.scalars(
        select(RecoveryCase)
        .join(DecisionResult, DecisionResult.case_id == RecoveryCase.id)
        .distinct()
    )
    return list(result.all())


async def get_strategy_dataset(session: AsyncSession) -> list[StrategyDatasetRow]:
    """The historical strategy dataset: one row per case that has an
    executed/scheduled action, with its strategy, disposition, current
    Phase 7 outcome (``None`` if not yet observed), and currency.
    """
    cases = await _eligible_cases(session)
    rows: list[StrategyDatasetRow] = []
    for case in cases:
        action = await get_action_for_case(session, case.id)
        if action is None:
            continue  # decided but never scheduled (escalated/rejected) -- no strategy yet

        diagnosis = await get_latest_diagnosis(session, case.id)
        if diagnosis is None:
            continue  # defensive; should not occur (decision requires a diagnosis)

        payment = await session.get(Payment, case.payment_id)
        assert payment is not None  # guaranteed by RecoveryCase.payment_id's FK

        outcome = await get_outcome_for_case(session, case.id)
        outcome_value = ObservedOutcome(outcome.outcome) if outcome is not None else None

        rows.append(
            StrategyDatasetRow(
                case_id=case.id,
                strategy=action.action_type,
                disposition=diagnosis.disposition,
                outcome=outcome_value,
                currency=payment.currency,
            )
        )
    return rows


def _stat_for_key(key: str, rows: list[StrategyDatasetRow]) -> StrategyStat:
    recovered = sum(1 for r in rows if r.outcome is ObservedOutcome.RECOVERED)
    not_recovered = sum(1 for r in rows if r.outcome is ObservedOutcome.NOT_RECOVERED)
    unresolved = sum(
        1 for r in rows if r.outcome is None or r.outcome is ObservedOutcome.UNRESOLVED
    )
    observed = recovered + not_recovered
    rate = (recovered / observed) if observed else None
    return StrategyStat(
        key=key,
        total_case_count=len(rows),
        observed_count=observed,
        recovered_count=recovered,
        not_recovered_count=not_recovered,
        unresolved_count=unresolved,
        empirical_recovery_rate=rate,
        low_sample=observed < LOW_SAMPLE_THRESHOLD,
    )


def _group_and_stat(
    rows: list[StrategyDatasetRow], key_fn: Callable[[StrategyDatasetRow], str]
) -> list[StrategyStat]:
    grouped: dict[str, list[StrategyDatasetRow]] = defaultdict(list)
    for row in rows:
        grouped[key_fn(row)].append(row)
    return [_stat_for_key(key, grouped[key]) for key in sorted(grouped)]


async def get_strategy_analytics_report(session: AsyncSession) -> StrategyAnalyticsReport:
    """The full Phase 9 strategy-analytics report."""
    rows = await get_strategy_dataset(session)
    return StrategyAnalyticsReport(
        dataset_size=len(rows),
        by_strategy=_group_and_stat(rows, lambda r: r.strategy),
        by_disposition=_group_and_stat(rows, lambda r: r.disposition),
    )
