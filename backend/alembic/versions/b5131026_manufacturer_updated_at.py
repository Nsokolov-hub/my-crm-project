"""When a manufacturer or its spellings changed: the site loads the registry by it (flow F5)."""

import sqlalchemy as sa

from alembic import op

revision = "b5131026"
down_revision = "b5131025"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("manufacturers") as batch:
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE manufacturers SET updated_at = created_at")
    with op.batch_alter_table("manufacturers") as batch:
        batch.alter_column("updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.create_index("ix_manufacturers_updated_at", ["updated_at"])


def downgrade():
    with op.batch_alter_table("manufacturers") as batch:
        batch.drop_index("ix_manufacturers_updated_at")
        batch.drop_column("updated_at")
