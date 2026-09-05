"""Extraction / transformation / load for the analytics warehouse (Phase
13).

Extract: read every ``RecoveryCase`` plus its ``Payment``, latest
``Diagnosis``, current-decision ``RecoveryAction`` (with executions), and
current Phase 7 outcome -- the exact same source tables and attribution
rules Phase 8's ``get_revenue_report`` and Phase 9's
``get_strategy_analytics_report`` already use (no new correlation rule,
no fuzzy matching, no AI reasoning enters any field here).

Transform: derive the denormalized, validated fields
``app.models.warehouse.CaseAnalyticsFact`` stores -- currency-safe
amounts, a structural (not causal) natural-vs-attempted-recovery split,
and a purely observed customer segment (case-volume tier, computed from
this same extraction pass, never the Phase 2 risk-scoring model).

Load: upsert one row per case, keyed by ``case_id`` (the fact table's own
primary key) -- idempotent and safe to re-run at any time; a rerun always
reflects the operational tables' current truth, it never appends a
duplicate or accumulates drift.

This module is the ONLY code that writes to ``case_analytics_facts`` --
see ``app.models.warehouse``'s module docstring for why that boundary is
what makes a later warehouse-storage migration (ClickHouse/BigQuery/
Snowflake) a load-step swap rather than a read-side rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.decision.actions import get_action_for_case
from app.models.decision import DecisionResult
from app.models.payment import Payment
from app.models.recovery import RecoveryCase
from app.models.warehouse import CaseAnalyticsFact
from app.outcome.schema import ObservedOutcome
from app.outcome.service import get_outcome_for_case
from app.services.diagnosis import get_latest_diagnosis

#: Purely structural customer segmentation by total recovery-case volume.
#: An observed fact about case history, not a risk/propensity model --
#: see ``app.models.warehouse.CaseAnalyticsFact.customer_case_segment``.
_SEGMENT_SINGLE = "single_case"
_SEGMENT_REPEAT_LOW = "repeat_2_4"
_SEGMENT_REPEAT_HIGH = "repeat_5_plus"


def _segment_for_case_count(count: int) -> str:
    if count <= 1:
        return _SEGMENT_SINGLE
    if count <= 4:
        return _SEGMENT_REPEAT_LOW
    return _SEGMENT_REPEAT_HIGH


@dataclass(frozen=True)
class ETLResult:
    facts_written: int
    run_at: datetime


async def _customer_case_counts(session: AsyncSession) -> dict[UUID, int]:
    rows = (
        await session.execute(
            select(RecoveryCase.customer_id, func.count(RecoveryCase.id)).group_by(
                RecoveryCase.customer_id
            )
        )
    ).all()
    return {customer_id: count for customer_id, count in rows}


async def _eligible_case_ids(session: AsyncSession) -> set[UUID]:
    """Cases with a ``DecisionResult`` -- identical eligibility rule to
    ``app.measurement.service._eligible_cases`` /
    ``app.analytics.service._eligible_cases``, duplicated here rather
    than imported (same established convention those two modules already
    follow relative to each other) to keep this module's read boundary
    independent of Phase 8/9's internals.
    """
    result = await session.scalars(
        select(RecoveryCase.id)
        .join(DecisionResult, DecisionResult.case_id == RecoveryCase.id)
        .distinct()
    )
    return set(result.all())


async def _build_fact(
    session: AsyncSession,
    case: RecoveryCase,
    payment: Payment,
    eligible: bool,
    customer_case_segment: str,
) -> CaseAnalyticsFact:
    outcome = await get_outcome_for_case(session, case.id)
    outcome_status = outcome.outcome if outcome is not None else ObservedOutcome.UNRESOLVED.value

    action = await get_action_for_case(session, case.id)
    has_action = action is not None
    action_type = action.action_type if action is not None else None
    attempt_count = len(action.executions) if action is not None else 0

    diagnosis = await get_latest_diagnosis(session, case.id)

    return CaseAnalyticsFact(
        case_id=case.id,
        customer_id=case.customer_id,
        currency=payment.currency,
        amount=payment.amount,
        eligible=eligible,
        outcome_status=outcome_status,
        has_action=has_action,
        action_type=action_type,
        attempt_count=attempt_count,
        disposition=diagnosis.disposition if diagnosis is not None else None,
        model_name=diagnosis.model_name if diagnosis is not None else None,
        model_version=diagnosis.model_version if diagnosis is not None else None,
        confidence=diagnosis.confidence if diagnosis is not None else None,
        latency_ms=diagnosis.latency_ms if diagnosis is not None else None,
        router_escalated=diagnosis.router_escalated if diagnosis is not None else None,
        failure_reason=payment.failure_reason,
        customer_case_segment=customer_case_segment,
    )


async def rebuild_warehouse(session: AsyncSession) -> ETLResult:
    """Recompute every case's analytics fact row from the current
    operational data and upsert it. Safe to call repeatedly (e.g. on a
    schedule, or after a batch of new cases) -- always converges to the
    operational tables' current truth, never accumulates duplicates or
    stale rows for a case that still exists.
    """
    cases = list((await session.scalars(select(RecoveryCase))).all())
    eligible_ids = await _eligible_case_ids(session)
    case_counts = await _customer_case_counts(session)

    existing_rows = {
        row.case_id: row for row in (await session.scalars(select(CaseAnalyticsFact))).all()
    }

    written = 0
    for case in cases:
        payment = await session.get(Payment, case.payment_id)
        if payment is None:
            continue  # defensive; FK guarantees this cannot occur in practice

        segment = _segment_for_case_count(case_counts.get(case.customer_id, 1))
        fresh = await _build_fact(session, case, payment, case.id in eligible_ids, segment)

        existing = existing_rows.get(case.id)
        if existing is None:
            session.add(fresh)
        else:
            existing.customer_id = fresh.customer_id
            existing.currency = fresh.currency
            existing.amount = fresh.amount
            existing.eligible = fresh.eligible
            existing.outcome_status = fresh.outcome_status
            existing.has_action = fresh.has_action
            existing.action_type = fresh.action_type
            existing.attempt_count = fresh.attempt_count
            existing.disposition = fresh.disposition
            existing.model_name = fresh.model_name
            existing.model_version = fresh.model_version
            existing.confidence = fresh.confidence
            existing.latency_ms = fresh.latency_ms
            existing.router_escalated = fresh.router_escalated
            existing.failure_reason = fresh.failure_reason
            existing.customer_case_segment = fresh.customer_case_segment
        written += 1

    await session.commit()
    return ETLResult(facts_written=written, run_at=datetime.now(UTC))
