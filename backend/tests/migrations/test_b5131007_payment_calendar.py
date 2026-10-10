"""Preserve legacy confirmations and file ownership while repairing refusals."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

from app.core.config import settings


def test_payment_migration_preserves_facts_and_closes_only_uncommitted_refusals(monkeypatch):
    monkeypatch.setattr(settings, "company_timezone", "Europe/Moscow")
    engine = sa.create_engine("sqlite://")
    owners = ("request_id", "client_id", "chat_id", "wave_id", "quote_id", "calculation_id", "document_id")
    metadata = sa.MetaData()
    sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("calendar_entries", metadata, sa.Column("id", sa.String(36), primary_key=True),
             sa.Column("status", sa.String(20)), sa.Column("confirmed_at", sa.String(40)))
    files = sa.Table("files", metadata, sa.Column("id", sa.String(36), primary_key=True),
                     *(sa.Column(owner, sa.String(36)) for owner in owners),
                     sa.CheckConstraint("(" + " + ".join(
                         f"CASE WHEN {owner} IS NULL THEN 0 ELSE 1 END" for owner in owners) + ") = 1"))
    requests = sa.Table("requests", metadata, sa.Column("id", sa.String(36), primary_key=True),
                        sa.Column("commercial_stage", sa.String(30)), sa.Column("loss_reason", sa.String(40)),
                        sa.Column("sale_confirmed_at", sa.DateTime()), sa.Column("closed_at", sa.DateTime()),
                        sa.Column("version", sa.Integer()))
    executions = sa.Table("executions", metadata, sa.Column("id", sa.String(36), primary_key=True),
                          sa.Column("request_id", sa.String(36)), sa.Column("quantity", sa.Integer()),
                          sa.Column("cancelled_quantity", sa.Integer()))
    path = Path(__file__).parents[1] / "alembic/versions/b5131007_payment_calendar.py"
    spec = importlib.util.spec_from_file_location("payment_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as conn:
        metadata.create_all(conn)
        conn.execute(sa.text("INSERT INTO calendar_entries VALUES ('paid','confirmed','2026-01-31T22:30:00+00:00'),"
                             "('plan','pending',NULL),('undated','confirmed',NULL)"))
        conn.execute(files.insert(), {"id": "old-file", "request_id": "refused"})
        conn.execute(requests.insert(), [
            {"id": key, "commercial_stage": stage, "loss_reason": reason, "version": 1}
            for key, stage, reason in (("refused", "quote_given", "no_budget"),
                ("active", "quote_given", "no_budget"), ("cancelled", "proposal_sent", "no_budget"),
                ("accepted", "accepted", "no_budget"), ("open", "quote_given", None))])
        conn.execute(executions.insert(), [
            {"id": "e1", "request_id": "active", "quantity": 3, "cancelled_quantity": 0},
            {"id": "e2", "request_id": "cancelled", "quantity": 3, "cancelled_quantity": 3}])
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        facts = {r.id: r.actual_date for r in conn.execute(sa.text("SELECT id, actual_date FROM calendar_entries"))}
        assert facts == {"paid": "2026-02-01", "plan": None, "undated": None}
        assert conn.scalar(sa.text("SELECT request_id FROM files WHERE id='old-file'")) == "refused"
        conn.execute(sa.text("INSERT INTO files (id, calendar_entry_id) VALUES ('invoice', 'paid')"))
        with pytest.raises(IntegrityError):
            with conn.begin_nested():
                conn.execute(sa.text("INSERT INTO files (id, request_id, calendar_entry_id) VALUES ('invalid', 'refused', 'paid')"))
        stages = {r.id: (r.commercial_stage, r.version, bool(r.closed_at)) for r in conn.execute(requests.select())}
        assert stages["refused"] == ("closed_lost", 2, True)
        assert stages["cancelled"] == ("closed_lost", 2, True)
        assert stages["active"] == ("quote_given", 1, False)
        assert stages["accepted"] == ("accepted", 1, False)
        assert stages["open"] == ("quote_given", 1, False)
    engine.dispose()
