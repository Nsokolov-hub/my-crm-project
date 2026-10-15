"""Everyone who reads analytics keeps every report, except the standard sales manager role."""

import importlib.util

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.support.paths import BACKEND_DIR


def load_migration():
    path = BACKEND_DIR / "alembic/versions/b5131016_analytics_reports.py"
    spec = importlib.util.spec_from_file_location("analytics_reports", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade(conn, migration):
    with Operations.context(MigrationContext.configure(conn)):
        migration.upgrade()


def test_reports_follow_analytics_except_for_the_sales_manager_role():
    migration = load_migration()
    with sa.create_engine("sqlite://").begin() as conn:
        conn.execute(sa.text("CREATE TABLE roles (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200) UNIQUE)"))
        conn.execute(sa.text("CREATE TABLE permission_grants (id VARCHAR(36) PRIMARY KEY, created_at TIMESTAMP, "
                             "version INTEGER, user_id VARCHAR(36), role_id VARCHAR(36), code VARCHAR(100), "
                             "scope VARCHAR(10), allow BOOLEAN)"))
        conn.execute(sa.text("INSERT INTO roles VALUES ('leader', 'Руководитель'), ('sales', 'Менеджер продаж'), "
                             "('finance', 'Финансовый контролёр')"))
        conn.execute(sa.text(
            "INSERT INTO permission_grants VALUES "
            "('a', NULL, 1, NULL, 'leader', 'analytics.read', 'all', 1), "
            "('b', NULL, 1, NULL, 'sales', 'analytics.read', 'own', 1), "
            "('c', NULL, 1, NULL, 'finance', 'analytics.read', 'all', 1), "
            "('d', NULL, 1, 'owner', NULL, 'analytics.read', 'all', 1), "
            "('e', NULL, 1, 'denied', NULL, 'analytics.read', 'all', 0)"))
        upgrade(conn, migration)
        upgrade(conn, migration)
        reports = conn.execute(sa.text(
            "SELECT coalesce(role_id, user_id), scope FROM permission_grants "
            "WHERE code = 'analytics.reports' ORDER BY 1")).all()
        assert reports == [("finance", "all"), ("leader", "all"), ("owner", "all")]
