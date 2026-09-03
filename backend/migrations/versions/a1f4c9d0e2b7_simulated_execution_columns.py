"""simulated execution columns

Revision ID: a1f4c9d0e2b7
Revises: 2620086aa88c
Create Date: 2026-09-03 22:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1f4c9d0e2b7'
down_revision: Union[str, None] = '2620086aa88c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'recovery_action_executions', sa.Column('detail', sa.String(length=300), nullable=True)
    )
    op.add_column(
        'recovery_action_executions',
        sa.Column('simulated_reference', sa.String(length=200), nullable=True),
    )
    op.add_column(
        'recovery_action_executions',
        sa.Column('resulting_payment_id', sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        'fk_recovery_action_executions_resulting_payment_id',
        'recovery_action_executions',
        'payments',
        ['resulting_payment_id'],
        ['id'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'fk_recovery_action_executions_resulting_payment_id',
        'recovery_action_executions',
        type_='foreignkey',
    )
    op.drop_column('recovery_action_executions', 'resulting_payment_id')
    op.drop_column('recovery_action_executions', 'simulated_reference')
    op.drop_column('recovery_action_executions', 'detail')
