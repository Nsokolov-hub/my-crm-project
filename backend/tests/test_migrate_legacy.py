"""Tests for R13 Legacy Data Migration, Idempotency, and Discrepancy Reporting."""

import json
import subprocess
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.commerce.models import CommercialDocument
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.models import User


@pytest.fixture
def sample_payload():
    uid = uuid.uuid4().hex[:8]
    return {
        "users": [
            {
                "legacy_id": f"U_{uid}",
                "email": f"historical_rep_{uid}@example.com",
                "name": f"Исторический Представитель {uid}",
                "active": False,
            }
        ],
        "sellers": [
            {
                "legacy_id": f"S_{uid}",
                "external_id": f"SELLER_{uid}",
                "name": f"Продавец Тест {uid}",
                "currency": "EUR",
            }
        ],
        "counterparties": [
            {
                "legacy_id": f"C_{uid}",
                "external_id": f"CLIENT_{uid}",
                "name": f"Клиент Тест {uid}",
                "owner_legacy_id": f"U_{uid}",
            }
        ],
        "requests": [
            {
                "legacy_id": f"REQ_{uid}",
                "number": f"REQ-TEST-{uid}-001",
                "title": f"Сделка R13 {uid} 2021",
                "client_legacy_id": f"C_{uid}",
                "seller_legacy_id": f"S_{uid}",
                "owner_legacy_id": f"U_{uid}",
                "stage": "closed",
                "archived": True,
                "created_at": "2021-04-12T09:30:00Z",
                "legacy_calculation": {
                    "totals": {"EUR": "5000.00"},
                    "invoice": {
                        "number": f"INV-TEST-{uid}-001",
                        "seller_legacy_id": f"S_{uid}",
                        "total": "5000.00",
                        "currency": "EUR",
                        "date": "2021-04-15",
                    },
                },
            }
        ],
        "payments": [
            {
                "external_id": f"PAY-TEST-{uid}-001",
                "amount": "5000.00",
                "currency": "EUR",
                "payment_date": "2021-04-20",
                "client_legacy_id": f"C_{uid}",
                "seller_legacy_id": f"S_{uid}",
                "request_legacy_id": f"REQ_{uid}",
                "invoice_number": f"INV-TEST-{uid}-001",
            },
            {
                "external_id": f"PAY-TEST-{uid}-002",
                "amount": "1200.00",
                "currency": "EUR",
                "payment_date": "2021-05-01",
                "client_legacy_id": f"C_{uid}",
                "seller_legacy_id": f"S_{uid}",
                "request_legacy_id": f"REQ_{uid}",
                "comment": "Авансовый платёж без счёта",
            },
        ],
    }


def test_dry_run_makes_zero_changes(tmp_path, sample_payload):
    """Verify that --dry-run produces a full report but rolls back all DB modifications."""
    if not settings.database_url.startswith("postgresql"):
        pytest.skip("Requires PostgreSQL database")

    script = Path(__file__).resolve().parents[2] / "scripts" / "migrate_legacy.py"
    data_file = tmp_path / "data.json"
    report_file = tmp_path / "report.json"
    data_file.write_text(json.dumps(sample_payload, ensure_ascii=False))

    res = subprocess.run(
        [
            sys.executable,
            str(script),
            str(data_file),
            "--dry-run",
            "--report",
            str(report_file),
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    assert "DRY-RUN" in res.stderr or "DRY-RUN" in res.stdout

    # Verify report structure
    assert report_file.exists()
    report = json.loads(report_file.read_text())
    assert report["dry_run"] is True
    assert report["stats"]["counterparties_created"] == 1
    assert report["stats"]["payments_created"] == 2
    assert report["stats"]["allocations_created"] == 1
    assert report["stats"]["payments_as_advances"] == 1

    # Verify database was NOT changed
    user_email = sample_payload["users"][0]["email"]
    inv_num = sample_payload["requests"][0]["legacy_calculation"]["invoice"]["number"]
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == user_email))
        assert user is None
        inv = db.scalar(select(CommercialDocument).where(CommercialDocument.number == inv_num))
        assert inv is None


def test_commit_persists_data_and_repeat_is_idempotent(tmp_path, sample_payload):
    """Verify commit stores data and second run reuses existing records without duplicating."""
    if not settings.database_url.startswith("postgresql"):
        pytest.skip("Requires PostgreSQL database")

    script = Path(__file__).resolve().parents[2] / "scripts" / "migrate_legacy.py"
    data_file = tmp_path / "data.json"
    report_file = tmp_path / "commit_report.json"
    data_file.write_text(json.dumps(sample_payload, ensure_ascii=False))

    # First run: COMMIT
    res = subprocess.run(
        [
            sys.executable,
            str(script),
            str(data_file),
            "--commit",
            "--report",
            str(report_file),
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    report1 = json.loads(report_file.read_text())
    assert report1["dry_run"] is False
    assert report1["stats"]["counterparties_created"] == 1
    assert report1["stats"]["invoices_created"] == 1
    assert report1["stats"]["payments_created"] == 2

    # Financial check in report
    fin = report1["financial_summary"]["EUR"]
    assert Decimal(fin["invoiced"]) == Decimal("5000.00")
    assert Decimal(fin["paid"]) == Decimal("6200.00")
    assert Decimal(fin["allocated"]) == Decimal("5000.00")
    assert Decimal(fin["outstanding_receivables"]) == Decimal("0")
    assert Decimal(fin["unallocated_advances"]) == Decimal("1200.00")

    # Second run: Repeat with --commit -> must be 100% idempotent
    res2 = subprocess.run(
        [
            sys.executable,
            str(script),
            str(data_file),
            "--commit",
            "--report",
            str(report_file),
        ],
        capture_output=True,
        text=True,
    )
    assert res2.returncode == 0, res2.stderr
    report2 = json.loads(report_file.read_text())
    # All records reused, 0 created
    assert report2["stats"].get("counterparties_created", 0) == 0
    assert report2["stats"].get("counterparties_reused", 0) == 1
    assert report2["stats"].get("invoices_reused", 0) == 1
    assert report2["stats"].get("payments_reused", 0) == 2


def test_discrepancy_reporting_for_missing_dates_and_references(tmp_path):
    """Verify missing dates and nonexistent references are explicitly reported in discrepancies."""
    if not settings.database_url.startswith("postgresql"):
        pytest.skip("Requires PostgreSQL database")

    bad_payload = {
        "counterparties": [
            {"legacy_id": "C_OK", "name": "Valid Client"}
        ],
        "sellers": [
            {"legacy_id": "S_OK", "name": "Valid Seller", "currency": "RUB"}
        ],
        "requests": [
            {
                "legacy_id": "R_BAD_DATE",
                "number": "REQ-BAD-DATE",
                "title": "Bad Date Request",
                "client_legacy_id": "C_OK",
                "created_at": "not-a-valid-date",
            },
            {
                "legacy_id": "R_BAD_CLIENT",
                "number": "REQ-BAD-CLIENT",
                "title": "Bad Client Request",
                "client_legacy_id": "NONEXISTENT_CLIENT",
                "created_at": "2021-01-01T00:00:00Z",
            },
        ],
        "payments": [
            {
                "external_id": "PAY-NO-DATE",
                "amount": "1000.00",
                "currency": "RUB",
                "client_legacy_id": "C_OK",
                "seller_legacy_id": "S_OK",
                # payment_date is intentionally omitted
            },
            {
                "external_id": "PAY-BAD-SELLER",
                "amount": "500.00",
                "currency": "RUB",
                "payment_date": "2021-02-01",
                "client_legacy_id": "C_OK",
                "seller_legacy_id": "NONEXISTENT_SELLER",
            },
        ],
    }

    script = Path(__file__).resolve().parents[2] / "scripts" / "migrate_legacy.py"
    data_file = tmp_path / "bad_data.json"
    report_file = tmp_path / "discrepancies.json"
    data_file.write_text(json.dumps(bad_payload, ensure_ascii=False))

    res = subprocess.run(
        [
            sys.executable,
            str(script),
            str(data_file),
            "--dry-run",
            "--report",
            str(report_file),
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    report = json.loads(report_file.read_text())
    discrepancies = report["discrepancies"]
    assert len(discrepancies) >= 4

    reasons = " ".join(d["reason"] for d in discrepancies)
    assert "Invalid or missing created_at" in reasons
    assert "Unknown client_legacy_id" in reasons
    assert "Missing or invalid payment_date" in reasons
    assert "Unknown seller_legacy_id" in reasons
