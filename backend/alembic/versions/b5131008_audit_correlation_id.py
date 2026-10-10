"""Name the audit HTTP correlation code apart from CRM request identifiers."""

from alembic import op

revision = "b5131008"
down_revision = "b5131007"
branch_labels = None
depends_on = None


def rename(old: str, new: str) -> None:
    # A column rename is a catalog change: no rows are rewritten and the history trigger does not fire.
    op.alter_column("audit_events", old, new_column_name=new)
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"ALTER INDEX ix_audit_events_{old} RENAME TO ix_audit_events_{new}")
    else:
        op.drop_index(f"ix_audit_events_{old}", table_name="audit_events")
        op.create_index(f"ix_audit_events_{new}", "audit_events", [new])


def upgrade():
    rename("request_id", "correlation_id")


def downgrade():
    rename("correlation_id", "request_id")
