"""Compact request numbers, counterparty documents, table formatting and ISO wave weeks."""

import re
from datetime import date

import sqlalchemy as sa

from alembic import op

revision = "b5131002"
down_revision = "b5131001"
branch_labels = None
depends_on = None


def compact_value(number):
    match = re.fullmatch(r"([A-Z]*)([1-9][0-9]{0,3})", number)
    if not match or int(match[2]) > 1000:
        return 0
    prefix = 0
    for letter in match[1]:
        prefix = prefix * 26 + ord(letter) - 64
    return prefix * 1000 + int(match[2])


def upgrade():
    op.add_column("request_items", sa.Column("source_format", sa.JSON(), nullable=False, server_default="{}"))
    op.create_table(
        "number_counters",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("value", sa.BigInteger(), nullable=False),
    )
    connection = op.get_bind()
    requests = sa.table("requests", sa.column("number", sa.String(80)))
    numbers = list(connection.scalars(sa.select(requests.c.number)))
    counters = sa.table(
        "number_counters", sa.column("key", sa.String(80)), sa.column("value", sa.BigInteger())
    )
    connection.execute(
        counters.insert().values(
            key="requests", value=max(len(numbers), max(map(compact_value, numbers), default=0))
        )
    )
    op.create_table(
        "counterparty_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("counterparty_id", sa.String(36), sa.ForeignKey("counterparties.id"), nullable=False),
        sa.Column("file_id", sa.String(36), sa.ForeignKey("files.id"), nullable=False),
        sa.Column("category", sa.String(20), nullable=False, server_default="other"),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("file_id", name="uq_counterparty_documents_file_id"),
        sa.CheckConstraint(
            "category IN ('founding', 'contract', 'other')", name="ck_counterparty_documents_category"
        ),
    )
    op.create_index(
        "ix_counterparty_documents_counterparty_id", "counterparty_documents", ["counterparty_id"]
    )
    with op.batch_alter_table("supplier_requests") as batch:
        batch.alter_column("supplier_id", existing_type=sa.String(36), nullable=True)
    prefixes = ("close", "departure", "arrival")
    for prefix in prefixes:
        op.add_column("waves", sa.Column(f"{prefix}_week", sa.Integer(), nullable=True))
        op.add_column("waves", sa.Column(f"{prefix}_year", sa.Integer(), nullable=True))
    waves = sa.table(
        "waves",
        sa.column("id", sa.String(36)),
        *[
            column
            for prefix in prefixes
            for column in (
                sa.column(f"{prefix}_date", sa.Date()),
                sa.column(f"{prefix}_week", sa.Integer()),
                sa.column(f"{prefix}_year", sa.Integer()),
            )
        ],
    )
    for row in connection.execute(sa.select(waves)).mappings():
        changes = {}
        for prefix in prefixes:
            old_date = row[f"{prefix}_date"]
            if old_date is not None:
                iso = (date.fromisoformat(old_date) if isinstance(old_date, str) else old_date).isocalendar()
                changes[f"{prefix}_week"], changes[f"{prefix}_year"] = iso.week, iso.year
        if changes:
            connection.execute(waves.update().where(waves.c.id == row["id"]).values(**changes))


def downgrade():
    # A common RFQ cannot be represented by the earlier supplier-required schema.
    # Refuse the downgrade before changing this constraint when common RFQs exist.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM supplier_requests WHERE supplier_id IS NULL")):
        raise RuntimeError("Archive/export common RFQs before downgrading to the supplier-required schema")
    for prefix in ("arrival", "departure", "close"):
        op.drop_column("waves", f"{prefix}_year")
        op.drop_column("waves", f"{prefix}_week")
    with op.batch_alter_table("supplier_requests") as batch:
        batch.alter_column("supplier_id", existing_type=sa.String(36), nullable=False)
    op.drop_index("ix_counterparty_documents_counterparty_id", table_name="counterparty_documents")
    op.drop_table("counterparty_documents")
    op.drop_table("number_counters")
    op.drop_column("request_items", "source_format")
