from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PaymentReferenceConflictError
from app.models.customer import Customer
from app.models.event import IngestionEvent
from app.models.payment import Payment, PaymentStatus
from app.schemas.ingestion import IngestionResult, PaymentEventIn

_STATUS_BY_EVENT_TYPE = {
    "payment.succeeded": PaymentStatus.SUCCEEDED,
    "payment.failed": PaymentStatus.FAILED,
    "payment.pending": PaymentStatus.PENDING,
}


async def _get_existing_event(session: AsyncSession, idempotency_key: str) -> IngestionEvent | None:
    result: IngestionEvent | None = await session.scalar(
        select(IngestionEvent).where(IngestionEvent.idempotency_key == idempotency_key)
    )
    return result


async def _get_or_create_customer(
    session: AsyncSession, external_id: str, email: str, name: str | None
) -> Customer:
    customer = await session.scalar(select(Customer).where(Customer.external_id == external_id))
    if customer is not None:
        return customer
    customer = Customer(external_id=external_id, email=email, name=name)
    session.add(customer)
    await session.flush()
    return customer


async def ingest_payment_event(session: AsyncSession, event_in: PaymentEventIn) -> IngestionResult:
    """Validate, idempotently persist, and materialize a payment event.

    This is the single entry point for payment data entering the platform.
    The database is the source of truth (ADR-001): this function never
    trusts the caller to have already deduplicated, and every branch is
    exercised by tests in ``tests/test_ingestion.py``.
    """
    existing = await _get_existing_event(session, event_in.idempotency_key)
    if existing is not None:
        assert existing.customer_id is not None
        assert existing.payment_id is not None
        return IngestionResult(
            event_id=existing.id,
            customer_id=existing.customer_id,
            payment_id=existing.payment_id,
            duplicate=True,
        )

    # KI-008: there is deliberately no separate "does a Payment with this
    # external_reference already exist?" pre-check here. A standalone SELECT
    # between this idempotency-key check and the insert below is a
    # time-of-check-to-time-of-use race: under genuine concurrency, a second
    # caller sharing this same idempotency key can commit in the gap, and an
    # isolated pre-check would then misread that committed row as a
    # *different* logical event's conflict rather than recognising it as the
    # caller's own duplicate. The external_reference uniqueness constraint is
    # instead enforced by the database (Payment.external_reference is
    # unique) and reconciled by the except-IntegrityError recheck below,
    # which decides "duplicate" vs. "genuine conflict" by idempotency key --
    # the same authority already used above -- with no separate racy check.
    try:
        customer = await _get_or_create_customer(
            session,
            external_id=event_in.customer.external_id,
            email=event_in.customer.email,
            name=event_in.customer.name,
        )

        payment = Payment(
            customer_id=customer.id,
            external_reference=event_in.payment.external_reference,
            amount=event_in.payment.amount,
            currency=event_in.payment.currency.upper(),
            status=_STATUS_BY_EVENT_TYPE[event_in.event_type],
            failure_reason=event_in.payment.failure_reason,
            occurred_at=event_in.occurred_at,
        )
        session.add(payment)
        await session.flush()

        event = IngestionEvent(
            idempotency_key=event_in.idempotency_key,
            event_type=event_in.event_type,
            source=event_in.source,
            payload=event_in.model_dump(mode="json"),
            occurred_at=event_in.occurred_at,
            customer_id=customer.id,
            payment_id=payment.id,
        )
        session.add(event)
        await session.flush()
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # A concurrent request may have won the race on either unique
        # constraint (idempotency_key or external_reference). Re-check
        # rather than assuming which one, to give an accurate result.
        existing = await _get_existing_event(session, event_in.idempotency_key)
        if existing is not None:
            assert existing.customer_id is not None
            assert existing.payment_id is not None
            return IngestionResult(
                event_id=existing.id,
                customer_id=existing.customer_id,
                payment_id=existing.payment_id,
                duplicate=True,
            )
        raise PaymentReferenceConflictError(event_in.payment.external_reference) from None

    return IngestionResult(
        event_id=event.id,
        customer_id=customer.id,
        payment_id=payment.id,
        duplicate=False,
    )
