"""Every spelling of a manufacturer in the nomenclature becomes a numbered registry entry."""

import importlib.util

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.support.paths import BACKEND_DIR


def load_migration():
    path = BACKEND_DIR / "alembic/versions/b5131014_manufacturer_registry.py"
    spec = importlib.util.spec_from_file_location("manufacturer_registry", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_spellings_become_numbered_entries_linked_to_the_nomenclature_and_downgrade_restores_the_tables():
    migration = load_migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE manufacturers (id VARCHAR(36) PRIMARY KEY, created_at DATETIME, version INTEGER, "
            "name VARCHAR(250), normalized_name VARCHAR(250), "
            "CONSTRAINT manufacturers_normalized_name_key UNIQUE (normalized_name))"))
        conn.execute(sa.text("CREATE TABLE nomenclatures (id VARCHAR(36) PRIMARY KEY, name VARCHAR(250), "
                             "manufacturer VARCHAR(250))"))
        conn.execute(sa.text("INSERT INTO manufacturers VALUES ('old', '2026-09-27', 1, 'Chemicea', 'chemicea')"))
        conn.execute(sa.text(
            "INSERT INTO nomenclatures VALUES ('n1', 'A', 'sigma-aldrich'), ('n2', 'B', ' Sigma-Aldrich '), "
            "('n3', 'C', 'chemicea'), ('n4', 'D', 'Waters'), ('n5', 'E', NULL), ('n6', 'F', '  '), "
            "('n7', 'G', 'waters'), ('n8', 'H', 'waters')"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        makers = conn.execute(sa.text("SELECT internal_code, name, id FROM manufacturers ORDER BY internal_code")).all()
        # The commonest spelling wins; on a tie the one with capitals does.
        assert [(code, name) for code, name, _ in makers] == [(1, "Chemicea"), (2, "Sigma-Aldrich"), (3, "waters")]
        ids = {name: maker_id for _, name, maker_id in makers}
        assert ids["Chemicea"] == "old"
        links = dict(conn.execute(sa.text("SELECT id, manufacturer_id FROM nomenclatures")).all())
        assert links == {"n1": ids["Sigma-Aldrich"], "n2": ids["Sigma-Aldrich"], "n3": "old", "n4": ids["waters"],
                         "n5": None, "n6": None, "n7": ids["waters"], "n8": ids["waters"]}
        assert conn.execute(sa.text("SELECT name, normalized_name FROM manufacturer_aliases ORDER BY name")).all() == [
            ("Chemicea", "chemicea"), ("Sigma-Aldrich", "sigma-aldrich"), ("waters", "waters")]
        assert conn.execute(sa.text("SELECT manufacturer FROM nomenclatures WHERE id = 'n2'")).scalar() == (
            " Sigma-Aldrich ")

        conn.execute(sa.text("UPDATE manufacturers SET merged_into_id = :target, name = 'Waters' "
                             "WHERE id = 'old'"), {"target": ids["waters"]})
        conn.execute(sa.text("UPDATE manufacturers SET normalized_name = 'waters' WHERE id = 'old'"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert {column["name"] for column in sa.inspect(conn).get_columns("manufacturers")} == {
            "id", "created_at", "version", "name", "normalized_name"}
        assert {column["name"] for column in sa.inspect(conn).get_columns("nomenclatures")} == {
            "id", "name", "manufacturer"}
        assert "manufacturer_aliases" not in sa.inspect(conn).get_table_names()
        assert conn.execute(sa.text("SELECT normalized_name FROM manufacturers WHERE id = 'old'")).scalar() == (
            "waters #old")
