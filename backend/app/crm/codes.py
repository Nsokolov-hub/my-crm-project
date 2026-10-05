"""Sequential, immutable counterparty codes, including imports and bootstrap inserts."""

import re

from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session


def compact_request_number(value: int) -> str:
    """1..1000, A1..A1000, B1..B1000, ... Z1000, AA1."""
    if value <= 0:
        raise ValueError('Номер должен быть положительным')
    if value <= 1000:
        return str(value)
    prefix_number, suffix = divmod(value - 1, 1000)
    prefix = ''
    while prefix_number:
        prefix_number, digit = divmod(prefix_number - 1, 26)
        prefix = chr(65 + digit) + prefix
    return f'{prefix}{suffix + 1}'


def request_number_value(number: str) -> int:
    match = re.fullmatch(r'([A-Z]*)([1-9][0-9]{0,3})', number)
    if not match or int(match[2]) > 1000:
        return 0
    prefix = 0
    for letter in match[1]:
        prefix = prefix * 26 + ord(letter) - 64
    return prefix * 1000 + int(match[2])


def next_request_number(db: Session) -> str:
    from app.core.service import advisory
    from app.crm.models import NumberCounter, Request

    advisory(db, 'request.number')
    counter = db.scalar(select(NumberCounter).where(NumberCounter.key == 'requests').with_for_update()
                        .execution_options(populate_existing=True))
    if counter is None:
        # Fresh databases and upgrades account for every legacy row, including archives.
        count = db.scalar(select(func.count()).select_from(Request)) or 0
        maximum = max((request_number_value(number) for number in db.scalars(select(Request.number))), default=0)
        counter = NumberCounter(key='requests', value=max(count, maximum))
        db.add(counter)
        db.flush()
    while True:
        counter.value += 1
        number = compact_request_number(counter.value)
        if not db.scalar(select(Request.id).where(Request.number == number)):
            db.flush()
            return number


def next_code(context):
    connection = context.connection
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(176222513)"))
    return connection.scalar(text("SELECT COALESCE(MAX(internal_code), 0) + 1 FROM counterparties"))


@event.listens_for(Session, "before_flush")
def assign_codes(session, flush_context, instances):
    # Assign a range before SQLAlchemy batches multiple INSERTs into one statement.
    from app.crm.models import Counterparty, ProductGroup

    groups = [row for row in session.new if isinstance(row, ProductGroup) and row.internal_code is None]
    if groups:
        connection = session.connection()
        if connection.dialect.name == 'postgresql':
            connection.execute(text('SELECT pg_advisory_xact_lock(176222514)'))
        code = connection.scalar(select(func.coalesce(func.max(ProductGroup.internal_code), 0)))
        explicit = [row.internal_code for row in session.new if isinstance(row, ProductGroup) and row.internal_code]
        code = max([code, *explicit])
        for row in groups:
            code += 1
            row.internal_code = code

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
