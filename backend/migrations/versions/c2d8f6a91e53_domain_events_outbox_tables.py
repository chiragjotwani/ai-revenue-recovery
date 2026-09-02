"""domain events outbox tables

Revision ID: c2d8f6a91e53
Revises: b1c4e9a72f38
Create Date: 2026-09-02 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c2d8f6a91e53'
down_revision: Union[str, None] = 'b1c4e9a72f38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'domain_events',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('aggregate_id', sa.UUID(), nullable=False),
        sa.Column('aggregate_type', sa.String(length=50), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('schema_version', sa.String(length=10), nullable=False),
        sa.Column('source', sa.String(length=100), nullable=False),
        sa.Column('correlation_id', sa.UUID(), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_domain_events_event_type'), 'domain_events', ['event_type'], unique=False
    )
    op.create_index(
        op.f('ix_domain_events_aggregate_id'), 'domain_events', ['aggregate_id'], unique=False
    )
    op.create_index(
        op.f('ix_domain_events_correlation_id'), 'domain_events', ['correlation_id'], unique=False
    )
    # The relay's own query ("unpublished rows, oldest first") is the hot
    # path -- a partial index keeps it cheap even as the table grows,
    # since published rows (the overwhelming majority in steady state)
    # never need to be scanned again.
    op.create_index(
        'ix_domain_events_unpublished',
        'domain_events',
        ['created_at'],
        unique=False,
        postgresql_where=sa.text('published_at IS NULL'),
    )

    op.create_table(
        'processed_events',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('event_id', sa.UUID(), nullable=False),
        sa.Column('consumer_group', sa.String(length=100), nullable=False),
        sa.Column(
            'processed_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'consumer_group', name='uq_processed_events_identity'),
    )
    op.create_index(
        op.f('ix_processed_events_event_id'), 'processed_events', ['event_id'], unique=False
    )

    op.create_table(
        'dead_letter_events',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('event_id', sa.UUID(), nullable=False),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('aggregate_id', sa.UUID(), nullable=False),
        sa.Column('consumer_group', sa.String(length=100), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('error', sa.Text(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_dead_letter_events_event_id'), 'dead_letter_events', ['event_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_dead_letter_events_event_id'), table_name='dead_letter_events')
    op.drop_table('dead_letter_events')
    op.drop_index(op.f('ix_processed_events_event_id'), table_name='processed_events')
    op.drop_table('processed_events')
    op.drop_index('ix_domain_events_unpublished', table_name='domain_events')
    op.drop_index(op.f('ix_domain_events_correlation_id'), table_name='domain_events')
    op.drop_index(op.f('ix_domain_events_aggregate_id'), table_name='domain_events')
    op.drop_index(op.f('ix_domain_events_event_type'), table_name='domain_events')
    op.drop_table('domain_events')
