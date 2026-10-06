"""Legacy approvals and budgets survive the separation of real and forecast waves."""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_oct6_backfill_and_rollback_preserve_drafts_and_recorded_budgets():
    path = Path(__file__).parents[1] / "alembic/versions/b5131004_business_oct6.py"
    spec = importlib.util.spec_from_file_location("business_oct6_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    meta = sa.MetaData()
    for name in ("users", "product_groups", "calculation_profiles", "waves"):
        sa.Table(name, meta, sa.Column("id", sa.String(36), primary_key=True))
    executions = sa.Table("executions", meta, sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("revision", sa.Integer()), sa.Column("quantity", sa.Numeric()),
        sa.Column("cancelled_quantity", sa.Numeric()), sa.Column("created_at", sa.DateTime()))
    approvals = sa.Table("approvals", meta, sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String()), sa.Column("snapshot", sa.JSON()),
        sa.Column("decided_at", sa.DateTime()), sa.Column("created_at", sa.DateTime()))
    allocations = sa.Table("wave_allocations", meta, sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("execution_id", sa.String(36)))
    orders = sa.Table("supplier_order_lines", meta, sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("execution_id", sa.String(36)))
    calculations = sa.Table("calculations", meta, sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("snapshot", sa.JSON()), sa.Column("profile_id", sa.String(36)), sa.Column("created_at", sa.DateTime()))
    meta.create_all(engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expense = {"name": "Транспорт", "scope": "WAVE", "stage": "DOMESTIC_LOGISTICS", "amount": "500"}
    with engine.begin() as conn:
        conn.execute(meta.tables["calculation_profiles"].insert().values(id="profile"))
        conn.execute(meta.tables["waves"].insert(), [{"id": "used"}, {"id": "draft"}])
        conn.execute(executions.insert(), [{"id": key, "revision": 1, "quantity": 2,
            "cancelled_quantity": 0, "created_at": now} for key in ("approved", "stale", "pending", "allocated", "ordered")])
        conn.execute(approvals.insert(), [{"id": key, "status": status, "snapshot": {"lines": [
            {"execution_id": key, "revision": revision}]}, "decided_at": now, "created_at": now}
            for key, status, revision in (("approved", "approved", 1), ("stale", "approved", 2), ("pending", "pending", 1))])
        conn.execute(allocations.insert().values(id="a", execution_id="allocated"))
        conn.execute(orders.insert().values(id="o", execution_id="ordered"))
        for key, wave_id, amount, day in (("old", "used", "100", -1), ("new", "used", "500", 0), ("draft", "draft", "500", 0)):
            conn.execute(calculations.insert().values(id=key, profile_id="profile", created_at=now + timedelta(days=day),
                snapshot={"algorithm_version": "itemized-v2", "wave": {"id": wave_id}, "rates": [],
                    "resolved_expenses": [{**expense, "amount": amount}, {"name": "СДЭК", "scope": "REQUEST"}]}))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        reflected = sa.MetaData()
        reflected.reflect(conn)
        updated = reflected.tables["executions"]
        handed_off = set(conn.scalars(sa.select(updated.c.id).where(updated.c.procurement_at.is_not(None))))
        assert handed_off == {"approved", "allocated", "ordered"}
        budgets = conn.execute(sa.select(reflected.tables["waves"])).mappings().all()
        assert all(row["budget_expenses"] == [expense] and row["budget_profile_id"] == "profile" for row in budgets)
        assert conn.scalar(sa.select(sa.func.count()).select_from(reflected.tables["wave_forecasts"])) == 0
        assert conn.scalar(sa.select(sa.func.count()).select_from(allocations)) == 1
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert "wave_forecasts" not in sa.inspect(conn).get_table_names()
        assert "procurement_at" not in {row["name"] for row in sa.inspect(conn).get_columns("executions")}
        assert conn.scalar(sa.select(sa.func.count()).select_from(calculations)) == 3
    engine.dispose()
