"""Sequential approval and immutable quote replacement for price reductions."""

import sqlalchemy as sa

from alembic import op

revision = "b5131034"
down_revision = "b5131033"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "price_reduction_requests",
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="pending_leader", nullable=False),
        sa.Column("leader_id", sa.String(length=36), nullable=False),
        sa.Column("manager_id", sa.String(length=36), nullable=False),
        sa.Column("author_id", sa.String(length=36), nullable=False),
        sa.Column("client_due_date", sa.Date(), nullable=False),
        sa.Column("comment", sa.Text(), server_default="", nullable=False),
        sa.Column("source_calculation_id", sa.String(length=36), nullable=True),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("mail_subject", sa.String(length=250), server_default="", nullable=False),
        sa.Column("mail_body", sa.Text(), server_default="", nullable=False),
        sa.Column("result_map", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["leader_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["manager_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["request_id"], ["requests.id"]),
        sa.ForeignKeyConstraint(["source_calculation_id"], ["calculations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_price_reduction_requests_request_id", "price_reduction_requests", ["request_id"])
    op.create_index("ix_price_reduction_requests_status", "price_reduction_requests", ["status"])
    op.create_index("ix_price_reduction_requests_leader_id", "price_reduction_requests", ["leader_id"])
    op.create_index("ix_price_reduction_requests_manager_id", "price_reduction_requests", ["manager_id"])
    op.create_index(
        "ix_price_reduction_requests_request_status",
        "price_reduction_requests",
        ["request_id", "status"],
    )
    op.add_column("workflow_reviews", sa.Column("reviewer_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_workflow_reviews_reviewer_id_users", "workflow_reviews", "users", ["reviewer_id"], ["id"]
    )
    op.create_index("ix_workflow_reviews_reviewer_id", "workflow_reviews", ["reviewer_id"])
    op.add_column("quote_items", sa.Column("previous_id", sa.String(length=36), nullable=True))
    op.add_column("quote_items", sa.Column("price_reduction_id", sa.String(length=36), nullable=True))
    op.add_column(
        "quote_items", sa.Column("archive_status", sa.String(length=20), server_default="active", nullable=False)
    )
    op.create_foreign_key("fk_quote_items_previous_id_quote_items", "quote_items", "quote_items", ["previous_id"], ["id"])
    op.create_foreign_key(
        "fk_quote_items_price_reduction_id", "quote_items", "price_reduction_requests", ["price_reduction_id"], ["id"]
    )
    op.create_index("ix_quote_items_previous_id", "quote_items", ["previous_id"], unique=True)
    op.create_index("ix_quote_items_price_reduction_id", "quote_items", ["price_reduction_id"])
    op.add_column("supplier_mails", sa.Column("kind", sa.String(length=30), server_default="rfq", nullable=False))
    op.add_column("supplier_mails", sa.Column("price_reduction_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_supplier_mails_price_reduction_id",
        "supplier_mails",
        "price_reduction_requests",
        ["price_reduction_id"],
        ["id"],
    )
    op.create_index("ix_supplier_mails_kind", "supplier_mails", ["kind"])
    op.create_index("ix_supplier_mails_price_reduction_id", "supplier_mails", ["price_reduction_id"])


def downgrade():
    op.drop_index("ix_supplier_mails_price_reduction_id", table_name="supplier_mails")
    op.drop_index("ix_supplier_mails_kind", table_name="supplier_mails")
    op.drop_constraint("fk_supplier_mails_price_reduction_id", "supplier_mails", type_="foreignkey")
    op.drop_column("supplier_mails", "price_reduction_id")
    op.drop_column("supplier_mails", "kind")
    op.drop_index("ix_quote_items_price_reduction_id", table_name="quote_items")
    op.drop_index("ix_quote_items_previous_id", table_name="quote_items")
    op.drop_constraint("fk_quote_items_price_reduction_id", "quote_items", type_="foreignkey")
    op.drop_constraint("fk_quote_items_previous_id_quote_items", "quote_items", type_="foreignkey")
    op.drop_column("quote_items", "archive_status")
    op.drop_column("quote_items", "price_reduction_id")
    op.drop_column("quote_items", "previous_id")
    op.drop_index("ix_workflow_reviews_reviewer_id", table_name="workflow_reviews")
    op.drop_constraint("fk_workflow_reviews_reviewer_id_users", "workflow_reviews", type_="foreignkey")
    op.drop_column("workflow_reviews", "reviewer_id")
    op.drop_index("ix_price_reduction_requests_request_status", table_name="price_reduction_requests")
    op.drop_index("ix_price_reduction_requests_manager_id", table_name="price_reduction_requests")
    op.drop_index("ix_price_reduction_requests_leader_id", table_name="price_reduction_requests")
    op.drop_index("ix_price_reduction_requests_status", table_name="price_reduction_requests")
    op.drop_index("ix_price_reduction_requests_request_id", table_name="price_reduction_requests")
    op.drop_table("price_reduction_requests")
