import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

PaymentEventType = Literal["payment.succeeded", "payment.failed", "payment.pending"]


class CustomerIn(BaseModel):
    external_id: str = Field(min_length=1, max_length=255)
    email: EmailStr
    name: str | None = None


class PaymentIn(BaseModel):
    external_reference: str = Field(min_length=1, max_length=255)
    amount: Decimal = Field(gt=0)
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
