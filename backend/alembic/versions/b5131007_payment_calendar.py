"""Actual payment dates, dated balances, invoice attachments and refusal closure."""

import sqlalchemy as sa

from alembic import op
from app.core.config import settings

revision = "b5131007"
down_revision = "b5131006"
branch_labels = None
depends_on = None

OWNER_CHECK = "(" + " + ".join(
    f"CASE WHEN {column} IS NULL THEN 0 ELSE 1 END" for column in (
        "request_id", "client_id", "chat_id", "wave_id", "quote_id", "calculation_id", "document_id", "calendar_entry_id"
    )
) + ") = 1"


def upgrade():
    op.add_column("calendar_entries", sa.Column("actual_date", sa.Date(), nullable=True))
    op.add_column("calendar_entries", sa.Column("outside_payment_days", sa.Boolean(), nullable=False,
                                                server_default=sa.false()))
    op.create_index("ix_calendar_entries_actual_date", "calendar_entries", ["actual_date"])
    # Old confirmations recorded only a timestamp. Preserve its company-local
    # day as the best available fact; do not invent actual dates for plans.
    timezone = settings.company_timezone
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("UPDATE calendar_entries SET actual_date = "
                           "CAST(timezone(:zone, confirmed_at) AS date) "
                           "WHERE status = 'confirmed' AND confirmed_at IS NOT NULL").bindparams(zone=timezone))
    else:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        conn = op.get_bind()
        for row in conn.execute(sa.text("SELECT id, confirmed_at FROM calendar_entries WHERE status='confirmed'")):
            if row.confirmed_at:
                stamp = datetime.fromisoformat(str(row.confirmed_at))
                from datetime import timezone as utc
                day = stamp.replace(tzinfo=stamp.tzinfo or utc.utc).astimezone(ZoneInfo(timezone)).date()
                conn.execute(sa.text("UPDATE calendar_entries SET actual_date=:day WHERE id=:id"),
                             {"day": day, "id": row.id})
    op.create_table("calendar_balances",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("balance_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount", sa.Numeric(24, 8), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("author_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.UniqueConstraint("currency", "balance_date"))
    op.create_index("ix_calendar_balances_balance_date", "calendar_balances", ["balance_date"])
    checks = sa.inspect(op.get_bind()).get_check_constraints("files")
    owner = next(row for row in checks if "CASE" in row["sqltext"].upper() and "request_id" in row["sqltext"])
    with op.batch_alter_table("files", naming_convention={"ck": "ck_%(table_name)s_%(column_0_name)s"}) as batch:
        batch.add_column(sa.Column("calendar_entry_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_files_calendar_entry", "calendar_entries", ["calendar_entry_id"], ["id"])
        batch.drop_constraint(owner["name"] or "ck_files_", type_="check")
        batch.create_check_constraint("ck_files_one_owner", OWNER_CHECK)
        batch.create_index("ix_files_calendar_entry_id", ["calendar_entry_id"])
    op.execute(sa.text("""
        UPDATE requests SET commercial_stage='closed_lost', closed_at=COALESCE(closed_at, CURRENT_TIMESTAMP),
                            version=version+1
        WHERE loss_reason IS NOT NULL AND TRIM(loss_reason) != '' AND sale_confirmed_at IS NULL
          AND commercial_stage IN ('new','clarification','collecting_quotes','quotes','quote_given','calculation','proposal_sent')
          AND NOT EXISTS (SELECT 1 FROM executions e WHERE e.request_id=requests.id AND e.quantity > e.cancelled_quantity)
    """))


def downgrade():
    # Do not discard payment facts or their invoices during a code rollback.
    raise RuntimeError("Payment facts must be retained; roll application code forward or restore a reviewed backup")
