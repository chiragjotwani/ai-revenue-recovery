"""recovery actions tables

Revision ID: 3f2a6c9d1e47
Revises: 95a41f6c2e7e
Create Date: 2026-09-01 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '3f2a6c9d1e47'
down_revision: Union[str, None] = '95a41f6c2e7e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'recovery_actions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('case_id', sa.UUID(), nullable=False),
        sa.Column('decision_result_id', sa.UUID(), nullable=False),
        sa.Column('action_type', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['case_id'], ['recovery_cases.id']),
        sa.ForeignKeyConstraint(['decision_result_id'], ['decision_results.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'case_id', 'action_type', 'decision_result_id', name='uq_recovery_actions_identity'
        ),
    )
    op.create_index(
        op.f('ix_recovery_actions_case_id'), 'recovery_actions', ['case_id'], unique=False
    )
    op.create_index(
        op.f('ix_recovery_actions_decision_result_id'),
        'recovery_actions',
        ['decision_result_id'],
        unique=False,
    )

    op.create_table(
        'recovery_action_executions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('action_id', sa.UUID(), nullable=False),
        sa.Column('attempt_no', sa.Integer(), nullable=False),
        sa.Column('idempotency_key', sa.String(length=200), nullable=False),
        sa.Column('outcome', sa.String(length=50), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['action_id'], ['recovery_actions.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key'),
        sa.UniqueConstraint(
            'action_id', 'attempt_no', name='uq_recovery_action_executions_identity'
        ),
    )
    op.create_index(
        op.f('ix_recovery_action_executions_action_id'),
        'recovery_action_executions',
        ['action_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_recovery_action_executions_action_id'), table_name='recovery_action_executions'
    )
    op.drop_table('recovery_action_executions')
    op.drop_index(op.f('ix_recovery_actions_decision_result_id'), table_name='recovery_actions')
    op.drop_index(op.f('ix_recovery_actions_case_id'), table_name='recovery_actions')
    op.drop_table('recovery_actions')
