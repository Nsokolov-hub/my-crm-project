"""s08_execution_revisions

Revision ID: 3f6d74c810d2
Revises: 1ea5852eaf70
Create Date: 2026-09-23 11:22:00.000000

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = '3f6d74c810d2'
down_revision = '1ea5852eaf70'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('executions', sa.Column('previous_id', sa.String(length=36), nullable=True))
    op.add_column('executions', sa.Column('cancel_reason', sa.Text(), nullable=True))
    op.create_foreign_key('fk_executions_previous_id', 'executions', 'executions', ['previous_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('fk_executions_previous_id', 'executions', type_='foreignkey')
    op.drop_column('executions', 'cancel_reason')
    op.drop_column('executions', 'previous_id')
