import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.recovery import RecoveryCase


class Diagnosis(Base):
    """A stored, validated diagnosis for a recovery case (Section 51).

    ``outcome`` / ``disposition`` / ``recommended_strategy`` are stored as
    strings, not Postgres enums: the diagnosis taxonomy is expected to
    evolve as the models and evaluation data mature, and app-level
    validation (``app/ai/schema.py``) already guarantees the values. See
    ADR-005.

    The ``model_*`` / ``prompt_version`` / ``schema_version`` / ``latency_ms``
    columns are the audit fields Section 51 requires, so diagnoses from
    different model or prompt versions can be compared later.
    """

    __tablename__ = "diagnoses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recovery_cases.id"), nullable=False, index=True
    )

    outcome: Mapped[str] = mapped_column(String(50), nullable=False)
    disposition: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_strategy: Mapped[str] = mapped_column(String(50), nullable=False)
    recommended_delay_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)

    schema_version: Mapped[str] = mapped_column(String(10), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    case: Mapped["RecoveryCase"] = relationship()
