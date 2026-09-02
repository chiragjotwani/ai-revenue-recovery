"""Analytics warehouse storage (Phase 13, ``app.warehouse``).

``CaseAnalyticsFact`` is the one materialized table this phase
introduces: a denormalized, rebuild-in-place snapshot of one case's
analytically-relevant facts, kept intentionally separate from the
operational tables it is derived from (``app.warehouse.etl`` is the only
code that writes to it). Analytical reads (``app.warehouse.service``)
query this table instead of joining five operational tables per request
-- the actual point of "separate analytical workloads from operational
transaction processing".

A single wide fact table, not a star schema, is a deliberate scope
choice for this project's size: every dimension a Phase 13 report needs
(strategy, failure reason, customer segment, model) is already a column
on the case itself, so a dimension-table join would add ceremony without
adding a real capability. Keeping this table's writer isolated to one ETL
module is what keeps a later migration to a column-store warehouse
(ClickHouse/BigQuery/Snowflake) a matter of swapping the load step, not
rewriting the read side.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.recovery import RecoveryCase


class CaseAnalyticsFact(Base):
    """One case's denormalized analytical snapshot. Primary key is
    ``case_id`` itself -- the ETL upserts (recomputes-in-place) this row
    rather than appending a new one, so it always reflects the case's
    current state, never a stale or duplicated copy (there is no
    "duplicate row" failure mode by construction: the primary key IS the
    natural key).
    """

    __tablename__ = "case_analytics_facts"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recovery_cases.id"), primary_key=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    #: Has a ``DecisionResult`` -- same eligibility Phase 8/9 already use.
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: One of ``app.outcome.schema.ObservedOutcome`` values.
    outcome_status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    has_action: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    action_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    disposition: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    router_escalated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: Coarse, purely structural segmentation by the customer's total
    #: recovery-case volume (see ``app.warehouse.etl`` for the exact
    #: buckets) -- not the Phase 2 risk-scoring model, which is a
    #: different, currently-at-risk-focused computation.
    customer_case_segment: Mapped[str] = mapped_column(String(20), nullable=False)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    case: Mapped[RecoveryCase] = relationship()
