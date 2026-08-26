from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.payment import Payment, PaymentStatus


async def _make_customer(session: AsyncSession, external_id: str = "cust-1") -> Customer:
    customer = Customer(external_id=external_id, email="a@example.com", name="Ada")
    session.add(customer)
    await session.flush()
    return customer


async def test_customer_external_id_must_be_unique(db_session: AsyncSession) -> None:
    await _make_customer(db_session, "dup-cust")
    await db_session.commit()

    db_session.add(Customer(external_id="dup-cust", email="b@example.com"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_payment_requires_existing_customer(db_session: AsyncSession) -> None:
    import uuid

    payment = Payment(
        customer_id=uuid.uuid4(),
        external_reference="ref-orphan",
        amount="10.00",
        currency="USD",
        status=PaymentStatus.FAILED,
        occurred_at=datetime.now(UTC),
    )
    db_session.add(payment)
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_payment_external_reference_must_be_unique(db_session: AsyncSession) -> None:
    customer = await _make_customer(db_session)
    await db_session.commit()

    db_session.add(
        Payment(
            customer_id=customer.id,
            external_reference="dup-ref",
            amount="10.00",
            currency="USD",
            status=PaymentStatus.FAILED,
            occurred_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    db_session.add(
        Payment(
            customer_id=customer.id,
            external_reference="dup-ref",
            amount="20.00",
            currency="USD",
            status=PaymentStatus.SUCCEEDED,
            occurred_at=datetime.now(UTC),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
