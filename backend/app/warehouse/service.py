"""Analytics warehouse read service (Phase 13).

Every function here reads ``case_analytics_facts`` (the ETL's
materialization) instead of joining the operational tables live -- this
is the actual "separate analytical workloads from operational
transaction processing" Phase 13 asks for. If the warehouse has never
been built, every report degrades to empty/zero rather than raising --
an unbuilt warehouse is an ordinary, expected state (a fresh environment,
or before the first ``scripts/build_analytics_warehouse.py`` run), not
an error condition.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.warehouse import CaseAnalyticsFact
from app.outcome.schema import ObservedOutcome
from app.warehouse.schema import (
    AnalyticsWarehouseReport,
    CaseAnalyticsFactRow,
    CurrencyAmount,
    ModelPerformanceStat,
    RecoveryRateStat,
)


async def get_fact_rows(session: AsyncSession) -> list[CaseAnalyticsFactRow]:
    """The raw materialization, one row per case -- read-only, for audit
    and debugging (mirrors ``app.analytics.service.get_strategy_dataset``'s
    role for Phase 9).
    """
    rows = (await session.scalars(select(CaseAnalyticsFact))).all()
    return [
        CaseAnalyticsFactRow(
            case_id=r.case_id,
            customer_id=r.customer_id,
            currency=r.currency,
            amount=r.amount,
            eligible=r.eligible,
            outcome_status=r.outcome_status,
            has_action=r.has_action,
            action_type=r.action_type,
            attempt_count=r.attempt_count,
            disposition=r.disposition,
            model_name=r.model_name,
            model_version=r.model_version,
            confidence=r.confidence,
            router_escalated=r.router_escalated,
            failure_reason=r.failure_reason,
            customer_case_segment=r.customer_case_segment,
            computed_at=r.computed_at,
        )
        for r in rows
    ]


def _bucket_by_currency(rows: list[tuple[str, Decimal]]) -> list[CurrencyAmount]:
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    counts: dict[str, int] = defaultdict(int)
    for currency, amount in rows:
        totals[currency] += amount
        counts[currency] += 1
    return [
        CurrencyAmount(currency=currency, amount=totals[currency], case_count=counts[currency])
        for currency in sorted(totals)
    ]


def _rate_stat(key: str, facts: list[CaseAnalyticsFact]) -> RecoveryRateStat:
    recovered = sum(1 for f in facts if f.outcome_status == ObservedOutcome.RECOVERED.value)
    not_recovered = sum(1 for f in facts if f.outcome_status == ObservedOutcome.NOT_RECOVERED.value)
    unresolved = len(facts) - recovered - not_recovered
    observed = recovered + not_recovered
    rate = (recovered / observed) if observed else None
    return RecoveryRateStat(
        key=key,
        total_case_count=len(facts),
        observed_count=observed,
        recovered_count=recovered,
        not_recovered_count=not_recovered,
        unresolved_count=unresolved,
        empirical_recovery_rate=rate,
    )


def _group_and_rate(
    facts: list[CaseAnalyticsFact], key_fn: Callable[[CaseAnalyticsFact], str | None]
) -> list[RecoveryRateStat]:
    grouped: dict[str, list[CaseAnalyticsFact]] = defaultdict(list)
    for fact in facts:
        key = key_fn(fact)
        if key is None:
            continue
        grouped[key].append(fact)
    return [_rate_stat(key, grouped[key]) for key in sorted(grouped)]


async def get_analytics_warehouse_report(session: AsyncSession) -> AnalyticsWarehouseReport:
    facts = list((await session.scalars(select(CaseAnalyticsFact))).all())
    built_at = await session.scalar(select(func.max(CaseAnalyticsFact.computed_at)))

    eligible = [f for f in facts if f.eligible]
    recovered = [f for f in facts if f.outcome_status == ObservedOutcome.RECOVERED.value]

    return AnalyticsWarehouseReport(
        fact_count=len(facts),
        warehouse_built_at=built_at,
        revenue_at_risk=_bucket_by_currency([(f.currency, f.amount) for f in eligible]),
        observed_recovered=_bucket_by_currency([(f.currency, f.amount) for f in recovered]),
        total_recovery_attempts=sum(f.attempt_count for f in facts),
        by_strategy=_group_and_rate(facts, lambda f: f.action_type),
        by_failure_reason=_group_and_rate(facts, lambda f: f.failure_reason),
        by_customer_segment=_group_and_rate(facts, lambda f: f.customer_case_segment),
        model_performance=_model_performance(facts),
    )


def _model_performance(facts: list[CaseAnalyticsFact]) -> list[ModelPerformanceStat]:
    grouped: dict[tuple[str, str], list[CaseAnalyticsFact]] = defaultdict(list)
    for fact in facts:
        if fact.model_name is None or fact.model_version is None:
            continue
        grouped[(fact.model_name, fact.model_version)].append(fact)

    stats: list[ModelPerformanceStat] = []
    for model_name, model_version in sorted(grouped):
        rows = grouped[(model_name, model_version)]
        confidences = [float(r.confidence) for r in rows if r.confidence is not None]
        latencies = [r.latency_ms for r in rows if r.latency_ms is not None]
        escalated = sum(1 for r in rows if r.router_escalated)
        stats.append(
            ModelPerformanceStat(
                model_name=model_name,
                model_version=model_version,
                diagnosis_count=len(rows),
                avg_confidence=(sum(confidences) / len(confidences)) if confidences else None,
                avg_latency_ms=(sum(latencies) / len(latencies)) if latencies else None,
                router_escalated_count=escalated,
                router_escalation_rate=(escalated / len(rows)) if rows else 0.0,
            )
        )
    return stats
