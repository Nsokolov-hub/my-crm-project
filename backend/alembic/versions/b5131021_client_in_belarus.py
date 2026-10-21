"""A client located in the Republic of Belarus is sold to with VAT 0 %.

Business remarks of 21 October 2026. Under Annex 18 to the Treaty on the EAEU the place of sale is the buyer's
state, so proposals and invoices for such a client carry VAT 0 % and the note of the annex, and a calculation
with VAT deduction is not offered.
"""

import sqlalchemy as sa

from alembic import op

revision = "b5131021"
down_revision = "b5131017"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("counterparties") as batch:
        batch.add_column(sa.Column("in_belarus", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("counterparties") as batch:
        batch.drop_column("in_belarus")
