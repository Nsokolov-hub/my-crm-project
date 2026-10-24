"""Where a request came from: «Сайт» for requests created from site requests (TZ of the site, 9.4)."""

import sqlalchemy as sa

from alembic import op

revision = "b5131025"
down_revision = "b5131024"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("requests") as batch:
        batch.add_column(sa.Column("source", sa.String(250), nullable=True))


def downgrade():
    with op.batch_alter_table("requests") as batch:
        batch.drop_column("source")
