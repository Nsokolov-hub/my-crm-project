"""Site integration: «Публиковать на сайте» and the site's card of a nomenclature (flow F4), subscribers of the
site's mailing as contacts for mailings."""

import sqlalchemy as sa

from alembic import op

revision = "b5131027"
down_revision = "b5131026"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("nomenclatures") as batch:
        batch.add_column(sa.Column("publish_to_site", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.add_column(sa.Column("site_product_id", sa.String(length=100), nullable=True))
        batch.create_index("ix_nomenclatures_site_product_id", ["site_product_id"])
    op.create_table(
        "site_subscribers",
        sa.Column("site_subscriber_id", sa.String(length=36), nullable=False),
        sa.Column("site_version", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("lang", sa.String(length=2), nullable=False),
        sa.Column("subscribed", sa.Boolean(), nullable=False),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=True),
        sa.Column("page_url", sa.String(length=2000), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("site_subscriber_id"),
    )
    op.create_index("ix_site_subscribers_email", "site_subscribers", ["email"])
    op.create_index("ix_site_subscribers_subscribed", "site_subscribers", ["subscribed"])


def downgrade():
    op.drop_index("ix_site_subscribers_subscribed", table_name="site_subscribers")
    op.drop_index("ix_site_subscribers_email", table_name="site_subscribers")
    op.drop_table("site_subscribers")
    with op.batch_alter_table("nomenclatures") as batch:
        batch.drop_index("ix_nomenclatures_site_product_id")
        batch.drop_column("site_product_id")
        batch.drop_column("publish_to_site")
