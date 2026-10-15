"""Every analytics report but «Продажи по менеджерам» needs the new right analytics.reports.

Business remark of 15 October 2026: a sales manager sees only the sales by managers report. Every role and
user that reads analytics keeps all reports, except the standard «Менеджер продаж» role.
"""

from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "b5131016"
down_revision = "b5131015"
branch_labels = None
depends_on = None

SALES_MANAGER = "Менеджер продаж"


def upgrade():
    conn = op.get_bind()
    sales_role = conn.execute(sa.text("SELECT id FROM roles WHERE name = :name"), {"name": SALES_MANAGER}).scalar()
    holders = conn.execute(sa.text(
        "SELECT user_id, role_id, scope FROM permission_grants WHERE code = 'analytics.read' AND allow = :allow"
    ), {"allow": True}).all()
    for user_id, role_id, scope in holders:
        if role_id is not None and role_id == sales_role:
            continue
        owner = "user_id" if user_id is not None else "role_id"
        # A decision already taken on the new right, for instance a re-run, is kept.
        if conn.execute(sa.text(
            f"SELECT 1 FROM permission_grants WHERE {owner} = :owner AND code = 'analytics.reports'"
        ), {"owner": user_id if user_id is not None else role_id}).first():
            continue
        conn.execute(sa.text(
            "INSERT INTO permission_grants (id, created_at, version, user_id, role_id, code, scope, allow) "
            "VALUES (:id, :now, 1, :user, :role, 'analytics.reports', :scope, :allow)"
        ), {"id": str(uuid4()), "now": datetime.now(timezone.utc), "user": user_id, "role": role_id,
            "scope": scope, "allow": True})


def downgrade():
    op.execute("DELETE FROM permission_grants WHERE code = 'analytics.reports'")
