"""The October 7 migration keeps existing quotes and mail readable."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_oct7_defaults_and_rollback_preserve_existing_records():
    path = Path(__file__).parents[1] / "alembic/versions/b5131005_business_oct7.py"
    spec = importlib.util.spec_from_file_location("business_oct7_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    meta = sa.MetaData()
    for name in ("quote_items", "quote_sheets", "supplier_mails"):
        sa.Table(name, meta, sa.Column("id", sa.String(36), primary_key=True))
    meta.create_all(engine)
    with engine.begin() as conn:
        for table in meta.tables.values():
            conn.execute(table.insert().values(id="existing"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        reflected = sa.MetaData()
        reflected.reflect(conn)
        for name in ("quote_items", "quote_sheets"):
            assert conn.scalar(sa.select(reflected.tables[name].c.archived)) is False
        assert conn.scalar(sa.select(reflected.tables["supplier_mails"].c.cc)) == []
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        for table in meta.tables.values():
            assert conn.scalar(sa.select(table.c.id)) == "existing"
            assert {c["name"] for c in sa.inspect(conn).get_columns(table.name)} == {"id"}
    engine.dispose()
