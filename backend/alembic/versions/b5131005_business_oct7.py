"""Archive mistaken quotes without losing history; save supplier mail copies."""

import sqlalchemy as sa

from alembic import op

revision = "b5131005"
down_revision = "b5131004"
branch_labels = None
depends_on = None


def quote_history_triggers(archivable):
    if op.get_bind().dialect.name != "postgresql":
        return
    arguments = "'archived', 'version'" if archivable else ""
    for table in ("quote_items", "quote_sheets"):
        op.execute(f"DROP TRIGGER crm_history_row ON {table}")
        op.execute(f"CREATE TRIGGER crm_history_row BEFORE UPDATE OR DELETE ON {table} "
                   f"FOR EACH ROW EXECUTE FUNCTION crm_protect_history({arguments})")


def upgrade():
    for table in ("quote_items", "quote_sheets"):
        op.add_column(table, sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("supplier_mails", sa.Column("cc", sa.JSON(), nullable=False, server_default="[]"))
    quote_history_triggers(True)


def downgrade():
    quote_history_triggers(False)
    op.drop_column("supplier_mails", "cc")
    for table in ("quote_items", "quote_sheets"):
        op.drop_column(table, "archived")
