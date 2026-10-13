"""Counterparties can point to the card they were merged into; requests remember archiving with the client."""

import importlib.util

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.support.paths import BACKEND_DIR


def load_migration():
    path = BACKEND_DIR / "alembic/versions/b5131012_counterparty_merge.py"
    spec = importlib.util.spec_from_file_location("counterparty_merge", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_requests_are_not_archived_with_their_client_and_downgrade_removes_the_columns():
    migration = load_migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE counterparties (id VARCHAR(36) PRIMARY KEY, name VARCHAR(250))"))
        conn.execute(sa.text("CREATE TABLE requests (id VARCHAR(36) PRIMARY KEY, client_id VARCHAR(36), "
                             "archived BOOLEAN)"))
        conn.execute(sa.text("INSERT INTO counterparties VALUES ('main', 'Основной'), ('copy', 'Дубль')"))
        conn.execute(sa.text("INSERT INTO requests VALUES ('archived', 'copy', 1)"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert conn.execute(sa.text("SELECT archived, archived_with_client FROM requests")).one() == (1, 0)
        conn.execute(sa.text("UPDATE counterparties SET merged_into_id = 'main' WHERE id = 'copy'"))
        foreign_keys = sa.inspect(conn).get_foreign_keys("counterparties")
        assert [(key["constrained_columns"], key["referred_table"]) for key in foreign_keys] == [
            (["merged_into_id"], "counterparties")]
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert {column["name"] for column in sa.inspect(conn).get_columns("counterparties")} == {"id", "name"}
        assert {column["name"] for column in sa.inspect(conn).get_columns("requests")} == {
            "id", "client_id", "archived"}
