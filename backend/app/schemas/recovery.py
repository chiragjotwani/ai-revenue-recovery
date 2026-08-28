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


class DiagnosisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    outcome: str
    disposition: str
    confidence: float
    reasoning: str
    recommended_strategy: str
    recommended_delay_hours: int | None
    schema_version: str
    model_name: str
    model_version: str
    prompt_version: str
    latency_ms: int
    created_at: datetime


class RecoveryCaseDetail(RecoveryCaseOut):
    history: list[TransitionOut]
    diagnosis: DiagnosisOut | None = None
