"""Search counterparties in SQL on normalized text instead of scanning the table in Python."""

import sqlalchemy as sa

from alembic import op

revision = "b5131010"
down_revision = "b5131009"
branch_labels = None
depends_on = None

TRIGRAM_INDEX = "ix_counterparties_search_text_trgm"
FILTER_FIELDS = ("name", "tax_id", "profile", "city", "phone", "email")
BATCH = 1000


def normalize(value):
    # Frozen copy of app.crm.text_search.normalize: a migration must not change with later code.
    return "".join(char for char in str(value or "").casefold().replace("ё", "е") if char.isalnum())


def search_values(row):
    details = row.details if isinstance(row.details, dict) else {}
    values = {"name": row.name, "tax_id": row.tax_id, "phone": row.phone, "email": row.email,
              "internal_code": row.internal_code, "profile": details.get("profile"), "city": details.get("city")}
    text = normalize(" ".join(str(value or "") for value in values.values()))
    return text, {field: normalize(values[field]) for field in FILTER_FIELDS}


def upgrade():
    op.add_column("counterparties", sa.Column("search_text", sa.Text(), nullable=False, server_default=""))
    op.add_column("counterparties", sa.Column("search_fields", sa.JSON(), nullable=False, server_default="{}"))
    bind = op.get_bind()
    table = sa.table(
        "counterparties", sa.column("id", sa.String), sa.column("name", sa.String), sa.column("tax_id", sa.String),
        sa.column("phone", sa.String), sa.column("email", sa.String), sa.column("internal_code", sa.Integer),
        sa.column("details", sa.JSON), sa.column("search_text", sa.Text), sa.column("search_fields", sa.JSON),
    )
    update = table.update().where(table.c.id == sa.bindparam("row_id")).values(
        search_text=sa.bindparam("text"), search_fields=sa.bindparam("fields"))
    rows = bind.execute(sa.select(table.c.id, table.c.name, table.c.tax_id, table.c.phone, table.c.email,
                                  table.c.internal_code, table.c.details).order_by(table.c.id)).all()
    for start in range(0, len(rows), BATCH):
        batch = []
        for row in rows[start:start + BATCH]:
            text, fields = search_values(row)
            batch.append({"row_id": row.id, "text": text, "fields": fields})
        bind.execute(update, batch)
    if bind.dialect.name == "postgresql":
        # Trigram index for LIKE '%…%'. The extension may already live in another schema; qualify with it.
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public")
        schema = bind.scalar(sa.text("SELECT extnamespace::regnamespace::text FROM pg_extension WHERE extname = 'pg_trgm'"))
        op.execute(f'CREATE INDEX {TRIGRAM_INDEX} ON counterparties USING gin (search_text "{schema}".gin_trgm_ops)')


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP INDEX IF EXISTS {TRIGRAM_INDEX}")
    op.drop_column("counterparties", "search_fields")
    op.drop_column("counterparties", "search_text")
