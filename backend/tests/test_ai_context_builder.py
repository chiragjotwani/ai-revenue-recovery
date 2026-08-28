"""RecoveryContextBuilder tests, against real Postgres (Section 49)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.context_builder import build_recovery_context
from app.models.customer import Customer
from app.models.payment import Payment, PaymentStatus
from app.models.recovery import RecoveryCase, RecoveryCaseState

BASE = datetime(2026, 3, 1, tzinfo=UTC)


async def _customer(session: AsyncSession, external_id: str = "cust-ctx") -> Customer:
    c = Customer(external_id=external_id, email="c@example.com", name="Ctx")
    session.add(c)
    await session.flush()
    return c


async def _payment(
    session: AsyncSession,
    customer: Customer,
    *,
    ref: str,
    status: PaymentStatus,
    occurred_at: datetime,
    failure_reason: str | None = None,
    amount: str = "4999.00",
) -> Payment:
    p = Payment(
        customer_id=customer.id,
        external_reference=ref,
        amount=Decimal(amount),
        currency="INR",
        status=status,
        failure_reason=failure_reason,
        occurred_at=occurred_at,
    )
    session.add(p)
    await session.flush()
    return p


async def _case(session: AsyncSession, payment: Payment) -> RecoveryCase:
    case = RecoveryCase(
        payment_id=payment.id,
        customer_id=payment.customer_id,
        state=RecoveryCaseState.DIAGNOSING,
    )
    session.add(case)
    await session.flush()
    return case


async def test_canonical_scenario_context(db_session: AsyncSession) -> None:
    cust = await _customer(db_session)
    for i in range(3):
        await _payment(
            db_session,
            cust,
            ref=f"s{i}",
            status=PaymentStatus.SUCCEEDED,
            occurred_at=BASE - timedelta(days=30 * (3 - i)),
        )
    failed = await _payment(
        db_session,
        cust,
        ref="f0",
        status=PaymentStatus.FAILED,
        occurred_at=BASE,
        failure_reason="insufficient_funds",
    )
    case = await _case(db_session, failed)
    await db_session.commit()

    ctx = await build_recovery_context(db_session, case)

    assert ctx.customer.total_payments == 4
    assert ctx.customer.successful_payments == 3
    assert ctx.customer.historical_success_rate == 0.75
    assert ctx.payment.failure_reason == "insufficient_funds"
    assert ctx.payment.external_reference == "f0"
    assert ctx.failure.consecutive_failures == 1
    assert ctx.failure.days_since_last_success == 30
    assert ctx.evidence_sufficiency == "sufficient"
    assert ctx.signals_conflict is False
    # history excludes the failing payment and is capped/most-recent-first
    assert failed.external_reference not in {h.external_reference for h in ctx.recent_history}
    assert len(ctx.recent_history) == 3
    assert ctx.applicable_policies  # policies are passed through


async def test_sparse_context_when_no_history_or_no_reason(db_session: AsyncSession) -> None:
    cust = await _customer(db_session, "cust-sparse")
    failed = await _payment(
        db_session,
        cust,
        ref="f-only",
        status=PaymentStatus.FAILED,
        occurred_at=BASE,
        failure_reason="insufficient_funds",
    )
    case = await _case(db_session, failed)
    await db_session.commit()

    ctx = await build_recovery_context(db_session, case)
    # Only one payment on record -> sparse.
    assert ctx.evidence_sufficiency == "sparse"
    assert ctx.failure.days_since_last_success is None


async def test_signals_conflict_for_fraud_reason_on_a_clean_customer(
    db_session: AsyncSession,
) -> None:
    cust = await _customer(db_session, "cust-clean")
    for i in range(5):
        await _payment(
            db_session,
            cust,
            ref=f"ok{i}",
            status=PaymentStatus.SUCCEEDED,
            occurred_at=BASE - timedelta(days=10 * (5 - i)),
        )
    failed = await _payment(
        db_session,
        cust,
        ref="fraud0",
        status=PaymentStatus.FAILED,
        occurred_at=BASE,
        failure_reason="fraud_suspected",
    )
    case = await _case(db_session, failed)
    await db_session.commit()

    ctx = await build_recovery_context(db_session, case)
    assert ctx.signals_conflict is True


async def test_recent_history_is_capped_at_ten(db_session: AsyncSession) -> None:
    cust = await _customer(db_session, "cust-many")
    for i in range(15):
        await _payment(
            db_session,
            cust,
            ref=f"h{i}",
            status=PaymentStatus.SUCCEEDED,
            occurred_at=BASE - timedelta(days=15 - i),
        )
    failed = await _payment(
        db_session,
        cust,
        ref="fx",
        status=PaymentStatus.FAILED,
        occurred_at=BASE,
        failure_reason="do_not_honor",
    )
    case = await _case(db_session, failed)
    await db_session.commit()

    ctx = await build_recovery_context(db_session, case)
    assert len(ctx.recent_history) == 10
