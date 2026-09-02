"""revenue measurements table

Revision ID: 4d8f0a2c6b91
Revises: 7c1b9e4f2a83
Create Date: 2026-09-01 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '4d8f0a2c6b91'
down_revision: Union[str, None] = '7c1b9e4f2a83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'revenue_measurements',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('case_id', sa.UUID(), nullable=False),
        sa.Column('payment_id', sa.UUID(), nullable=False),
        sa.Column('outcome_observation_id', sa.UUID(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column(
            'measured_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['case_id'], ['recovery_cases.id']),
        sa.ForeignKeyConstraint(['payment_id'], ['payments.id']),
        sa.ForeignKeyConstraint(
            ['outcome_observation_id'], ['recovery_outcome_observations.id']
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'case_id', 'outcome_observation_id', name='uq_revenue_measurements_identity'
        ),
    )
    op.create_index(
        op.f('ix_revenue_measurements_case_id'), 'revenue_measurements', ['case_id'], unique=False
    )
    op.create_index(
        op.f('ix_revenue_measurements_payment_id'),
        'revenue_measurements',
        ['payment_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_revenue_measurements_outcome_observation_id'),
        'revenue_measurements',
        ['outcome_observation_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_revenue_measurements_outcome_observation_id'),
        table_name='revenue_measurements',
    )
    op.drop_index(op.f('ix_revenue_measurements_payment_id'), table_name='revenue_measurements')
    op.drop_index(op.f('ix_revenue_measurements_case_id'), table_name='revenue_measurements')
    op.drop_table('revenue_measurements')
