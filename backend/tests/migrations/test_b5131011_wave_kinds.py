"""Existing waves become working waves; the database keeps one plan per supplier in use."""

import importlib.util

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.support.paths import BACKEND_DIR


def load_migration():
    spec = importlib.util.spec_from_file_location("wave_kinds", BACKEND_DIR / "alembic/versions/b5131011_wave_kinds.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_waves_keep_working_and_a_supplier_has_one_plan_in_use():
    migration = load_migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE waves (id VARCHAR(36) PRIMARY KEY, number VARCHAR(80) UNIQUE, "
                             "supplier_id VARCHAR(36))"))
        conn.execute(sa.text("INSERT INTO waves VALUES (:id, :number, 's')"),
                     [{"id": "a", "number": "Поставщик 1"}, {"id": "b", "number": "Поставщик 2"}])
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        rows = conn.execute(sa.text("SELECT kind, plan_id, archived FROM waves ORDER BY id")).all()
        assert [tuple(row) for row in rows] == [("work", None, 0), ("work", None, 0)]
        insert = sa.text("INSERT INTO waves (id, number, supplier_id, kind, plan_id, archived) "
                         "VALUES (:id, :id, 's', :kind, :plan, :archived)")
        conn.execute(insert, {"id": "plan", "kind": "plan", "plan": None, "archived": False})
        conn.execute(insert, {"id": "old-plan", "kind": "plan", "plan": None, "archived": True})
        conn.execute(insert, {"id": "copy", "kind": "work", "plan": "plan", "archived": False})
        with pytest.raises(sa.exc.IntegrityError):
            with conn.begin_nested():
                conn.execute(insert, {"id": "second-plan", "kind": "plan", "plan": None, "archived": False})
        with pytest.raises(sa.exc.IntegrityError):
            with conn.begin_nested():
                conn.execute(insert, {"id": "odd", "kind": "draft", "plan": None, "archived": False})
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        columns = {column["name"] for column in sa.inspect(conn).get_columns("waves")}
        assert columns == {"id", "number", "supplier_id"}
