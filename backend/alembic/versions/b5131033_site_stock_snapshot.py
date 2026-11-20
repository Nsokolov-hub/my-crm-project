"""Keep stock evidence from a site request through conversion into a CRM request."""

import sqlalchemy as sa

from alembic import op

revision = 'b5131033'
down_revision = 'b5131032'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('site_request_items', sa.Column('site_packaging_id', sa.String(150), nullable=True))
    op.add_column('site_request_items', sa.Column('stock_snapshot', sa.JSON(), nullable=True))
    op.add_column('request_items', sa.Column('stock_snapshot', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('request_items', 'stock_snapshot')
    op.drop_column('site_request_items', 'stock_snapshot')
    op.drop_column('site_request_items', 'site_packaging_id')
