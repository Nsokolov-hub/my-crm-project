"""Separate client bases, immutable counterparty codes and staged table imports."""

import sqlalchemy as sa

from alembic import op

revision = "b5131001"
down_revision = "aa92c4915d1e"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("counterparties", sa.Column("internal_code", sa.Integer(), nullable=True))
    op.add_column(
        "counterparties", sa.Column("client_base", sa.String(20), nullable=False, server_default="working")
    )
    op.execute(
        "WITH numbered AS (SELECT id, row_number() OVER (ORDER BY created_at, id) AS code FROM counterparties) UPDATE counterparties SET internal_code = numbered.code FROM numbered WHERE counterparties.id = numbered.id"
    )
    # Preserve all existing live relationships. Only unused call-import prospects become cold.
    op.execute(
        "UPDATE counterparties SET client_base = 'cold' WHERE source = 'База обзвона' AND kind = 'client' AND NOT EXISTS (SELECT 1 FROM requests WHERE requests.client_id = counterparties.id) AND NOT EXISTS (SELECT 1 FROM contacts WHERE contacts.client_id = counterparties.id)"
    )
    with op.batch_alter_table("counterparties") as batch:
        batch.alter_column("internal_code", existing_type=sa.Integer(), nullable=False)
        batch.create_unique_constraint("uq_counterparties_internal_code", ["internal_code"])
        batch.create_check_constraint("ck_counterparties_internal_code", "internal_code > 0")
        batch.create_check_constraint("ck_counterparties_client_base", "client_base IN ('cold', 'working')")
        batch.create_index("ix_counterparties_client_base", ["client_base"])
    for name, kind in [
        ("department", sa.String(200)),
        ("purchase_area", sa.String(500)),
        ("comment", sa.Text()),
    ]:
        op.add_column("contacts", sa.Column(name, kind, nullable=True))
    for name, kind in [("manufacturer", sa.String(250)), ("purity", sa.String(200))]:
        op.add_column("nomenclatures", sa.Column(name, kind, nullable=True))
    op.add_column(
        "request_items", sa.Column("source_columns", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column("request_items", sa.Column("source_values", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column(
        "request_items", sa.Column("work_status", sa.String(20), nullable=False, server_default="requested")
    )
    op.execute(
        "UPDATE request_items SET work_status = 'in_progress' WHERE EXISTS (SELECT 1 FROM executions WHERE executions.item_id = request_items.id AND executions.quantity > executions.cancelled_quantity)"
    )
    op.create_table(
        "table_imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(36), sa.ForeignKey("requests.id"), nullable=False),
        sa.Column("author_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("source_name", sa.String(250), nullable=False),
        *[
            sa.Column(name, sa.JSON(), nullable=False)
            for name in ("file_metadata", "columns", "rows", "mapping", "plan", "summary", "result")
        ],
        sa.Column("status", sa.String(20), nullable=False),
    )
    op.create_index("ix_table_imports_request_id", "table_imports", ["request_id"])
    op.create_index("ix_table_imports_author_id", "table_imports", ["author_id"])
    for name in ("source_columns", "source_values"):
        op.alter_column("request_items", name, server_default=None)


def downgrade():
    op.drop_table("table_imports")
    for name in ("source_columns", "source_values", "work_status"):
        op.drop_column("request_items", name)
    for name in ("manufacturer", "purity"):
        op.drop_column("nomenclatures", name)
    for name in ("department", "purchase_area", "comment"):
        op.drop_column("contacts", name)
    with op.batch_alter_table("counterparties") as batch:
        batch.drop_index("ix_counterparties_client_base")
        batch.drop_constraint("ck_counterparties_client_base", type_="check")
        batch.drop_constraint("ck_counterparties_internal_code", type_="check")
        batch.drop_constraint("uq_counterparties_internal_code", type_="unique")
        batch.drop_column("internal_code")
        batch.drop_column("client_base")
