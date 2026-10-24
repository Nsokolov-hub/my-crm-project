"""The «Заявки с сайта» section: site requests, their lines, attribution and the site's service tokens.

Site integration, flow F1 (docs/site-integration.md). A customer's attachments become ordinary files owned
by the site request, so the files owner check gains one more column.
"""

import sqlalchemy as sa

from alembic import op

revision = "b5131024"
down_revision = "b5131021"
branch_labels = None
depends_on = None

OWNER_COLUMNS = ("request_id", "client_id", "chat_id", "wave_id", "quote_id", "calculation_id", "document_id",
                 "calendar_entry_id")


def owner_check(columns: tuple[str, ...]) -> str:
    return "(" + " + ".join(f"CASE WHEN {column} IS NULL THEN 0 ELSE 1 END" for column in columns) + ") = 1"


def entity_columns() -> list[sa.Column]:
    return [sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False)]


def upgrade():
    op.create_table(
        "integration_tokens", *entity_columns(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_integration_tokens_user_id", "integration_tokens", ["user_id"])
    op.create_table(
        "site_requests", *entity_columns(),
        sa.Column("number", sa.String(40), nullable=False, unique=True),
        sa.Column("site_request_id", sa.String(36), nullable=False, unique=True),
        sa.Column("site_number", sa.String(40), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("lang", sa.String(2), nullable=False),
        sa.Column("form_type", sa.String(40)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("page_url", sa.String(2000)),
        sa.Column("customer_name", sa.String(200), nullable=False),
        sa.Column("company", sa.String(250), nullable=False),
        sa.Column("inn", sa.String(12)),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("city", sa.String(200)),
        sa.Column("comment", sa.Text()),
        sa.Column("desired_date", sa.Date()),
        sa.Column("marketing_consent", sa.Boolean(), nullable=False),
        sa.Column("service", sa.JSON()),
        sa.Column("assignee_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("assigned_at", sa.DateTime(timezone=True)),
        sa.Column("request_id", sa.String(36), sa.ForeignKey("requests.id")),
        sa.Column("converted_at", sa.DateTime(timezone=True)),
        sa.Column("reject_reason", sa.Text()),
        sa.Column("rejected_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('new', 'in_work', 'converted', 'rejected')"),
        sa.CheckConstraint("kind IN ('product_rfq', 'one_click', 'service', 'general')"),
        sa.CheckConstraint("lang IN ('ru', 'en')"),
    )
    for column in ("site_number", "status", "company", "inn", "phone", "email", "assignee_id", "request_id"):
        op.create_index(f"ix_site_requests_{column}", "site_requests", [column])
    op.create_table(
        "site_request_items", *entity_columns(),
        sa.Column("site_request_id", sa.String(36), sa.ForeignKey("site_requests.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("site_product_id", sa.String(100)),
        sa.Column("crm_product_id", sa.String(36)),
        sa.Column("brand", sa.String(200)),
        sa.Column("catalog_number", sa.String(150)),
        sa.Column("cas", sa.String(30)),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("packaging", sa.String(200)),
        sa.Column("quantity", sa.Numeric(24, 6), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("analogue", sa.String(20), nullable=False),
        sa.Column("custom_packaging", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column("page_url", sa.String(2000)),
        sa.CheckConstraint("quantity > 0"),
        sa.CheckConstraint("analogue IN ('none', 'allowed', 'requested')"),
    )
    op.create_index("ix_site_request_items_site_request_id", "site_request_items", ["site_request_id"])
    touch = [sa.Column(f"{prefix}_{name}", sa.String(2000 if name in ("landing_url", "referrer") else 250))
             for prefix in ("first", "last")
             for name in ("landing_url", "referrer", "utm_source", "utm_medium", "utm_campaign", "utm_content",
                          "utm_term")]
    op.create_table(
        "site_request_attributions", *entity_columns(),
        sa.Column("site_request_id", sa.String(36), sa.ForeignKey("site_requests.id"), nullable=False, unique=True),
        sa.Column("channel", sa.String(20)),
        sa.Column("conversion_page_url", sa.String(2000)),
        *touch,
        sa.Column("first_at", sa.DateTime(timezone=True)),
        sa.Column("last_at", sa.DateTime(timezone=True)),
        sa.Column("yclid", sa.String(250)),
        sa.Column("gclid", sa.String(250)),
        sa.Column("ym_uid", sa.String(100)),
        sa.Column("ga_client_id", sa.String(100)),
        sa.CheckConstraint("channel IS NULL OR channel IN ('cpc', 'organic', 'referral', 'direct', 'email', 'social')"),
    )
    op.create_index("ix_site_request_attributions_channel", "site_request_attributions", ["channel"])

    checks = sa.inspect(op.get_bind()).get_check_constraints("files")
    owner = next(row for row in checks if "CASE" in row["sqltext"].upper() and "request_id" in row["sqltext"])
    with op.batch_alter_table("files", naming_convention={"ck": "ck_%(table_name)s_%(column_0_name)s"}) as batch:
        batch.add_column(sa.Column("site_request_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_files_site_request", "site_requests", ["site_request_id"], ["id"])
        batch.drop_constraint(owner["name"] or "ck_files_", type_="check")
        batch.create_check_constraint("ck_files_one_owner", owner_check((*OWNER_COLUMNS, "site_request_id")))
        batch.create_index("ix_files_site_request_id", ["site_request_id"])


def downgrade():
    # Site requests are customer leads: keep them unless a reviewed backup replaces the database.
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM site_requests")).scalar():
        raise RuntimeError("Site requests exist; roll application code forward or restore a reviewed backup")
    with op.batch_alter_table("files", naming_convention={"ck": "ck_%(table_name)s_%(column_0_name)s"}) as batch:
        batch.drop_index("ix_files_site_request_id")
        batch.drop_constraint("ck_files_one_owner", type_="check")
        batch.create_check_constraint("ck_files_one_owner", owner_check(OWNER_COLUMNS))
        batch.drop_constraint("fk_files_site_request", type_="foreignkey")
        batch.drop_column("site_request_id")
    op.drop_table("site_request_attributions")
    op.drop_table("site_request_items")
    op.drop_table("site_requests")
    op.drop_table("integration_tokens")
