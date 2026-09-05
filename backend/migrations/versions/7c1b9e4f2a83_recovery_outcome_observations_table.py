"""recovery outcome observations table

Revision ID: 7c1b9e4f2a83
Revises: 3f2a6c9d1e47
Create Date: 2026-09-01 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '7c1b9e4f2a83'
down_revision: Union[str, None] = '3f2a6c9d1e47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'recovery_outcome_observations',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('case_id', sa.UUID(), nullable=False),
        sa.Column('action_id', sa.UUID(), nullable=False),
        sa.Column('attempt_no', sa.Integer(), nullable=False),
        sa.Column('outcome', sa.String(length=20), nullable=False),
        sa.Column('is_terminal', sa.Boolean(), nullable=False),
        sa.Column('evidence_payment_id', sa.UUID(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['case_id'], ['recovery_cases.id']),
        sa.ForeignKeyConstraint(['action_id'], ['recovery_actions.id']),
        sa.ForeignKeyConstraint(['evidence_payment_id'], ['payments.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'action_id', 'attempt_no', name='uq_recovery_outcome_observations_identity'
        ),
    )
    op.create_index(
        op.f('ix_recovery_outcome_observations_case_id'),
        'recovery_outcome_observations',
        ['case_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_recovery_outcome_observations_action_id'),
        'recovery_outcome_observations',
        ['action_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_recovery_outcome_observations_action_id'),
        table_name='recovery_outcome_observations',
    )
    op.drop_index(
        op.f('ix_recovery_outcome_observations_case_id'),
        table_name='recovery_outcome_observations',
    )
    op.drop_table('recovery_outcome_observations')
