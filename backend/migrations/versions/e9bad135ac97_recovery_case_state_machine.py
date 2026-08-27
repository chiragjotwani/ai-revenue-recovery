"""recovery case state machine

Revision ID: e9bad135ac97
Revises: d54257564c3a
Create Date: 2026-08-27 12:51:04.413458

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e9bad135ac97'
down_revision: Union[str, None] = 'd54257564c3a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The Postgres enum type is shared by three columns across two tables, so it
# is created once explicitly here and referenced with create_type=False on
# each column (otherwise each column would emit its own CREATE TYPE and the
# second would fail with "type already exists").
recovery_case_state = postgresql.ENUM(
    'DETECTED',
    'DIAGNOSING',
    'DIAGNOSED',
    'DECISION_PENDING',
    'ACTION_SCHEDULED',
    'ACTION_EXECUTED',
    'OBSERVING',
    'RECOVERED',
    'ABANDONED',
    'FAILED',
    name='recovery_case_state',
)


def upgrade() -> None:
    bind = op.get_bind()
    recovery_case_state.create(bind, checkfirst=True)

    enum_ref = postgresql.ENUM(name='recovery_case_state', create_type=False)

    op.create_table(
        'recovery_cases',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('payment_id', sa.UUID(), nullable=False),
        sa.Column('customer_id', sa.UUID(), nullable=False),
        sa.Column('state', enum_ref, nullable=False),
        sa.Column(
            'opened_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id']),
        sa.ForeignKeyConstraint(['payment_id'], ['payments.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_recovery_cases_customer_id'), 'recovery_cases', ['customer_id'], unique=False
    )
    op.create_index(
        op.f('ix_recovery_cases_payment_id'), 'recovery_cases', ['payment_id'], unique=True
    )
    op.create_index(op.f('ix_recovery_cases_state'), 'recovery_cases', ['state'], unique=False)

    op.create_table(
        'recovery_case_transitions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('case_id', sa.UUID(), nullable=False),
        sa.Column('from_state', enum_ref, nullable=True),
        sa.Column('to_state', enum_ref, nullable=False),
        sa.Column('reason', sa.String(length=255), nullable=True),
        sa.Column('actor', sa.String(length=100), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.ForeignKeyConstraint(['case_id'], ['recovery_cases.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_recovery_case_transitions_case_id'),
        'recovery_case_transitions',
        ['case_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_recovery_case_transitions_case_id'), table_name='recovery_case_transitions'
    )
    op.drop_table('recovery_case_transitions')
    op.drop_index(op.f('ix_recovery_cases_state'), table_name='recovery_cases')
    op.drop_index(op.f('ix_recovery_cases_payment_id'), table_name='recovery_cases')
    op.drop_index(op.f('ix_recovery_cases_customer_id'), table_name='recovery_cases')
    op.drop_table('recovery_cases')
    postgresql.ENUM(name='recovery_case_state', create_type=False).drop(op.get_bind(), checkfirst=True)
