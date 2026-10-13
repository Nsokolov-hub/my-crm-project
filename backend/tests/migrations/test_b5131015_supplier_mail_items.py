"""Mails remember their positions; mails sent before keep no list and are read back from their table."""

import importlib.util

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.support.paths import BACKEND_DIR


def load_migration():
    path = BACKEND_DIR / "alembic/versions/b5131015_supplier_mail_items.py"
    spec = importlib.util.spec_from_file_location("supplier_mail_items", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sent_mails_keep_no_positions_and_downgrade_removes_the_columns():
    migration = load_migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE supplier_mails (id VARCHAR(36) PRIMARY KEY, body TEXT)"))
        conn.execute(sa.text("INSERT INTO supplier_mails VALUES ('old', 'Name\tQty')"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert conn.execute(sa.text("SELECT item_ids, replied_at, reply_note FROM supplier_mails")).one() == (
            None, None, "")
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert {column["name"] for column in sa.inspect(conn).get_columns("supplier_mails")} == {"id", "body"}
