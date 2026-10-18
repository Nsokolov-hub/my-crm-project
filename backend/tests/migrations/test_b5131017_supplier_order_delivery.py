"""A cancelled order line frees its position; the delivery right follows ordering and the logistician role."""

import importlib.util

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.support.paths import BACKEND_DIR


def load_migration():
    path = BACKEND_DIR / "alembic/versions/b5131017_supplier_order_delivery.py"
    spec = importlib.util.spec_from_file_location("supplier_order_delivery", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(conn, step):
    with Operations.context(MigrationContext.configure(conn)):
        step()


def test_cancelled_lines_free_the_position_and_logistics_gets_the_delivery_right():
    migration = load_migration()
    with sa.create_engine("sqlite://").begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE roles (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200) UNIQUE)"))
        conn.execute(sa.text("CREATE TABLE permission_grants (id VARCHAR(36) PRIMARY KEY, created_at TIMESTAMP, "
                             "version INTEGER, user_id VARCHAR(36), role_id VARCHAR(36), code VARCHAR(100), "
                             "scope VARCHAR(10), allow BOOLEAN)"))
        # As b5131003 created it: the execution unique without a name.
        conn.execute(sa.text(
            "CREATE TABLE supplier_order_lines (id VARCHAR(36) PRIMARY KEY, created_at TIMESTAMP, version INTEGER, "
            "order_id VARCHAR(36) NOT NULL, execution_id VARCHAR(36) NOT NULL, quantity NUMERIC(24, 6) NOT NULL, "
            "unit_price NUMERIC(24, 8) NOT NULL, status VARCHAR(20) NOT NULL, snapshot JSON NOT NULL, "
            "CHECK (quantity > 0 AND unit_price >= 0), UNIQUE (execution_id))"))
        conn.execute(sa.text("INSERT INTO supplier_order_lines VALUES "
                             "('a', NULL, 1, 'o1', 'e1', 1, 10, 'waiting', '{}')"))
        conn.execute(sa.text("INSERT INTO roles VALUES ('buyer', 'Закупщик'), ('logist', 'Логист'), "
                             "('sales', 'Менеджер продаж')"))
        conn.execute(sa.text(
            "INSERT INTO permission_grants VALUES "
            "('g1', NULL, 1, NULL, 'buyer', 'procurement.write', 'all', 1), "
            "('g2', NULL, 1, 'owner', NULL, 'procurement.write', 'own', 1), "
            "('g3', NULL, 1, 'denied', NULL, 'procurement.write', 'all', 0), "
            "('g4', NULL, 1, NULL, 'sales', 'requests.read', 'own', 1)"))
        run(conn, migration.upgrade)
        migration.grant_delivery_right(conn)

        grants = conn.execute(sa.text(
            "SELECT coalesce(role_id, user_id), scope FROM permission_grants "
            "WHERE code = 'supplier_orders.delivery' ORDER BY 1")).all()
        assert grants == [("buyer", "all"), ("logist", "all"), ("owner", "own")]
        conn.execute(sa.text("UPDATE supplier_order_lines SET status = 'cancelled', cancel_reason = 'Снято'"))
        conn.execute(sa.text("INSERT INTO supplier_order_lines (id, version, order_id, execution_id, quantity, "
                             "unit_price, status, snapshot) VALUES ('b', 1, 'o2', 'e1', 1, 10, 'waiting', '{}')"))
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(sa.text("INSERT INTO supplier_order_lines (id, version, order_id, execution_id, quantity, "
                                 "unit_price, status, snapshot) VALUES ('c', 1, 'o3', 'e1', 1, 10, 'delivery', '{}')"))
