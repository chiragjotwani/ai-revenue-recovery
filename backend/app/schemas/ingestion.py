import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field

PaymentEventType = Literal["payment.succeeded", "payment.failed", "payment.pending"]

# ``payments.amount`` is ``NUMERIC(12, 2)``: at most 10 integer digits and
# exactly 2 fractional digits, so the largest storable value is
# 9_999_999_999.99. The request contract is validated against that here so
# that an out-of-range amount is a clean ``422`` (never an unhandled
# database ``NumericValueOutOfRange`` / HTTP 500 -- BUG-002) and an amount
# with sub-cent precision is *rejected*, never silently rounded to 2 dp
# (BUG-003). The database is a backstop, not the primary validator.
MAX_PAYMENT_AMOUNT = Decimal("9999999999.99")

MoneyAmount = Annotated[
    Decimal,
    Field(gt=0, le=MAX_PAYMENT_AMOUNT, max_digits=12, decimal_places=2),
]


class CustomerIn(BaseModel):
    external_id: str = Field(min_length=1, max_length=255)
    email: EmailStr
    name: str | None = None


class PaymentIn(BaseModel):
    external_reference: str = Field(min_length=1, max_length=255)
    amount: MoneyAmount
    currency: str = Field(min_length=3, max_length=3)
    failure_reason: str | None = None


class PaymentEventIn(BaseModel):
    """Canonical shape for an inbound payment lifecycle event.

    ``idempotency_key`` must be supplied by the source system and uniquely
    identifies this exact event delivery; redelivering the same key must
    never create duplicate state (see IngestionEvent).
    """

    idempotency_key: str = Field(min_length=1, max_length=255)
    event_type: PaymentEventType
    source: str = Field(min_length=1, max_length=100)
    occurred_at: datetime
    customer: CustomerIn
    payment: PaymentIn


class IngestionResult(BaseModel):
    event_id: uuid.UUID
    customer_id: uuid.UUID
    payment_id: uuid.UUID
    duplicate: bool
