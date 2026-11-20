"""Personal supplier shortcuts and explicit transport metadata of nomenclatures."""

import sqlalchemy as sa

from alembic import op

revision = 'b5131032'
down_revision = 'b5131031'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('nomenclatures', sa.Column('storage_temperature', sa.String(80), nullable=True))
    op.add_column('nomenclatures', sa.Column('transport_hazard', sa.String(32), nullable=True))
    op.create_table('supplier_favorites',
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('supplier_id', sa.String(36), sa.ForeignKey('counterparties.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('user_id', 'supplier_id'))
    op.create_index('ix_supplier_favorites_supplier_id', 'supplier_favorites', ['supplier_id'])


def downgrade():
    op.drop_index('ix_supplier_favorites_supplier_id', table_name='supplier_favorites')
    op.drop_table('supplier_favorites')
    op.drop_column('nomenclatures', 'transport_hazard')
    op.drop_column('nomenclatures', 'storage_temperature')
