"""Link planned supplier waves to requests before calculation.

Revision ID: aa92c4915d1e
Revises: d2cfe4b7a901
"""

import sqlalchemy as sa

from alembic import op

revision = "aa92c4915d1e"
down_revision = "d2cfe4b7a901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("waves") as batch:
        batch.add_column(sa.Column("supplier_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_waves_supplier_id", "counterparties", ["supplier_id"], ["id"])
        batch.create_index("ix_waves_supplier_id", ["supplier_id"])
    with op.batch_alter_table("requests") as batch:
        batch.add_column(sa.Column("wave_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_requests_wave_id", "waves", ["wave_id"], ["id"])
        batch.create_index("ix_requests_wave_id", ["wave_id"])


def downgrade() -> None:
    with op.batch_alter_table("requests") as batch:
        batch.drop_index("ix_requests_wave_id")
        batch.drop_constraint("fk_requests_wave_id", type_="foreignkey")
        batch.drop_column("wave_id")
    with op.batch_alter_table("waves") as batch:
        batch.drop_index("ix_waves_supplier_id")
        batch.drop_constraint("fk_waves_supplier_id", type_="foreignkey")
        batch.drop_column("supplier_id")
