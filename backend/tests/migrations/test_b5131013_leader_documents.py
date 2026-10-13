"""The standard leader role gets document rights for every request, once, without overriding an administrator."""

import importlib.util

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.support.paths import BACKEND_DIR


def load_migration():
    path = BACKEND_DIR / "alembic/versions/b5131013_leader_documents.py"
    spec = importlib.util.spec_from_file_location("leader_documents", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def grants(conn):
    return conn.execute(sa.text("SELECT role_id, code, scope, allow FROM permission_grants ORDER BY role_id, code")).all()


def schema(conn):
    conn.execute(sa.text("CREATE TABLE roles (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200) UNIQUE)"))
    conn.execute(sa.text("CREATE TABLE permission_grants (id VARCHAR(36) PRIMARY KEY, created_at TIMESTAMP, "
                         "version INTEGER, user_id VARCHAR(36), role_id VARCHAR(36), code VARCHAR(100), "
                         "scope VARCHAR(10), allow BOOLEAN)"))
    conn.execute(sa.text("INSERT INTO roles VALUES ('leader', 'Руководитель'), ('sales', 'Менеджер продаж')"))
    conn.execute(sa.text("INSERT INTO permission_grants VALUES ('sales-docs', NULL, 1, NULL, 'sales', "
                         "'documents.write', 'own', 1)"))


def upgrade(conn, migration):
    with Operations.context(MigrationContext.configure(conn)):
        migration.upgrade()


def test_the_leader_role_issues_documents_in_every_request_and_the_grant_is_added_once():
    migration = load_migration()
    with sa.create_engine("sqlite://").begin() as conn:
        schema(conn)
        upgrade(conn, migration)
        upgrade(conn, migration)
        assert grants(conn) == [("leader", "documents.write", "all", 1), ("sales", "documents.write", "own", 1)]


def test_an_administrator_decision_on_the_leader_role_is_kept():
    migration = load_migration()
    with sa.create_engine("sqlite://").begin() as conn:
        schema(conn)
        conn.execute(sa.text("INSERT INTO permission_grants VALUES ('kept', NULL, 1, NULL, 'leader', "
                             "'documents.write', 'own', 1)"))
        upgrade(conn, migration)
        assert ("leader", "documents.write", "own", 1) in grants(conn)
        assert len(grants(conn)) == 2
