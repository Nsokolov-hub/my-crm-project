"""Ordered positions can be cancelled with a reason and move on to «Прибыло в РФ» and «Доставлено клиенту».

Business remarks of 18 October 2026. A cancelled order line frees its position for a new order, so the
unique execution becomes unique among live lines only. The new right supplier_orders.delivery lets a
logistician set delivery statuses; it goes to everyone who orders from suppliers and to the «Логист» role.
"""

from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "b5131017"
down_revision = "b5131016"
branch_labels = None
depends_on = None

DELIVERY = "supplier_orders.delivery"
LOGISTICIAN = "Логист"
ACTIVE_LINE = "status <> 'cancelled'"
# Names the unnamed unique constraint of b5131003 as PostgreSQL did, so SQLite batch mode can drop it too.
NAMING = {"uq": "%(table_name)s_%(column_0_name)s_key"}


def upgrade():
    with op.batch_alter_table("supplier_order_lines", naming_convention=NAMING) as batch:
        batch.add_column(sa.Column("cancel_reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("cancelled_by", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_supplier_order_lines_cancelled_by", "users", ["cancelled_by"], ["id"])
        batch.drop_constraint("supplier_order_lines_execution_id_key", type_="unique")
    op.create_index("uq_supplier_order_lines_active_execution", "supplier_order_lines", ["execution_id"],
                    unique=True, postgresql_where=sa.text(ACTIVE_LINE), sqlite_where=sa.text(ACTIVE_LINE))
    grant_delivery_right(op.get_bind())


def grant_delivery_right(conn):
    owners = [
        (user_id, role_id, scope)
        for user_id, role_id, scope in conn.execute(sa.text(
            "SELECT user_id, role_id, scope FROM permission_grants WHERE code = 'procurement.write' AND allow = :allow"
        ), {"allow": True})
    ]
    logistician = conn.execute(sa.text("SELECT id FROM roles WHERE name = :name"), {"name": LOGISTICIAN}).scalar()
    if logistician:
        owners.append((None, logistician, "all"))
    for user_id, role_id, scope in owners:
        owner = "user_id" if user_id is not None else "role_id"
        # A decision already taken on the new right, for instance a re-run, is kept.
        if conn.execute(sa.text(
            f"SELECT 1 FROM permission_grants WHERE {owner} = :owner AND code = :code"
        ), {"owner": user_id if user_id is not None else role_id, "code": DELIVERY}).first():
            continue
        conn.execute(sa.text(
            "INSERT INTO permission_grants (id, created_at, version, user_id, role_id, code, scope, allow) "
            "VALUES (:id, :now, 1, :user, :role, :code, :scope, :allow)"
        ), {"id": str(uuid4()), "now": datetime.now(timezone.utc), "user": user_id, "role": role_id,
            "code": DELIVERY, "scope": scope, "allow": True})


def downgrade():
    op.execute(sa.text("DELETE FROM permission_grants WHERE code = :code").bindparams(code=DELIVERY))
    op.drop_index("uq_supplier_order_lines_active_execution", table_name="supplier_order_lines")
    with op.batch_alter_table("supplier_order_lines") as batch:
        batch.create_unique_constraint("supplier_order_lines_execution_id_key", ["execution_id"])
        batch.drop_constraint("fk_supplier_order_lines_cancelled_by", type_="foreignkey")
        batch.drop_column("cancelled_by")
        batch.drop_column("cancelled_at")
        batch.drop_column("cancel_reason")
