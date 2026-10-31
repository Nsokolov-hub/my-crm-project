"""The «Руководитель» role gets the rights of «Заявки с сайта»; sales managers do not; a decision taken stays."""

import importlib.util

import sqlalchemy as sa

from tests.support.paths import BACKEND_DIR


def load_migration():
    path = BACKEND_DIR / "alembic/versions/b5131029_site_request_rights.py"
    spec = importlib.util.spec_from_file_location("site_request_rights", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_leaders_get_the_site_request_rights_once():
    migration = load_migration()
    with sa.create_engine("sqlite://").begin() as conn:
        conn.execute(sa.text("CREATE TABLE roles (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200) UNIQUE)"))
        conn.execute(sa.text("CREATE TABLE permission_grants (id VARCHAR(36) PRIMARY KEY, created_at TIMESTAMP, "
                             "version INTEGER, user_id VARCHAR(36), role_id VARCHAR(36), code VARCHAR(100), "
                             "scope VARCHAR(10), allow BOOLEAN)"))
        conn.execute(sa.text("INSERT INTO roles VALUES ('leader', 'Руководитель'), ('sales', 'Менеджер продаж')"))
        # The owner already decided on one right of the role by hand: it is kept as it is.
        conn.execute(sa.text("INSERT INTO permission_grants VALUES "
                             "('g1', NULL, 1, NULL, 'leader', 'site_requests.write', 'own', 1)"))
        migration.grant_leader_rights(conn)
        migration.grant_leader_rights(conn)
        grants = conn.execute(sa.text(
            "SELECT role_id, code, scope FROM permission_grants ORDER BY role_id, code")).all()
        assert grants == [("leader", "site_requests.assign", "all"), ("leader", "site_requests.read", "all"),
                          ("leader", "site_requests.write", "own")]


def test_a_system_without_the_role_is_left_alone():
    migration = load_migration()
    with sa.create_engine("sqlite://").begin() as conn:
        conn.execute(sa.text("CREATE TABLE roles (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200) UNIQUE)"))
        conn.execute(sa.text("CREATE TABLE permission_grants (id VARCHAR(36) PRIMARY KEY, created_at TIMESTAMP, "
                             "version INTEGER, user_id VARCHAR(36), role_id VARCHAR(36), code VARCHAR(100), "
                             "scope VARCHAR(10), allow BOOLEAN)"))
        migration.grant_leader_rights(conn)
        assert conn.execute(sa.text("SELECT count(*) FROM permission_grants")).scalar() == 0
