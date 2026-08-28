"""RecoveryContextBuilder (Phase 4, Section 49).

The database is memory; this builder decides what slice of it the reasoning
model actually sees. It never dumps raw rows -- it assembles a bounded,
curated summary: customer, the failed payment, a failure summary, a capped
slice of recent history, previous interventions, the case state, and the
applicable policies.

It also computes two cheap signals the model (and the safeguards) use:
``evidence_sufficiency`` and ``signals_conflict``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import Payment, PaymentStatus
from app.models.recovery import RecoveryCase
from app.risk.features import compute_risk_features

_RECENT_HISTORY_LIMIT = 10
_FRAUD_REASONS = {"stolen_card", "lost_card", "fraud_suspected"}

# Static placeholder until a real policy store exists (Phase 5). Passing the
# policies into the context keeps the prompt honest about the constraints
# the downstream decision engine will enforce.
_APPLICABLE_POLICIES = [
    "A payment may be retried at most 3 times.",
    "Do not retry a payment the customer has already paid by another attempt.",
    "Suspected-fraud outcomes are never auto-retried; they go to manual review.",
]


class CustomerSummary(BaseModel):
    external_id: str
    tenure_days: int
    total_payments: int
    successful_payments: int
    historical_success_rate: float


class PaymentSummary(BaseModel):
    external_reference: str
    amount: Decimal
    currency: str
    status: str
    failure_reason: str | None
    occurred_at: datetime


class FailureSummary(BaseModel):
    consecutive_failures: int
    distinct_prior_failure_reasons: list[str]
    days_since_last_success: int | None


class HistoryItem(BaseModel):
    external_reference: str
    status: str
    amount: Decimal
    currency: str
    failure_reason: str | None
    occurred_at: datetime


class RecoveryContext(BaseModel):
    case_id: UUID
    case_state: str
    customer: CustomerSummary
    payment: PaymentSummary
    failure: FailureSummary
    recent_history: list[HistoryItem]
    previous_interventions: list[str]
    applicable_policies: list[str]
    evidence_sufficiency: Literal["sufficient", "sparse"]
    signals_conflict: bool


async def build_recovery_context(session: AsyncSession, case: RecoveryCase) -> RecoveryContext:
    payment = await session.get(Payment, case.payment_id)
    if payment is None:  # pragma: no cover - a case cannot exist without its payment
        raise ValueError(f"payment {case.payment_id} for case {case.id} is missing")

    history_rows = list(
        (
            await session.scalars(
                select(Payment)
                .where(Payment.customer_id == case.customer_id)
                .where(Payment.status != PaymentStatus.PENDING)
                .order_by(Payment.occurred_at)
            )
        ).all()
    )

    succeeded = [p for p in history_rows if p.status == PaymentStatus.SUCCEEDED]
    total_payments = len(history_rows)

    earliest_at = min((p.occurred_at for p in history_rows), default=payment.occurred_at)
    tenure_days = max((payment.occurred_at - earliest_at).days, 0)

    last_success_at = max((p.occurred_at for p in succeeded), default=None)
    days_since_last_success = (
        (payment.occurred_at - last_success_at).days if last_success_at is not None else None
    )

    prior_failures = [
        p
        for p in history_rows
        if p.status == PaymentStatus.FAILED and p.occurred_at < payment.occurred_at
    ]
    distinct_prior_reasons = sorted(
        {p.failure_reason for p in prior_failures if p.failure_reason is not None}
    )

    features = await compute_risk_features(session, payment)

    recent_history = [
        HistoryItem(
            external_reference=p.external_reference,
            status=p.status.value,
            amount=p.amount,
            currency=p.currency,
            failure_reason=p.failure_reason,
            occurred_at=p.occurred_at,
        )
        for p in reversed(history_rows)
        if p.id != payment.id
    ][:_RECENT_HISTORY_LIMIT]

    evidence_sufficiency: Literal["sufficient", "sparse"] = (
        "sparse" if payment.failure_reason is None or total_payments <= 1 else "sufficient"
    )
    # A fraud/stolen/lost flag on an otherwise well-established, reliable
    # customer is a genuine "signals disagree" situation -- surfaced so the
    # model (and the safeguards) treat it with low confidence.
    signals_conflict = (
        payment.failure_reason in _FRAUD_REASONS
        and features.historical_success_rate >= 0.8
        and len(succeeded) >= 3
    )

    return RecoveryContext(
        case_id=case.id,
        case_state=case.state.value,
        customer=CustomerSummary(
            external_id=(await _customer_external_id(session, case.customer_id)),
            tenure_days=tenure_days,
            total_payments=total_payments,
            successful_payments=len(succeeded),
            historical_success_rate=round(features.historical_success_rate, 4),
        ),
        payment=PaymentSummary(
            external_reference=payment.external_reference,
            amount=payment.amount,
            currency=payment.currency,
            status=payment.status.value,
            failure_reason=payment.failure_reason,
            occurred_at=payment.occurred_at,
        ),
        failure=FailureSummary(
            consecutive_failures=features.consecutive_failures,
            distinct_prior_failure_reasons=distinct_prior_reasons,
            days_since_last_success=days_since_last_success,
        ),
        recent_history=recent_history,
        previous_interventions=[],  # populated from Phase 6 onward
        applicable_policies=list(_APPLICABLE_POLICIES),
        evidence_sufficiency=evidence_sufficiency,
        signals_conflict=signals_conflict,
    )


async def _customer_external_id(session: AsyncSession, customer_id: UUID) -> str:
    from app.models.customer import Customer

    customer = await session.get(Customer, customer_id)
    return customer.external_id if customer is not None else str(customer_id)
