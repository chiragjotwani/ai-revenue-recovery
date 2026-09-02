"""diagnosis router escalation columns

Revision ID: 9a3e7b5c1d24
Revises: 4d8f0a2c6b91
Create Date: 2026-09-02 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '9a3e7b5c1d24'
down_revision: Union[str, None] = '4d8f0a2c6b91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'diagnoses',
        sa.Column(
            'router_escalated', sa.Boolean(), nullable=False, server_default=sa.text('false')
        ),
    )
    op.add_column(
        'diagnoses', sa.Column('router_escalation_reason', sa.String(length=255), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('diagnoses', 'router_escalation_reason')
    op.drop_column('diagnoses', 'router_escalated')
