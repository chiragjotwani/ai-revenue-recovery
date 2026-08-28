"""Diagnosis output schema and versioned enums (Phase 4, Sections 50/51).

Two layers, by design (ADR-005):

* ``DiagnosisOutcome`` -- the specific failure cause. This is what the
  reasoning model is asked to choose.
* ``DiagnosisDisposition`` -- the coarse routing category Phase 5's
  decision engine branches on. It is **derived from the outcome by our
  code**, never returned by the model, so the model cannot produce a
  cause/disposition pair that disagrees.

``ModelDiagnosisJSON`` is exactly what a provider must return as JSON.
``Diagnosis`` is the validated, enriched result the rest of the platform
stores and serves.
"""

from __future__ import annotations

import enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION: Literal["1"] = "1"


class DiagnosisOutcome(str, enum.Enum):
    """The specific cause of a payment failure, as judged from the context.

    ``UNKNOWN`` is a valid, expected result when the evidence is
    insufficient -- it is never a failure of the model (Section 37).
    """

    INSUFFICIENT_FUNDS = "insufficient_funds"
    CARD_EXPIRED = "card_expired"
    DO_NOT_HONOR = "do_not_honor"
    PROCESSING_ERROR = "processing_error"
    STOLEN_CARD = "stolen_card"
    LOST_CARD = "lost_card"
    FRAUD_SUSPECTED = "fraud_suspected"
    AUTHENTICATION_REQUIRED = "authentication_required"
    CARD_NOT_SUPPORTED = "card_not_supported"
    UNKNOWN = "unknown"


class DiagnosisDisposition(str, enum.Enum):
    """Coarse routing category derived from the outcome.

    Phase 5's decision engine branches on this, not on the raw outcome.
    """

    RETRIABLE_TRANSIENT = "retriable_transient"
    CUSTOMER_ACTION_REQUIRED = "customer_action_required"
    SUSPECTED_FRAUD = "suspected_fraud"
    UNKNOWN = "unknown"


class RecoveryStrategy(str, enum.Enum):
    """Advisory recovery approach suggested alongside a diagnosis.

    This is only a hint (Section 38: "AI recommends a retry after 6
    hours"). Phase 5's policy engine is the authority on what actually
    happens; nothing here executes anything.
    """

    RETRY = "retry"
    REQUEST_PAYMENT_METHOD_UPDATE = "request_payment_method_update"
    CONTACT_CUSTOMER = "contact_customer"
    MANUAL_REVIEW = "manual_review"
    NO_ACTION = "no_action"


# Deterministic outcome -> disposition mapping. Every outcome maps to
# exactly one disposition. Enforced complete by test_ai_diagnosis.py.
_OUTCOME_DISPOSITION: dict[DiagnosisOutcome, DiagnosisDisposition] = {
    DiagnosisOutcome.INSUFFICIENT_FUNDS: DiagnosisDisposition.RETRIABLE_TRANSIENT,
    DiagnosisOutcome.DO_NOT_HONOR: DiagnosisDisposition.RETRIABLE_TRANSIENT,
    DiagnosisOutcome.PROCESSING_ERROR: DiagnosisDisposition.RETRIABLE_TRANSIENT,
    DiagnosisOutcome.CARD_EXPIRED: DiagnosisDisposition.CUSTOMER_ACTION_REQUIRED,
    DiagnosisOutcome.CARD_NOT_SUPPORTED: DiagnosisDisposition.CUSTOMER_ACTION_REQUIRED,
    DiagnosisOutcome.AUTHENTICATION_REQUIRED: DiagnosisDisposition.CUSTOMER_ACTION_REQUIRED,
    DiagnosisOutcome.STOLEN_CARD: DiagnosisDisposition.SUSPECTED_FRAUD,
    DiagnosisOutcome.LOST_CARD: DiagnosisDisposition.SUSPECTED_FRAUD,
    DiagnosisOutcome.FRAUD_SUSPECTED: DiagnosisDisposition.SUSPECTED_FRAUD,
    DiagnosisOutcome.UNKNOWN: DiagnosisDisposition.UNKNOWN,
}


def disposition_for(outcome: DiagnosisOutcome) -> DiagnosisDisposition:
    return _OUTCOME_DISPOSITION[outcome]


class ModelDiagnosisJSON(BaseModel):
    """The exact JSON contract a reasoning-model provider must return.

    Anything not matching this (missing field, unknown outcome value,
    non-numeric confidence, empty reasoning) is a validation failure and
    the diagnosis is rejected -- nothing downstream runs (Section 37).
    """

    model_config = {"extra": "forbid"}

    outcome: DiagnosisOutcome
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=1, max_length=2000)
    recommended_strategy: RecoveryStrategy
    recommended_delay_hours: int | None = Field(default=None, ge=0, le=720)

    @field_validator("reasoning")
    @classmethod
    def _reasoning_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("reasoning must not be blank")
        return v.strip()


class Diagnosis(BaseModel):
    """A validated diagnosis, enriched with the derived disposition and the
    schema version. This is what gets persisted and served.
    """

    outcome: DiagnosisOutcome
    disposition: DiagnosisDisposition
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    recommended_strategy: RecoveryStrategy
    recommended_delay_hours: int | None
    schema_version: Literal["1"] = SCHEMA_VERSION

    @classmethod
    def from_model_json(cls, raw: ModelDiagnosisJSON) -> Diagnosis:
        return cls(
            outcome=raw.outcome,
            disposition=disposition_for(raw.outcome),
            confidence=raw.confidence,
            reasoning=raw.reasoning,
            recommended_strategy=raw.recommended_strategy,
            recommended_delay_hours=raw.recommended_delay_hours,
            schema_version=SCHEMA_VERSION,
        )
