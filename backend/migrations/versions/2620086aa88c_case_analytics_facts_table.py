"""case analytics facts table

Revision ID: 2620086aa88c
Revises: c2d8f6a91e53
Create Date: 2026-09-02 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2620086aa88c"
down_revision: str | None = "c2d8f6a91e53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "case_analytics_facts",
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("outcome_status", sa.String(length=20), nullable=False),
        sa.Column("has_action", sa.Boolean(), nullable=False),
        sa.Column("action_type", sa.String(length=50), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("disposition", sa.String(length=50), nullable=True),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("model_version", sa.String(length=100), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("router_escalated", sa.Boolean(), nullable=True),
        sa.Column("failure_reason", sa.String(length=255), nullable=True),
        sa.Column("customer_case_segment", sa.String(length=20), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["case_id"], ["recovery_cases.id"]),
        sa.PrimaryKeyConstraint("case_id"),
    )
    op.create_index(
        op.f("ix_case_analytics_facts_customer_id"),
        "case_analytics_facts",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_case_analytics_facts_outcome_status"),
        "case_analytics_facts",
        ["outcome_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_case_analytics_facts_has_action"),
        "case_analytics_facts",
        ["has_action"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_case_analytics_facts_has_action"), table_name="case_analytics_facts")
    op.drop_index(op.f("ix_case_analytics_facts_outcome_status"), table_name="case_analytics_facts")
    op.drop_index(op.f("ix_case_analytics_facts_customer_id"), table_name="case_analytics_facts")
    op.drop_table("case_analytics_facts")
