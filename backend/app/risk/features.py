from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import Payment, PaymentStatus


@dataclass(frozen=True)
class RiskFeatures:
    """Inputs to rule-based risk scoring for a single failed payment.

    Computed entirely from Postgres (the source of truth, ADR-001) --
    never from an LLM or any other inferred source.
    """

    consecutive_failures: int
    historical_success_rate: float
    has_prior_success: bool
    failure_reason: str | None


async def compute_risk_features(session: AsyncSession, payment: Payment) -> RiskFeatures:
    """Compute risk features for a failed payment from that customer's
    payment history in Postgres.

    ``consecutive_failures`` counts failures since the customer's most
    recent successful payment (inclusive of this one) -- i.e. how many
    times in a row this customer has failed without a subsequent success.
    A customer with no successful payment on record has all of their
    failures counted here.
    """
    history_stmt = (
        select(Payment)
        .where(Payment.customer_id == payment.customer_id)
        .where(Payment.status != PaymentStatus.PENDING)
    )
    history = (await session.scalars(history_stmt)).all()

    succeeded = [p for p in history if p.status == PaymentStatus.SUCCEEDED]
    failed = [p for p in history if p.status == PaymentStatus.FAILED]

    last_success_at: datetime | None = max((p.occurred_at for p in succeeded), default=None)

    consecutive_failures = sum(
        1 for p in failed if last_success_at is None or p.occurred_at > last_success_at
    )

    total = len(succeeded) + len(failed)
    historical_success_rate = (len(succeeded) / total) if total > 0 else 0.0

    return RiskFeatures(
        consecutive_failures=consecutive_failures,
        historical_success_rate=historical_success_rate,
        has_prior_success=last_success_at is not None,
        failure_reason=payment.failure_reason,
    )
