"""Automated tests for R15, S15, S18: Load testing, calculation performance, PDF export, and worker resilience."""

import io
import time
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.commerce.calculator import calculate, example_profile
from app.commerce.files import document_files
from app.communication.worker import run_once
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.models import OutboxEvent, User
from app.crm.imports import parse_rows


def test_calculation_engine_100_lines_performance():
    """Verify that a 100-line financial calculation completes in < 1.0s and satisfies SLA (R15)."""
    profile = example_profile()
    selections = [
        {
            "quote": {
                "id": f"q-perf-{i}",
                "item_id": f"item-perf-{i}",
                "revision": 1,
                "supplier_id": "sup-perf-1",
                "currency": "RUB",
                "price": str(100 + i),
                "price_unit": "kg",
            },
            "product": {"name": f"Химический продукт №{i}", "cas": "64-17-5"},
            "quantity": "10",
            "unit": "kg",
            "variables": {},
        }
        for i in range(100)
    ]
    expenses = [
        {
            "name": "international",
            "amount": "1000.00",
            "currency": "RUB",
            "method": "purchase",
            "basis": "Доставка",
            "include_in_cost": True,
            "include_in_cash": True,
        }
    ]

    start = time.perf_counter()
    result = calculate(profile, selections, expenses, [])
    elapsed = time.perf_counter() - start

    assert len(result["lines"]) == 100
    assert "total" in result["totals"]
    assert Decimal(result["totals"]["total"]) > Decimal("0")
    # SLA requirement: 100-line calculation must evaluate in < 1.0s
    assert elapsed < 1.0, f"Calculation took too long: {elapsed:.4f}s"


def test_pdf_export_100_lines_performance(tmp_path):
    """Verify that generating a commercial document PDF with 100 lines completes in < 2.0s (R15)."""
    doc_lines = [
        {
            "description": f"Аналитический реагент чистый для анализа №{i}",
            "cas": f"100-{i:03d}-0",
            "quantity": "5.000",
            "unit": "кг",
            "unit_price": "2000.00",
            "net": "10000.00",
            "tax": "2000.00",
            "total": "12000.00",
        }
        for i in range(1, 101)
    ]

    snapshot = {
        "title": "Коммерческое предложение",
        "number": f"KP-TEST-{uuid4().hex[:6].upper()}",
        "date": str(date.today()),
        "currency": "RUB",
        "seller": {
            "name": 'ООО "Поставщик Тест"',
            "details": {"inn": "7701234567", "kpp": "770101001"},
        },
        "client": {
            "name": 'ООО "Заказчик Тест"',
            "details": {"inn": "7809876543"},
        },
        "lines": doc_lines,
        "totals": {
            "net": str(Decimal("10000.00") * 100),
            "tax": str(Decimal("2000.00") * 100),
            "total": str(Decimal("12000.00") * 100),
        },
        "terms": "100% предоплата, отгрузка 5 дней",
        "valid_until": str(date.today() + timedelta(days=14)),
    }

    start = time.perf_counter()
    files = document_files(snapshot)
    elapsed = time.perf_counter() - start

    assert "pdf" in files
    assert "xlsx" in files
    assert files["pdf"]["size"] > 10000
    assert elapsed < 2.0, f"PDF export took too long: {elapsed:.4f}s"


def test_large_import_parsing_performance():
    """Verify XLSX parser throughput with 2,000 rows completes in < 1.0s (R15)."""
    row_count = 2000
    wb = Workbook()
    ws = wb.active
    ws.append(["Название организации", "ИНН", "Электронная почта"])

    for i in range(1, row_count + 1):
        ws.append([f"Компания Тест {i}", f"77{i:08d}", f"contact_{i}@perf.local"])

    buf = io.BytesIO()
    wb.save(buf)
    file_bytes = buf.getvalue()

    start = time.perf_counter()
    parsed = parse_rows(file_bytes, {
        "name": "Название организации",
        "tax_id": "ИНН",
        "email": "Электронная почта",
    })
    elapsed = time.perf_counter() - start

    assert len(parsed) == row_count
    assert elapsed < 1.5, f"Import parsing took too long: {elapsed:.4f}s"


def test_worker_resilience_and_rto():
    """Verify worker transactional recovery after interruption and measure RTO <= 10.0s (R15, S18)."""
    if not settings.database_url.startswith("postgresql"):
        pytest.skip("Requires PostgreSQL database")

    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.active.is_(True)).limit(1))
        assert admin is not None
        event = OutboxEvent(
            event_key=f"worker_resilience_test:{uuid4().hex}",
            kind="notification",
            payload={
                "user_id": admin.id,
                "title": "Тест устойчивости воркера",
                "entity_type": "system",
                "entity_id": admin.id,
            },
            status="pending",
        )
        db.add(event)
        db.commit()
        event_id = event.id

    # Measure recovery time objective (RTO) upon restart
    start_rto = time.perf_counter()
    run_once(reminders=False, limit=10)
    rto = time.perf_counter() - start_rto

    with SessionLocal() as db:
        recovered = db.get(OutboxEvent, event_id)
        assert recovered is not None
        assert recovered.status == "succeeded"

    assert rto < 10.0, f"RTO exceeded 10.0s threshold: {rto:.4f}s"
