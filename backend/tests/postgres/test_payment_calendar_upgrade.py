"""Concurrent manager decisions must record one cash fact and one recurrence."""

from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import select
from test_postgres_commerce import concurrent_posts, postgres_commerce  # noqa: F401

from alembic import command as migrations
from app.business.models import CalendarEntry, WorkflowReview
from app.business.routes import router
from app.communication.models import FileRecord
from app.core.models import AuditEvent
from app.crm.models import Request

pytestmark = pytest.mark.postgres


@pytest.fixture
def legacy_calendar_schema(request, monkeypatch):
    # Use the same isolated schema fixture, but start before this migration.
    upgrade = migrations.upgrade
    monkeypatch.setattr(migrations, "upgrade", lambda config, _target: upgrade(config, "b5131006"))
    env = request.getfixturevalue("postgres_commerce")
    monkeypatch.setattr(migrations, "upgrade", upgrade)
    return env


def test_legacy_payments_files_and_refusals_survive_postgres_upgrade(legacy_calendar_schema):
    env = legacy_calendar_schema
    paid_id, old_file_id = str(uuid4()), str(uuid4())
    with env["sessions"].begin() as db:
        conn = db.connection()
        payments = sa.Table("calendar_entries", sa.MetaData(), autoload_with=conn)
        files = sa.Table("files", sa.MetaData(), autoload_with=conn)
        db.execute(payments.insert(), {"id": paid_id, "created_at": datetime(2026, 1, 31, tzinfo=timezone.utc),
            "version": 1, "direction": "income", "planned_date": date(2026, 1, 20), "amount": 100,
            "currency": "RUB", "purpose": "Исторический платёж", "status": "confirmed", "recurrence": "none",
            "payment_kind": "other", "responsible_id": env["owner_id"], "author_id": env["owner_id"],
            "confirmed_at": datetime(2026, 1, 31, 22, 30, tzinfo=timezone.utc), "confirmed_by": env["owner_id"]})
        db.execute(files.insert(), {"id": old_file_id, "created_at": datetime.now(timezone.utc), "version": 1,
            "name": "Исторический счёт.pdf", "media_type": "application/pdf", "size": 20,
            "sha256": "a" * 64, "storage_key": "legacy/" + old_file_id, "classification": "general",
            "status": "clean", "author_id": env["owner_id"], "request_id": env["request_id"]})
        req = db.get(Request, env["request_id"])
        req.commercial_stage, req.loss_reason = "quote_given", "no_budget"
        db.flush()
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        config.attributes["connection"] = conn
        migrations.upgrade(config, "head")
    with env["sessions"]() as db:
        assert db.get(CalendarEntry, paid_id).actual_date == date(2026, 2, 1)
        assert db.get(CalendarEntry, paid_id).amount == 100
        assert db.get(FileRecord, old_file_id).request_id == env["request_id"]
        req = db.get(Request, env["request_id"])
        assert req.commercial_stage == "closed_lost" and req.closed_at and req.version == 2


def test_concurrent_calendar_decisions_cannot_confirm_or_recur_twice(postgres_commerce):  # noqa: F811
    env = postgres_commerce
    env["app"].include_router(router, prefix="/api/v1")
    with env["sessions"].begin() as db:
        payment = CalendarEntry(direction="expense", planned_date=date(2026, 10, 6), amount=100, currency="RUB",
                                purpose="Счёт поставщика", status="pending", recurrence="weekly",
                                author_id=env["owner_id"], responsible_id=env["owner_id"])
        db.add(payment)
        db.flush()
        review = WorkflowReview(kind="calendar", entity_id=payment.id, source_version=payment.version,
                                title="Платёж", snapshot={}, submitted_by=env["owner_id"])
        db.add_all([review, FileRecord(name="Счёт.pdf", media_type="application/pdf", size=20,
            sha256=uuid4().hex * 2, storage_key="test/" + str(uuid4()), author_id=env["owner_id"],
            classification="general", status="clean", calendar_entry_id=payment.id)])
        db.flush()
        review_id, payment_id, version = review.id, payment.id, review.version
    payload = {"version": version, "decision": "approved", "reason": "Оплачено", "actual_date": "2026-10-08"}
    responses = concurrent_posts(env, [(f"/workflow-approvals/{review_id}/decision",
        {**payload, "idempotency_key": str(uuid4())}) for _ in range(2)])
    assert sorted(r.status_code for r in responses) == [200, 409]
    with env["sessions"]() as db:
        paid = db.get(CalendarEntry, payment_id)
        assert paid.status == "confirmed" and paid.actual_date == date(2026, 10, 8)
        assert db.query(AuditEvent).filter_by(entity_id=payment_id, action="confirmed").count() == 1
        drafts = db.scalars(select(CalendarEntry).where(CalendarEntry.status == "draft")).all()
        assert len(drafts) == 1 and drafts[0].planned_date == date(2026, 10, 13)
        assert drafts[0].actual_date is None and not drafts[0].outside_payment_days
