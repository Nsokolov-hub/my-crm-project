"""Calendar, supplier orders, reviews, absences and group codes."""

from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "b5131003"
down_revision = "b5131002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users", sa.Column("own_requests_only", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column(
        "request_items", sa.Column("quote_only", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("product_groups", sa.Column("internal_code", sa.Integer(), nullable=True))
    connection = op.get_bind()
    groups = sa.table(
        "product_groups",
        sa.column("id", sa.String(36)),
        sa.column("name", sa.String(150)),
        sa.column("slug", sa.String(80)),
        sa.column("internal_code", sa.Integer()),
        sa.column("active", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("version", sa.Integer()),
    )
    records = list(
        connection.execute(sa.select(groups.c.id, groups.c.slug).order_by(groups.c.created_at, groups.c.id))
    )
    for index, row in enumerate(records, 1):
        connection.execute(groups.update().where(groups.c.id == row.id).values(internal_code=index))
    if not any(row.slug == "strains" for row in records):
        connection.execute(
            groups.insert().values(
                id=str(uuid4()),
                name="Штаммы",
                slug="strains",
                internal_code=len(records) + 1,
                active=True,
                version=1,
                created_at=datetime.now(timezone.utc),
            )
        )
    with op.batch_alter_table("product_groups") as batch:
        batch.alter_column("internal_code", existing_type=sa.Integer(), nullable=False)
        batch.create_unique_constraint("uq_product_groups_internal_code", ["internal_code"])
    op.create_table(
        "employee_absences",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("author_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("id", sa.String(36), nullable=False, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("ends_at > starts_at"),
    )
    op.create_index("ix_employee_absences_user_id", "employee_absences", ["user_id"], unique=False)
    op.create_table(
        "workflow_reviews",
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("request_id", sa.String(36), sa.ForeignKey("requests.id"), nullable=True),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(250), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("submitted_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decided_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(36), nullable=False, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("kind", "entity_id", "source_version"),
    )
    op.create_index("ix_workflow_reviews_request_id", "workflow_reviews", ["request_id"], unique=False)
    op.create_index("ix_workflow_reviews_entity_id", "workflow_reviews", ["entity_id"], unique=False)
    op.create_table(
        "supplier_orders",
        sa.Column("number", sa.String(80), nullable=False),
        sa.Column("supplier_id", sa.String(36), sa.ForeignKey("counterparties.id"), nullable=False),
        sa.Column("seller_id", sa.String(36), sa.ForeignKey("sellers.id"), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("total", sa.Numeric(24, 8), nullable=False),
        sa.Column("expected_date", sa.Date(), nullable=False),
        sa.Column("contract", sa.Text(), nullable=False),
        sa.Column("payment_terms", sa.Text(), nullable=False),
        sa.Column("delivery_terms", sa.Text(), nullable=False),
        sa.Column("author_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(36), nullable=False, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("number"),
    )
    op.create_index("ix_supplier_orders_supplier_id", "supplier_orders", ["supplier_id"], unique=False)
    op.create_table(
        "supplier_order_lines",
        sa.Column("order_id", sa.String(36), sa.ForeignKey("supplier_orders.id"), nullable=False),
        sa.Column("execution_id", sa.String(36), sa.ForeignKey("executions.id"), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 6), nullable=False),
        sa.Column("unit_price", sa.Numeric(24, 8), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(36), nullable=False, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity > 0 AND unit_price >= 0"),
        sa.UniqueConstraint("execution_id"),
    )
    op.create_index("ix_supplier_order_lines_order_id", "supplier_order_lines", ["order_id"], unique=False)
    op.create_table(
        "calendar_entries",
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("planned_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(24, 8), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("counterparty_id", sa.String(36), sa.ForeignKey("counterparties.id"), nullable=True),
        sa.Column("request_id", sa.String(36), sa.ForeignKey("requests.id"), nullable=True),
        sa.Column("supplier_order_id", sa.String(36), sa.ForeignKey("supplier_orders.id"), nullable=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("commercial_documents.id"), nullable=True),
        sa.Column("payment_kind", sa.String(20), nullable=False),
        sa.Column("recurrence", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("responsible_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("author_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("id", sa.String(36), nullable=False, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("amount > 0"),
    )
    op.create_index("ix_calendar_entries_planned_date", "calendar_entries", ["planned_date"], unique=False)
    op.create_index("ix_calendar_entries_request_id", "calendar_entries", ["request_id"], unique=False)
    op.create_table(
        "supplier_mails",
        sa.Column("request_id", sa.String(36), sa.ForeignKey("requests.id"), nullable=False),
        sa.Column("supplier_id", sa.String(36), sa.ForeignKey("counterparties.id"), nullable=False),
        sa.Column("recipient", sa.String(254), nullable=False),
        sa.Column("subject", sa.String(250), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("author_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("id", sa.String(36), nullable=False, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    )
    op.create_index("ix_supplier_mails_request_id", "supplier_mails", ["request_id"], unique=False)
    roles = sa.table("roles", sa.column("id", sa.String(36)), sa.column("name", sa.String(200)))
    grants = sa.table(
        "permission_grants",
        sa.column("id", sa.String(36)),
        sa.column("role_id", sa.String(36)),
        sa.column("code", sa.String(100)),
        sa.column("scope", sa.String(10)),
        sa.column("allow", sa.Boolean()),
        sa.column("version", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    extra = {
        "Руководитель": (
            "procurement.write calculations.write finance.purchase.read finance.calculations.read finance.reward.read finance.profit.read",
            "all",
        ),
        "Закупщик": ("procurement.write", "all"),
        "Менеджер продаж": (
            "calculations.write finance.purchase.read finance.calculations.read finance.reward.read finance.profit.read quotes.write",
            "own",
        ),
    }
    for role in connection.execute(sa.select(roles).where(roles.c.name.in_(extra))):
        codes, scope = extra[role.name]
        for code in codes.split():
            if not connection.scalar(
                sa.select(grants.c.id).where(grants.c.role_id == role.id, grants.c.code == code)
            ):
                connection.execute(
                    grants.insert().values(
                        id=str(uuid4()),
                        role_id=role.id,
                        code=code,
                        scope=scope,
                        allow=True,
                        version=1,
                        created_at=datetime.now(timezone.utc),
                    )
                )


def downgrade():
    op.drop_table("supplier_mails")
    op.drop_table("calendar_entries")
    op.drop_table("supplier_order_lines")
    op.drop_table("supplier_orders")
    op.drop_table("workflow_reviews")
    op.drop_table("employee_absences")
    with op.batch_alter_table("product_groups") as batch:
        batch.drop_constraint("uq_product_groups_internal_code", type_="unique")
        batch.drop_column("internal_code")
    op.drop_column("request_items", "quote_only")
    op.drop_column("users", "own_requests_only")
    op.execute("DELETE FROM permission_grants WHERE code = 'procurement.write'")
