"""Leaders issue documents in every request: the Руководитель role gets documents.write for all requests.

Before, only sales managers could issue proposals, record a client's acceptance or issue invoices, and
only in their own requests; a leader could approve an order but not accept the composition behind it.
"""

from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "b5131013"
down_revision = "b5131012"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    role_id = conn.execute(sa.text("SELECT id FROM roles WHERE name = 'Руководитель'")).scalar()
    # An installation without the standard role, or with its own decision on this right, is left as it is.
    if not role_id or conn.execute(sa.text(
        "SELECT 1 FROM permission_grants WHERE role_id = :role AND code = 'documents.write'"
    ), {"role": role_id}).first():
        return
    conn.execute(sa.text(
        "INSERT INTO permission_grants (id, created_at, version, role_id, code, scope, allow) "
        "VALUES (:id, :now, 1, :role, 'documents.write', 'all', :allow)"
    ), {"id": str(uuid4()), "now": datetime.now(timezone.utc), "role": role_id, "allow": True})


def downgrade():
    # The grant cannot be told apart from one an administrator adds later, so it stays.
    pass
