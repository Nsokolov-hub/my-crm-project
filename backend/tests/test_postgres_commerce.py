"""Commerce concurrency acceptance checks against real PostgreSQL.

Run: PYTHONPATH=backend .venv/bin/pytest backend/tests/test_postgres_commerce.py -q
Uses POSTGRES_TEST_DATABASE_URL when set, otherwise the configured database URL.
Every test creates and drops its own schema; public tables are never used.
For an offline unit-test run, exclude the postgres marker with ``-m 'not postgres'``.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier, Lock
from uuid import uuid4

import pytest
from alembic import command as migrations
from alembic.config import Config
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema
from test_commerce import approve, command, fund, get, prepare, wave

from app.commerce.models import (
    Manufacturer,
    Payment,
    PaymentAllocation,
    Product,
    Substance,
    WaveAllocation,
)
from app.commerce.routes import router
from app.core.config import settings
from app.core.db import Base, get_db
from app.core.errors import DomainError
from app.core.models import AuditEvent, IdempotencyRecord, PermissionGrant, User
from app.core.security import PERMISSIONS, current_user
from app.crm.models import Counterparty, Request, RequestItem, Seller

pytestmark = pytest.mark.postgres


@pytest.fixture
def postgres_commerce(tmp_path, monkeypatch):
    url = make_url(os.environ.get("POSTGRES_TEST_DATABASE_URL", settings.database_url))
    if url.get_backend_name() != "postgresql":
        pytest.skip("The postgres acceptance gate requires a PostgreSQL database URL")
    schema = f"test_commerce_{uuid4().hex}"
    admin = create_engine(url, connect_args={"connect_timeout": 5}, hide_parameters=True)
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
    except OperationalError:
        admin.dispose()
        pytest.fail(
            "PostgreSQL is unavailable. Start the local database or set POSTGRES_TEST_DATABASE_URL.",
            pytrace=False,
        )

    engine = create_engine(
        url,
        connect_args={
            "connect_timeout": 5,
            "options": f"-csearch_path={schema} -cstatement_timeout=20000 -clock_timeout=10000",
        },
        pool_size=8,
        max_overflow=0,
        hide_parameters=True,
    )
    try:
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT current_schema()")) == schema
            config = Config('backend/alembic.ini')
            config.attributes['connection'] = connection
            migrations.upgrade(config, 'head')
        sessions = sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(settings, "storage_dir", tmp_path / "files")
        with sessions() as db:
            owner = User(email="owner@example.test", name="Владелец", password_hash="fixture-only")
            db.add(owner)
            db.flush()
            db.add_all(PermissionGrant(user_id=owner.id, code=code, scope="all") for code in PERMISSIONS)
            seller = Seller(name="Тестовый продавец", currency="RUB", details={"tax_id": "TEST"})
            client = Counterparty(name="Клиент", kind="client", owner_id=owner.id)
            supplier = Counterparty(name="Поставщик", kind="supplier", owner_id=owner.id)
            db.add_all([seller, client, supplier])
            db.flush()
            request = Request(
                number="TEST-PG-001",
                title="Конкурентная тестовая заявка",
                seller_id=seller.id,
                client_id=client.id,
                owner_id=owner.id,
                is_test=True,
            )
            db.add(request)
            db.flush()
            item = RequestItem(
                request_id=request.id,
                description="Этанол для теста",
                cas="64-17-5",
                quantity=Decimal("10"),
                unit="pcs",
                purity="99%",
                packaging="1 штука",
                allow_analogue=False,
            )
            db.add(item)
            db.commit()
            state = {
                "owner_id": owner.id,
                "seller_id": seller.id,
                "client_id": client.id,
                "supplier_id": supplier.id,
                "request_id": request.id,
                "item_id": item.id,
                "sessions": sessions,
                "barrier": None,
                "backend_pids": [],
            }

        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        pid_lock = Lock()

        @app.exception_handler(DomainError)
        async def domain_error(_request, exc):
            return JSONResponse(
                {"code": exc.code, "message": exc.message, "field": exc.field},
                status_code=exc.status,
            )

        def get_session():
            with sessions() as db:
                try:
                    if state["barrier"] is not None:
                        # Reserve independent connections and begin every transaction before
                        # releasing any route. No service locks or queries are mocked.
                        pid, active_schema = db.execute(
                            text("SELECT pg_backend_pid(), current_schema()")
                        ).one()
                        assert active_schema == schema
                        with pid_lock:
                            state["backend_pids"].append(pid)
                        state["barrier"].wait(timeout=10)
                    yield db
                    db.commit()
                except Exception:
                    db.rollback()
                    raise

        app.dependency_overrides[get_db] = get_session
        app.dependency_overrides[current_user] = lambda: owner
        state["app"] = app
        with TestClient(app) as api:
            state["client"] = api
            yield state
    finally:
        engine.dispose()
        try:
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        finally:
            admin.dispose()


def concurrent_posts(env, requests):
    """Perform simultaneous API calls and prove they used distinct PostgreSQL sessions."""
    env["barrier"] = Barrier(len(requests))
    env["backend_pids"] = []

    def post(request):
        path, payload = request
        with TestClient(env["app"]) as client:
            return client.post("/api/v1" + path, json=payload)

    try:
        with ThreadPoolExecutor(max_workers=len(requests)) as executor:
            futures = [executor.submit(post, request) for request in requests]
            responses = [future.result(timeout=30) for future in futures]
    finally:
        env["barrier"] = None
    assert len(env["backend_pids"]) == len(requests)
    assert len(set(env["backend_pids"])) == len(requests)
    return responses


def payment_payload(invoice_id, amount="1000", number="PAY-PG-001"):
    return {
        "idempotency_key": str(uuid4()),
        "invoice_id": invoice_id,
        "amount": amount,
        "currency": "RUB",
        "payment_date": date.today().isoformat(),
        "number": number,
    }


def confirmed_payment(env, state, amount="1000", number="PAY-PG-001"):
    payment = command(
        env,
        f"/requests/{env['request_id']}/payments",
        payment_payload(state["invoice"]["id"], amount, number),
    )
    return command(env, f"/payments/{payment['id']}/confirm", {"version": 1})


def test_parallel_allocations_cannot_spend_the_same_payment_twice(postgres_commerce):
    env = postgres_commerce
    state = prepare(env, with_invoice=True)
    payment = confirmed_payment(env, state)
    path = f"/payments/{payment['id']}/allocate"
    body = {
        "version": payment["version"],
        "allocations": [{"invoice_id": state["invoice"]["id"], "amount": "700"}],
    }
    responses = concurrent_posts(env, [(path, {**body, "idempotency_key": str(uuid4())}) for _ in range(2)])
    assert sorted(response.status_code for response in responses) == [200, 409]
    rejected = next(response.json() for response in responses if response.status_code == 409)
    assert rejected["code"] == "VERSION_CONFLICT"
    saved = get(env, f"/requests/{env['request_id']}/payments")["items"][0]
    assert Decimal(saved["allocated"]) == Decimal("700")
    assert Decimal(saved["unallocated"]) == Decimal("300")
    with env["sessions"]() as db:
        allocated = db.scalar(
            select(func.sum(PaymentAllocation.amount)).where(PaymentAllocation.payment_id == payment["id"])
        )
        assert allocated == Decimal("700") <= db.get(Payment, payment["id"]).amount
    retry = command(env, path, {**body, "version": saved["version"]}, expected=422)
    assert retry["code"] == "PAYMENT_OVERALLOCATED"


def test_parallel_payments_cannot_overpay_one_invoice(postgres_commerce):
    env = postgres_commerce
    state = prepare(env, with_invoice=True)
    payments = [confirmed_payment(env, state, "2000", f"PAY-PG-{index}") for index in range(2)]
    responses = concurrent_posts(
        env,
        [
            (
                f"/payments/{payment['id']}/allocate",
                {
                    "idempotency_key": str(uuid4()),
                    "version": payment["version"],
                    "allocations": [{"invoice_id": state["invoice"]["id"], "amount": "2000"}],
                },
            )
            for payment in payments
        ],
    )
    assert sorted(response.status_code for response in responses) == [200, 422]
    assert next(response.json() for response in responses if response.status_code == 422)["code"] == (
        "INVOICE_OVERPAYMENT"
    )
    invoice = get(env, f"/requests/{env['request_id']}/invoices")["items"][0]
    assert Decimal(invoice["paid"]) == Decimal("2000")
    assert Decimal(invoice["remaining"]) == Decimal("1593.75")
    with env["sessions"]() as db:
        assert (
            db.scalar(
                select(func.sum(PaymentAllocation.amount)).where(
                    PaymentAllocation.invoice_id == state["invoice"]["id"]
                )
            )
            == Decimal("2000")
            <= Decimal(invoice["total"])
        )


def test_parallel_waves_cannot_exceed_approved_quantity(postgres_commerce):
    env = postgres_commerce
    state = prepare(env, with_invoice=True)
    fund(env, state)
    approval = approve(env, state)
    waves = [wave(env, f"PG-WAVE-{index}") for index in range(2)]
    body = {
        "execution_id": state["execution"]["id"],
        "approval_id": approval["id"],
        "quantity": "6",
    }
    responses = concurrent_posts(
        env,
        [(f"/waves/{item['id']}/allocations", {**body, "idempotency_key": str(uuid4())}) for item in waves],
    )
    assert sorted(response.status_code for response in responses) == [200, 422]
    assert next(response.json() for response in responses if response.status_code == 422)["code"] == (
        "WAVE_OVERALLOCATED"
    )
    with env["sessions"]() as db:
        allocated = db.scalar(
            select(func.sum(WaveAllocation.quantity)).where(
                WaveAllocation.execution_id == state["execution"]["id"],
                WaveAllocation.active.is_(True),
            )
        )
        assert allocated == Decimal("6") <= Decimal(approval["snapshot"]["lines"][0]["quantity"])
    command(env, f"/waves/{waves[0]['id']}/allocations", {**body, "quantity": "4"})
    saved = get(env, f"/requests/{env['request_id']}/executions")["items"][0]
    assert Decimal(saved["allocated"]) == Decimal("10")
    assert Decimal(saved["unallocated"]) == 0
    assert (
        command(env, f"/waves/{waves[1]['id']}/allocations", {**body, "quantity": "1"}, expected=422)["code"]
        == "WAVE_OVERALLOCATED"
    )


def test_parallel_product_imports_resolve_to_one_variant(postgres_commerce):
    env = postgres_commerce
    body = {
        "name": "Этанол",
        "cas": "64-17-5",
        "manufacturer": "Фабрика",
        "purity": "99%",
        "packaging": "25 г",
        "unit": "g",
        "package_quantity": "25",
    }
    responses = concurrent_posts(
        env,
        [
            ("/catalog/products", {**body, "manufacturer": maker})
            for maker in ("Фабрика", " фабрика  ", "ФАБРИКА", "  Фабрика", "фабрика", "Фабрика ")
        ],
    )
    assert [response.status_code for response in responses] == [200] * 6
    assert len({response.json()["id"] for response in responses}) == 1
    with env["sessions"]() as db:
        for model in (Product, Substance, Manufacturer):
            assert db.scalar(select(func.count()).select_from(model)) == 1
    different_package = env["client"].post(
        "/api/v1/catalog/products", json={**body, "packaging": "100 г", "package_quantity": "100"}
    )
    assert different_package.status_code == 200
    assert different_package.json()["id"] != responses[0].json()["id"]


def test_parallel_retries_return_one_payment_and_one_business_event(postgres_commerce):
    env = postgres_commerce
    state = prepare(env, with_invoice=True)
    path = f"/requests/{env['request_id']}/payments"
    body = payment_payload(state["invoice"]["id"])
    responses = concurrent_posts(env, [(path, body) for _ in range(6)])
    assert [response.status_code for response in responses] == [200] * 6
    assert all(response.json() == responses[0].json() for response in responses)
    with env["sessions"]() as db:
        assert db.scalar(select(func.count()).select_from(Payment)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(IdempotencyRecord.key == body["idempotency_key"])
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.entity_type == "payment", AuditEvent.action == "declare")
            )
            == 1
        )


def test_parallel_reuse_of_key_with_different_amount_conflicts(postgres_commerce):
    env = postgres_commerce
    state = prepare(env, with_invoice=True)
    path = f"/requests/{env['request_id']}/payments"
    body = payment_payload(state["invoice"]["id"])
    responses = concurrent_posts(env, [(path, body), (path, {**body, "amount": "2000"})])
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert next(response.json() for response in responses if response.status_code == 409)["code"] == (
        "IDEMPOTENCY_CONFLICT"
    )
    winner = next(response.json() for response in responses if response.status_code == 200)
    with env["sessions"]() as db:
        assert db.scalar(select(func.count()).select_from(Payment)) == 1
        assert db.get(Payment, winner["id"]).amount == Decimal(winner["amount"])
