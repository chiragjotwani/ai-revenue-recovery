"""case feature vectors table

Revision ID: b1c4e9a72f38
Revises: 9a3e7b5c1d24
Create Date: 2026-09-02 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b1c4e9a72f38'
down_revision: Union[str, None] = '9a3e7b5c1d24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'case_feature_vectors',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('case_id', sa.UUID(), nullable=False),
        sa.Column('diagnosis_id', sa.UUID(), nullable=False),
        sa.Column('features', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('feature_version', sa.String(length=10), nullable=False),
        sa.Column(
            'computed_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['case_id'], ['recovery_cases.id']),
        sa.ForeignKeyConstraint(['diagnosis_id'], ['diagnoses.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'case_id', 'diagnosis_id', name='uq_case_feature_vectors_identity'
        ),
    )
    op.create_index(
        op.f('ix_case_feature_vectors_case_id'), 'case_feature_vectors', ['case_id'], unique=False
    )
    op.create_index(
        op.f('ix_case_feature_vectors_diagnosis_id'),
        'case_feature_vectors',
        ['diagnosis_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_case_feature_vectors_diagnosis_id'), table_name='case_feature_vectors'
    )
    op.drop_index(op.f('ix_case_feature_vectors_case_id'), table_name='case_feature_vectors')
    op.drop_table('case_feature_vectors')
