"""Schedule outbox retries in the database instead of re-checking backoff in the worker."""

import sqlalchemy as sa

from alembic import op

revision = "b5131009"
down_revision = "b5131008"
branch_labels = None
depends_on = None


def upgrade():
    # Pending events keep NULL and are picked up on the next worker cycle, as before.
    op.add_column("outbox_events", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_outbox_events_next_attempt_at", "outbox_events", ["next_attempt_at"])


def downgrade():
    op.drop_index("ix_outbox_events_next_attempt_at", table_name="outbox_events")
    op.drop_column("outbox_events", "next_attempt_at")
