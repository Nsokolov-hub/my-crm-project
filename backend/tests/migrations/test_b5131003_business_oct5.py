"""Upgrade seeded legacy roles/groups and exercise a schema rollback on SQLite."""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_oct5_upgrade_codes_roles_and_downgrade():
    path = Path(__file__).parents[1] / "alembic/versions/b5131003_business_oct5.py"
    spec = importlib.util.spec_from_file_location("business_oct5_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    meta = sa.MetaData()
    for name in [
        "users",
        "request_items",
        "requests",
        "counterparties",
        "sellers",
        "commercial_documents",
        "executions",
    ]:
        sa.Table(name, meta, sa.Column("id", sa.String(36), primary_key=True))
    groups = sa.Table(
        "product_groups",
        meta,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(150)),
        sa.Column("slug", sa.String(80)),
        sa.Column("active", sa.Boolean()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer()),
    )
    roles = sa.Table(
        "roles", meta, sa.Column("id", sa.String(36), primary_key=True), sa.Column("name", sa.String(200))
    )
    grants = sa.Table(
        "permission_grants",
        meta,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("role_id", sa.String(36)),
        sa.Column("code", sa.String(100)),
        sa.Column("scope", sa.String(10)),
        sa.Column("allow", sa.Boolean()),
        sa.Column("version", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    meta.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            groups.insert().values(
                id="old",
                name="Колонки",
                slug="columns",
                active=True,
                created_at=datetime.now(timezone.utc),
                version=1,
            )
        )
        conn.execute(
            roles.insert(),
            [{"id": "leader", "name": "Руководитель"}, {"id": "manager", "name": "Менеджер продаж"}],
        )
        conn.execute(
            grants.insert().values(
                id="custom-deny", role_id="manager", code="quotes.write", scope="own", allow=False, version=1
            )
        )
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        updated = sa.Table("product_groups", sa.MetaData(), autoload_with=conn)
        rows = conn.execute(sa.select(updated).order_by(updated.c.internal_code)).mappings().all()
        assert [(r["slug"], r["internal_code"]) for r in rows] == [("columns", 1), ("strains", 2)]
        assert (
            conn.scalar(
                sa.select(grants.c.scope).where(
                    grants.c.role_id == "manager", grants.c.code == "calculations.write"
                )
            )
            == "own"
        )
        assert conn.scalar(sa.select(grants.c.allow).where(grants.c.id == "custom-deny")) is False
        inspector = sa.inspect(conn)
        assert {"supplier_orders", "workflow_reviews", "calendar_entries", "employee_absences"} <= set(
            inspector.get_table_names()
        )
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert "calendar_entries" not in sa.inspect(conn).get_table_names()
        assert "internal_code" not in {c["name"] for c in sa.inspect(conn).get_columns("product_groups")}
        assert conn.scalar(sa.select(groups.c.name).where(groups.c.id == "old")) == "Колонки"
    engine.dispose()
