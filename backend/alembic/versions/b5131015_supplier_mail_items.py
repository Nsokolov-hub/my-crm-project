"""Positions of each request mailed to a supplier, and a reply recorded without prices.

Mails sent before this revision keep item_ids empty: their positions are read back from the mailed table.
"""

import sqlalchemy as sa

from alembic import op

revision = "b5131015"
down_revision = "b5131014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("supplier_mails", sa.Column("item_ids", sa.JSON(), nullable=True))
    op.add_column("supplier_mails", sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("supplier_mails", sa.Column("reply_note", sa.Text(), nullable=False, server_default=""))


def downgrade():
    with op.batch_alter_table("supplier_mails") as batch:
        batch.drop_column("reply_note")
        batch.drop_column("replied_at")
        batch.drop_column("item_ids")
