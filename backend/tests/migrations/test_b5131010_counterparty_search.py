"""Existing counterparties become searchable on normalized text right after the upgrade."""

import importlib.util
import json

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.support.paths import BACKEND_DIR


def load_migration():
    spec = importlib.util.spec_from_file_location(
        "counterparty_search", BACKEND_DIR / "alembic/versions/b5131010_counterparty_search.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backfill_normalizes_existing_rows_and_downgrade_drops_columns():
    migration = load_migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE counterparties (id VARCHAR(36) PRIMARY KEY, name VARCHAR(250), "
                             "tax_id VARCHAR(100), phone VARCHAR(100), email VARCHAR(254), internal_code INTEGER, "
                             "details JSON)"))
        conn.execute(sa.text("INSERT INTO counterparties VALUES (:id, :name, :tax, :phone, :email, :code, :details)"), [
            {"id": "a", "name": "АО «Ёлка»", "tax": "7722-422", "phone": "+7 (495) 900-77-73",
             "email": "Sales@Elka.example", "code": 12, "details": json.dumps({"city": "Москва", "profile": "Фарма"})},
            {"id": "b", "name": "Без реквизитов", "tax": None, "phone": None, "email": None, "code": 13,
             "details": None},
        ])
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        rows = {row.id: row for row in conn.execute(sa.text("SELECT id, search_text, search_fields FROM counterparties"))}
        # name, tax id, phone, e-mail, internal code, profile and city, without spaces and punctuation
        assert rows["a"].search_text == "аоелка" "7722422" "74959007773" "saleselkaexample" "12" "фарма" "москва"
        assert json.loads(rows["a"].search_fields) == {
            "name": "аоелка", "tax_id": "7722422", "profile": "фарма", "city": "москва",
            "phone": "74959007773", "email": "saleselkaexample"}
        assert rows["b"].search_text == "безреквизитов13"
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert "search_text" not in {column["name"] for column in sa.inspect(conn).get_columns("counterparties")}
