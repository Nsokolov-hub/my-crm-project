"""A duplicate counterparty merged into its main card, and requests archived together with their client."""

import sqlalchemy as sa

from alembic import op

revision = "b5131012"
down_revision = "b5131011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("counterparties", sa.Column("merged_into_id", sa.String(36), nullable=True))
    with op.batch_alter_table("counterparties") as batch:
        batch.create_foreign_key("fk_counterparties_merged_into", "counterparties", ["merged_into_id"], ["id"])
    # Requests already archived stay archived when their client returns from the archive.
    op.add_column("requests", sa.Column("archived_with_client", sa.Boolean(), nullable=False,
                                        server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("requests") as batch:
        batch.drop_column("archived_with_client")
    with op.batch_alter_table("counterparties") as batch:
        batch.drop_constraint("fk_counterparties_merged_into", type_="foreignkey")
        batch.drop_column("merged_into_id")
