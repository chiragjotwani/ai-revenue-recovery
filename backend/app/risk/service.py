from collections import Counter
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.payment import Payment, PaymentStatus
from app.risk.features import compute_risk_features
from app.risk.scoring import compute_risk_score, risk_level
from app.schemas.risk import RiskAssessment, RiskLevelBreakdown, RiskSummary


async def _at_risk_payments(session: AsyncSession) -> list[Payment]:
    """Failed payments with no later successful payment for the same
    customer -- i.e. revenue that is still at risk, not already resolved
    by a subsequent successful attempt.
    """
    later_success = aliased(Payment)
    stmt = (
        select(Payment)
        .where(Payment.status == PaymentStatus.FAILED)
        .where(
            ~select(later_success.id)
            .where(later_success.customer_id == Payment.customer_id)
            .where(later_success.status == PaymentStatus.SUCCEEDED)
            .where(later_success.occurred_at > Payment.occurred_at)
            .exists()
        )
        .order_by(Payment.occurred_at.desc())
    )
    return list((await session.scalars(stmt)).all())


async def assess_risk(session: AsyncSession, payment: Payment) -> RiskAssessment:
    features = await compute_risk_features(session, payment)
    score = compute_risk_score(features)
    return RiskAssessment(
        payment_id=payment.id,
        customer_id=payment.customer_id,
        external_reference=payment.external_reference,
        amount=payment.amount,
        currency=payment.currency,
        failure_reason=payment.failure_reason,
        consecutive_failures=features.consecutive_failures,
        historical_success_rate=features.historical_success_rate,
        risk_score=score,
        risk_level=risk_level(score),
    )


async def list_risk_assessments(session: AsyncSession) -> list[RiskAssessment]:
    payments = await _at_risk_payments(session)
    return [await assess_risk(session, payment) for payment in payments]


async def get_risk_summary(session: AsyncSession) -> RiskSummary:
    """Aggregate revenue-at-risk across all currently at-risk payments.

    ``revenue_at_risk`` is a naive sum across currencies with no FX
    conversion (there is no exchange-rate source in this phase). It is
    only a meaningful total when ``currency_breakdown`` has a single
    currency; see KI-006 in docs/known-issues.md. Callers needing an
    accurate multi-currency total must use ``currency_breakdown``.
    """
    assessments = await list_risk_assessments(session)

    currency_breakdown: dict[str, Decimal] = {}
    for assessment in assessments:
        currency_breakdown[assessment.currency] = (
            currency_breakdown.get(assessment.currency, Decimal("0")) + assessment.amount
        )

    level_counts = Counter(a.risk_level for a in assessments)

    return RiskSummary(
        at_risk_payment_count=len(assessments),
        revenue_at_risk=sum(currency_breakdown.values(), Decimal("0")),
        currency_breakdown=currency_breakdown,
        risk_level_breakdown=RiskLevelBreakdown(
            low=level_counts.get("low", 0),
            medium=level_counts.get("medium", 0),
            high=level_counts.get("high", 0),
        ),
    )
