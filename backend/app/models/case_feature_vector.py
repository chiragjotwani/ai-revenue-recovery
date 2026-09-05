"""Persisted case feature vectors (Phase 11: "vector storage").

One row per ``(case_id, diagnosis_id)`` -- mirrors ``DecisionResult``'s own
identity shape exactly (a vector is a function of exactly one case's
exactly one diagnosis). Storing it avoids recomputing every diagnosed
case's vector on every retrieval query; ``features`` is a plain JSON array
of floats, not a specialized vector-column type -- no ``pgvector``
extension or other new database dependency is introduced (KI-002-style
discipline: do not add infrastructure this repository does not already
have configured, for a feature-vector count in the dozens, not millions).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.diagnosis import Diagnosis
    from app.models.recovery import RecoveryCase


class CaseFeatureVector(Base):
    """A deterministic structured-feature vector for one diagnosed case
    (see ``app.retrieval.embedding`` -- NOT a learned/neural embedding).
    """

    __tablename__ = "case_feature_vectors"
    __table_args__ = (
        UniqueConstraint("case_id", "diagnosis_id", name="uq_case_feature_vectors_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recovery_cases.id"), nullable=False, index=True
    )
    diagnosis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("diagnoses.id"), nullable=False, index=True
    )
    features: Mapped[list[float]] = mapped_column(JSONB, nullable=False)
    feature_version: Mapped[str] = mapped_column(String(10), nullable=False)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    case: Mapped[RecoveryCase] = relationship()
    diagnosis: Mapped[Diagnosis] = relationship()
