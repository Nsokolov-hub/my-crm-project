"""Sequential, immutable counterparty codes, including imports and bootstrap inserts."""

from sqlalchemy import event, text
from sqlalchemy.orm import Session


def next_code(context):
    connection = context.connection
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(176222513)"))
    return connection.scalar(text("SELECT COALESCE(MAX(internal_code), 0) + 1 FROM counterparties"))


@event.listens_for(Session, "before_flush")
def assign_codes(session, flush_context, instances):
    # Assign a range before SQLAlchemy batches multiple INSERTs into one statement.
    from app.crm.models import Counterparty

    rows = [row for row in session.new if isinstance(row, Counterparty) and row.internal_code is None]
    if not rows:
        return
    connection = session.connection()
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(176222513)"))
    code = connection.scalar(text("SELECT COALESCE(MAX(internal_code), 0) FROM counterparties"))
    explicit = [
        row.internal_code for row in session.new if isinstance(row, Counterparty) and row.internal_code
    ]
    code = max([code, *explicit])
    for row in rows:
        code += 1
        row.internal_code = code
