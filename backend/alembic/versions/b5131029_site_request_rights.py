"""The «Руководитель» role gets the rights of the «Заявки с сайта» section.

The rights site_requests.read, .assign and .write came with the site integration (b5131024), but only roles created
after it got them, so on installed systems nobody saw the section and the site's requests waited unassigned. On
31 October 2026 the owner chose the leaders only (not sales managers): this role gets all three. A decision already
taken on one of these rights for the role is kept.
"""

from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "b5131029"
down_revision = "b5131028"
branch_labels = None
depends_on = None

LEADER = "Руководитель"
RIGHTS = ("site_requests.read", "site_requests.assign", "site_requests.write")


def upgrade():
    grant_leader_rights(op.get_bind())


def grant_leader_rights(conn):
    leader = conn.execute(sa.text("SELECT id FROM roles WHERE name = :name"), {"name": LEADER}).scalar()
    if not leader:
        return
    for code in RIGHTS:
        if conn.execute(sa.text("SELECT 1 FROM permission_grants WHERE role_id = :role AND code = :code"),
                        {"role": leader, "code": code}).first():
            continue
        conn.execute(sa.text(
            "INSERT INTO permission_grants (id, created_at, version, user_id, role_id, code, scope, allow) "
            "VALUES (:id, :now, 1, NULL, :role, :code, 'all', :allow)"
        ), {"id": str(uuid4()), "now": datetime.now(timezone.utc), "role": leader, "code": code, "allow": True})


def downgrade():
    # The rights may have been set by hand since: the downgrade leaves them.
    pass
