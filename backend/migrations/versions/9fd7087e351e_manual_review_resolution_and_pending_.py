"""manual review resolution and pending_manual_review state

Revision ID: 9fd7087e351e
Revises: a1f4c9d0e2b7
Create Date: 2026-09-05 17:53:59.583377

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9fd7087e351e'
down_revision: Union[str, None] = 'a1f4c9d0e2b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# NOTE: the auto-generated diff also proposed dropping
# ix_domain_events_unpublished (a partial index on domain_events) -- a
# known alembic autogenerate false positive on partial indexes, unrelated
# to this migration's actual change. Deliberately not included here.


def upgrade() -> None:
    # Postgres requires ALTER TYPE ... ADD VALUE to run outside an
    # explicit transaction block in older versions; modern Postgres (16,
    # what this project runs) permits it inside a transaction but the new
    # value cannot be used in the same transaction that adds it. Alembic
    # runs each migration in its own transaction, so this and the table
    # creation below are safely two separate statements within one
    # migration, but the new enum value must not be referenced by any DML
    # in this same migration (there is none here -- only DDL).
    op.execute("ALTER TYPE recovery_case_state ADD VALUE IF NOT EXISTS 'PENDING_MANUAL_REVIEW'")

    op.create_table(
        'manual_review_resolutions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('case_id', sa.UUID(), nullable=False),
        sa.Column('resolution', sa.String(length=20), nullable=False),
        sa.Column('note', sa.String(length=1000), nullable=False),
        sa.Column('actor', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['case_id'], ['recovery_cases.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('case_id', name='uq_manual_review_resolutions_case_id'),
    )
    op.create_index(
        op.f('ix_manual_review_resolutions_case_id'),
        'manual_review_resolutions',
        ['case_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_manual_review_resolutions_case_id'), table_name='manual_review_resolutions')
    op.drop_table('manual_review_resolutions')
    # Postgres cannot remove a single value from an existing enum type
    # (no DROP VALUE) -- the standard workaround is rebuilding the type,
    # which every dependent column must be temporarily cast away from and
    # back. Not attempted here: no data can exist in this new state
    # immediately after a fresh upgrade in any normal migration sequence,
    # and this project's own downgrade tests (test_migrations.py) exercise
    # a full upgrade/downgrade roundtrip on a fresh database where no case
    # ever reaches PENDING_MANUAL_REVIEW mid-test -- so leaving the enum
    # value in place on downgrade (rather than reconstructing the type) is
    # the same pragmatic choice this project has not needed to make until
    # now, documented rather than silently glossed over.
