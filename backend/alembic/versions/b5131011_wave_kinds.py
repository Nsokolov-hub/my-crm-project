"""Planned and working waves, and an archive for the waves no longer in use."""

import sqlalchemy as sa

from alembic import op

revision = "b5131011"
down_revision = "b5131010"
branch_labels = None
depends_on = None

ACTIVE_PLAN = "kind = 'plan' AND NOT archived"


def upgrade():
    # Existing waves already take real orders, so they become working waves and behave as before.
    op.add_column("waves", sa.Column("kind", sa.String(10), nullable=False, server_default="work"))
    op.add_column("waves", sa.Column("plan_id", sa.String(36), nullable=True))
    op.add_column("waves", sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("waves") as batch:
        batch.create_foreign_key("fk_waves_plan", "waves", ["plan_id"], ["id"])
        batch.create_check_constraint("ck_waves_kind", "kind IN ('plan', 'work')")
    op.create_index("ix_waves_plan_id", "waves", ["plan_id"])
    op.create_index("uq_waves_active_plan", "waves", ["supplier_id"], unique=True,
                    postgresql_where=sa.text(ACTIVE_PLAN), sqlite_where=sa.text(ACTIVE_PLAN))


def downgrade():
    op.drop_index("uq_waves_active_plan", table_name="waves")
    op.drop_index("ix_waves_plan_id", table_name="waves")
    with op.batch_alter_table("waves") as batch:
        batch.drop_constraint("ck_waves_kind", type_="check")
        batch.drop_constraint("fk_waves_plan", type_="foreignkey")
        batch.drop_column("archived")
        batch.drop_column("plan_id")
        batch.drop_column("kind")
