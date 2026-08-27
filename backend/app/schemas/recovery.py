import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.recovery import RecoveryCaseState


class OpenCaseRequest(BaseModel):
    payment_id: uuid.UUID


class TransitionRequest(BaseModel):
    to_state: RecoveryCaseState
    reason: str | None = None


class TransitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    from_state: RecoveryCaseState | None
    to_state: RecoveryCaseState
    reason: str | None
    actor: str
    created_at: datetime


class RecoveryCaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    payment_id: uuid.UUID
    customer_id: uuid.UUID
    state: RecoveryCaseState
    opened_at: datetime
    closed_at: datetime | None


class RecoveryCaseDetail(RecoveryCaseOut):
    history: list[TransitionOut]
