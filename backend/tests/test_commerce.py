"""A03–A18 service/API acceptance checks; PostgreSQL concurrency is a separate gate."""

import copy
import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.commerce.calculator import (
    convert,
    dec,
    distribute,
    example_profile,
    expression,
    validate_cas,
)
from app.commerce.models import Quote
from app.commerce.routes import router
from app.core.config import settings
from app.core.db import Base, get_db
from app.core.errors import DomainError
from app.core.models import AuditEvent, PermissionGrant, User
from app.core.security import PERMISSIONS, current_user
from app.crm.models import Counterparty, Request, RequestItem, Seller


@pytest.fixture
def commerce(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "files")
    with sessions() as db:
        owner = User(email="owner@example.test", name="Владелец", password_hash="fixture-only")
        other = User(email="other@example.test", name="Менеджер", password_hash="fixture-only")
        db.add_all([owner, other])
        db.flush()
        for code in PERMISSIONS:
            db.add(PermissionGrant(user_id=owner.id, code=code, scope="all"))
        for code in ("requests.read", "catalog.read", "documents.write", "exports.download"):
            db.add(PermissionGrant(user_id=other.id, code=code, scope="own"))
        seller = Seller(name="Тестовый продавец", currency="RUB", details={"tax_id": "TEST"})
        client = Counterparty(name="Клиент", kind="client", owner_id=owner.id)
        supplier = Counterparty(name="Поставщик", kind="supplier", owner_id=owner.id)
        db.add_all([seller, client, supplier])
        db.flush()
        request = Request(
            number="TEST-001",
            title="Тестовая заявка",
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
            "user_id": owner.id,
            "owner_id": owner.id,
            "other_id": other.id,
            "seller_id": seller.id,
            "client_id": client.id,
            "supplier_id": supplier.id,
            "request_id": request.id,
            "item_id": item.id,
            "sessions": sessions,
        }
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    @app.exception_handler(DomainError)
    async def domain_error(_request, exc):
        return JSONResponse(
            {"code": exc.code, "message": exc.message, "field": exc.field}, status_code=exc.status
        )

    def get_session():
        with sessions() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    def actor():
        with sessions() as db:
            return db.get(User, state["user_id"])

    app.dependency_overrides[get_db] = get_session
    app.dependency_overrides[current_user] = actor
    state["client"] = TestClient(app)
    state["client"].post("/api/v1/setup/wizard", json={"company_name": "Test Company"})
    yield state
    engine.dispose()


def command(env, path, payload=None, expected=200, method="post"):
    data = {"idempotency_key": str(uuid4()), **(payload or {})}
    response = getattr(env["client"], method)("/api/v1" + path, json=data)
    assert response.status_code == expected, response.text
    return response.json()


def get(env, path, expected=200):
    response = env["client"].get("/api/v1" + path)
    assert response.status_code == expected, response.text
    return response.json()


def prepare(env, with_invoice=False):
    product = (
        env["client"]
        .post(
            "/api/v1/catalog/products",
            json={
                "name": "Этанол",
                "cas": "64-17-5",
                "manufacturer": "Тестовый завод",
                "purity": "99%",
                "packaging": "1 штука",
                "unit": "pcs",
            },
        )
        .json()
    )
    command(
        env,
        f"/catalog/products/{product['id']}/verify",
        {"version": 1, "reason": "CAS и характеристики проверены"},
    )
    quote_payload = {
        "item_id": env["item_id"],
        "item_revision": 1,
        "supplier_id": env["supplier_id"],
        "product_id": product["id"],
        "price": "100",
        "currency": "CNY",
        "price_unit": "pcs",
        "available_quantity": "100",
        "multiple": "1",
        "valid_until": (date.today() + timedelta(days=30)).isoformat(),
    }
    quote = command(env, f"/requests/{env['request_id']}/quotes", quote_payload)
    profile = command(
        env,
        "/profiles",
        {
            "name": "A08: условные ставки",
            "effective_from": date.today().isoformat(),
            "reason": "Условный профиль из ТЗ для проверки арифметики",
            "definition": example_profile(),
        },
    )
    command(env, f"/profiles/{profile['id']}/publish")
    payload = {
        "request_version": 1,
        "profile_id": profile["id"],
        "selections": [{"quote_id": quote["id"], "quote_revision": 1, "quantity": "10", "unit": "pcs"}],
        "rates": [
            {
                "currency": "CNY",
                "management_per_unit": "2",
                "quoted_units": "1",
                "date": date.today().isoformat(),
                "source": "Арифметический пример A08",
                "reason": "Синтетический тест",
            }
        ],
        "expenses": [
            {
                "name": name,
                "amount": amount,
                "currency": "RUB",
                "method": "purchase",
                "basis": "Тестовая статья A08",
            }
            for name, amount in (("international", "200"), ("fees", "40"), ("inland", "150"))
        ],
        "reason": "Проверка A08",
    }
    calc = command(env, f"/requests/{env['request_id']}/calculations", payload)
    proposal = command(
        env,
        f"/requests/{env['request_id']}/proposals",
        {
            "calculation_id": calc["id"],
            "valid_until": (date.today() + timedelta(days=15)).isoformat(),
            "terms": "Условная поставка; полная предоплата",
        },
    )
    result = {
        "product": product,
        "quote": quote,
        "quote_payload": quote_payload,
        "profile": profile,
        "calc": calc,
        "calculation_payload": payload,
        "proposal": proposal,
    }
    if with_invoice:
        accepted = command(
            env,
            f"/proposals/{proposal['id']}/accept",
            {
                "version": 1,
                "lines": [{"line_id": quote["id"], "quantity": "10"}],
                "reason": "Клиент подтвердил весь состав",
            },
        )
        execution = accepted["executions"][0]
        invoice = command(
            env,
            f"/requests/{env['request_id']}/invoices",
            {
                "proposal_id": proposal["id"],
                "lines": [{"execution_id": execution["id"], "quantity": "10"}],
                "due_date": (date.today() + timedelta(days=10)).isoformat(),
                "terms": "Полная предоплата",
            },
        )
        result.update(execution=execution, invoice=invoice)
    return result


def fund(env, state, amount="4000"):
    payment = command(
        env,
        f"/requests/{env['request_id']}/payments",
        {
            "invoice_id": state["invoice"]["id"],
            "amount": amount,
            "currency": "RUB",
            "payment_date": date.today().isoformat(),
            "number": "PAY-001",
        },
    )
    payment = command(env, f"/payments/{payment['id']}/confirm", {"version": 1})
    payment = command(
        env,
        f"/payments/{payment['id']}/allocate",
        {"version": 2, "allocations": [{"invoice_id": state["invoice"]["id"], "amount": "3593.75"}]},
    )
    return payment


def approve(env, state):
    approval = command(
        env,
        f"/requests/{env['request_id']}/approvals",
        {"execution_ids": [state["execution"]["id"]], "reviewer_id": env["owner_id"]},
    )
    return command(env, f"/approvals/{approval['id']}/decision", {"version": 1, "decision": "approved"})


def wave(env, number):
    return command(
        env,
        "/waves",
        {
            "number": number,
            "route": "Тестовый маршрут",
            "origin_country": "Условная страна",
            "owner_id": env["owner_id"],
            "close_date": date.today().isoformat(),
            "departure_date": date.today().isoformat(),
            "arrival_date": (date.today() + timedelta(days=10)).isoformat(),
        },
    )


def test_a05_cas_and_units():
    assert validate_cas("64-17-5") == "64-17-5"
    assert validate_cas(None, "Смесь") is None
    for value in ("64-17-4", "000-12-3", "abc"):
        with pytest.raises(DomainError):
            validate_cas(value)
    with pytest.raises(DomainError):
        validate_cas(None)
    assert convert(Decimal("1000"), "g", "kg") == Decimal("1")
    with pytest.raises(DomainError):
        convert(Decimal("1000"), "g", "l")


@pytest.mark.parametrize(
    "source",
    [
        "__import__('os').system('id')",
        "x.__class__",
        "sum([1])",
        "1 ** 9999999",
        "[x for x in y]",
        "True",
        "{'a':1}",
    ],
)
def test_untrusted_formula_cannot_execute(source):
    with pytest.raises(DomainError):
        expression(source, {"x": Decimal("1")})


def test_a08_rounding_distribution_and_no_binary_float():
    assert expression("0.1 + 0.2", {}) == Decimal("0.3")
    with pytest.raises(DomainError):
        dec(0.1)
    assert distribute(
        Decimal("1.00"), {"b": Decimal("1"), "a": Decimal("1"), "c": Decimal("1")}, Decimal("0.01"), "half_up"
    ) == {"a": Decimal("0.33"), "b": Decimal("0.34"), "c": Decimal("0.33")}
    with pytest.raises(DomainError):
        distribute(Decimal("1"), {"a": Decimal("0")}, Decimal("0.01"), "half_up")

def test_a11_partial_amount_rounding():
    bases = {"p1": Decimal("1"), "p2": Decimal("1"), "p3": Decimal("1"), "p4": Decimal("1")}
    result = distribute(Decimal("0.02"), bases, Decimal("0.01"), "half_up")
    assert result == {"p1": Decimal("0.01"), "p2": Decimal("0.00"), "p3": Decimal("0.01"), "p4": Decimal("0.00")}


def test_a03_rfq_saved_file_not_sent_and_omits_finances(commerce):
    env = commerce
    rfq = command(
        env,
        f"/requests/{env['request_id']}/rfqs",
        {
            "supplier_id": env["supplier_id"],
            "item_ids": [env["item_id"]],
            "response_due": date.today().isoformat(),
        },
    )
    content = env["client"].get(f"/api/v1/rfqs/{rfq['id']}/file")
    assert content.status_code == 200
    book = load_workbook(io.BytesIO(content.content))
    assert book.active.cell(2, 3).value == "64-17-5"
    assert "цена" not in " ".join(str(cell.value) for cell in book.active[1]).lower()
    assert get(env, f"/requests/{env['request_id']}/rfqs")["items"][0]["sent_at"] is None
    assert env["client"].get(f"/api/v1/rfqs/{rfq['id']}/file").content == content.content


def test_a04_product_variant_deduplication(commerce):
    env = commerce
    body = {
        "name": "Этанол",
        "cas": "64-17-5",
        "manufacturer": "Фабрика",
        "purity": "99%",
        "packaging": "25 г",
        "unit": "g",
        "package_quantity": "25",
    }
    first = env["client"].post("/api/v1/catalog/products", json=body).json()
    second = (
        env["client"].post("/api/v1/catalog/products", json={**body, "manufacturer": " фабрика  "}).json()
    )
    third = (
        env["client"]
        .post("/api/v1/catalog/products", json={**body, "packaging": "100 г", "package_quantity": "100"})
        .json()
    )
    assert first["id"] == second["id"]
    assert third["id"] != first["id"]
    assert not first["verified"]


def test_a08_a09_a10_snapshot_arithmetic_idempotency_and_conflict(commerce):
    env = commerce
    state = prepare(env)
    result = state["calc"]["snapshot"]
    assert result["totals"]["total"] == "3593.75"
    detail = result["lines"][0]["detail"]
    assert Decimal(detail["cost"]) == Decimal("2500")
    assert Decimal(detail["cash_need"]) == Decimal("2731")
    key = str(uuid4())
    body = {**state["calculation_payload"], "idempotency_key": key}
    first = command(env, f"/requests/{env['request_id']}/calculations", body)
    second = command(env, f"/requests/{env['request_id']}/calculations", body)
    assert first["id"] == second["id"]
    assert (
        command(
            env, f"/requests/{env['request_id']}/calculations", {**body, "reason": "Изменение"}, expected=409
        )["code"]
        == "IDEMPOTENCY_CONFLICT"
    )
    command(
        env,
        f"/quotes/{state['quote']['id']}/revise",
        {**state["quote_payload"], "price": "200", "revision_reason": "Новая цена поставщика"},
    )
    saved = get(env, f"/requests/{env['request_id']}/calculations")["items"]
    assert all(row["snapshot"]["totals"]["total"] == "3593.75" for row in saved)
    assert (
        command(
            env, f"/requests/{env['request_id']}/calculations", state["calculation_payload"], expected=409
        )["code"]
        == "QUOTE_REVISION_CONFLICT"
    )


def test_a06_disallows_mixed_supplier_currency(commerce):
    env = commerce
    state = prepare(env)
    second = command(
        env, f"/requests/{env['request_id']}/quotes", {**state["quote_payload"], "currency": "RUB"}
    )
    payload = copy.deepcopy(state["calculation_payload"])
    payload["selections"].append(
        {"quote_id": second["id"], "quote_revision": 1, "quantity": "1", "unit": "pcs"}
    )
    assert (
        command(env, f"/requests/{env['request_id']}/calculations/preview", payload, expected=422)["code"]
        == "QUOTE_GROUP"
    )


def test_a07_expired_quotes_and_analogue_consent(commerce):
    env = commerce
    state = prepare(env)
    expired = command(
        env,
        f"/requests/{env['request_id']}/quotes",
        {**state["quote_payload"], "valid_until": (date.today() - timedelta(days=1)).isoformat()},
    )
    payload = copy.deepcopy(state["calculation_payload"])
    payload["selections"][0]["quote_id"] = expired["id"]
    assert (
        command(env, f"/requests/{env['request_id']}/calculations/preview", payload, expected=422)["code"]
        == "QUOTE_EXPIRED"
    )
    with env["sessions"]() as db:
        quote = db.get(Quote, state["quote"]["id"])
        quote.is_analogue = True
        db.commit()
    assert (
        command(
            env,
            f"/proposals/{state['proposal']['id']}/accept",
            {
                "version": 1,
                "lines": [{"line_id": state["quote"]["id"], "quantity": "1"}],
                "reason": "Частичное принятие",
            },
            expected=422,
        )["code"]
        == "ANALOGUE_APPROVAL"
    )


def test_a11_a12_partial_acceptance_and_invoice_snapshot(commerce):
    env = commerce
    state = prepare(env)
    accepted = command(
        env,
        f"/proposals/{state['proposal']['id']}/accept",
        {
            "version": 1,
            "lines": [{"line_id": state["quote"]["id"], "quantity": "6"}],
            "reason": "Клиент принимает часть",
        },
    )
    assert (
        command(
            env,
            f"/proposals/{state['proposal']['id']}/accept",
            {
                "version": 2,
                "lines": [{"line_id": state["quote"]["id"], "quantity": "5"}],
                "reason": "Превышение потребности",
            },
            expected=422,
        )["code"]
        == "ACCEPTANCE_EXCEEDED"
    )
    execution_id = accepted["executions"][0]["id"]
    invoice_body = {
        "proposal_id": state["proposal"]["id"],
        "lines": [{"execution_id": execution_id, "quantity": "6"}],
        "due_date": date.today().isoformat(),
        "terms": "Оплата части",
    }
    invoice = command(env, f"/requests/{env['request_id']}/invoices", invoice_body)
    assert Decimal(invoice["total"]) == Decimal("2156.25")
    assert (
        command(env, f"/requests/{env['request_id']}/invoices", invoice_body, expected=422)["code"]
        == "QUANTITY_EXCEEDED"
    )
    original = env["client"].get(f"/api/v1/documents/{invoice['id']}/file").content
    assert original.startswith(b"%PDF")
    with env["sessions"]() as db:
        client = db.get(Counterparty, env["client_id"])
        client.name = "Изменённый клиент"
        db.commit()
    assert env["client"].get(f"/api/v1/documents/{invoice['id']}/file").content == original


def test_a13_a14_payments_partial_and_unallocated_overpayment(commerce):
    env = commerce
    state = prepare(env, with_invoice=True)
    body = {
        "invoice_id": state["invoice"]["id"],
        "amount": "4000",
        "currency": "RUB",
        "payment_date": date.today().isoformat(),
        "number": "PAY-001",
        "external_id": "bank-001",
    }
    payment = command(env, f"/requests/{env['request_id']}/payments", body)
    assert get(env, f"/requests/{env['request_id']}/invoices")["items"][0]["payment_status"] == "unpaid"
    assert (
        command(
            env,
            f"/payments/{payment['id']}/allocate",
            {"version": 1, "allocations": [{"invoice_id": state["invoice"]["id"], "amount": "1000"}]},
            expected=422,
        )["code"]
        == "PAYMENT_UNCONFIRMED"
    )
    command(env, f"/payments/{payment['id']}/confirm", {"version": 1})
    payment = command(
        env,
        f"/payments/{payment['id']}/allocate",
        {"version": 2, "allocations": [{"invoice_id": state["invoice"]["id"], "amount": "1000"}]},
    )
    assert get(env, f"/requests/{env['request_id']}/invoices")["items"][0]["payment_status"] == "partial"
    assert (
        command(
            env,
            f"/payments/{payment['id']}/allocate",
            {"version": 3, "allocations": [{"invoice_id": state["invoice"]["id"], "amount": "3000"}]},
            expected=422,
        )["code"]
        == "INVOICE_OVERPAYMENT"
    )
    payment = command(
        env,
        f"/payments/{payment['id']}/allocate",
        {"version": 3, "allocations": [{"invoice_id": state["invoice"]["id"], "amount": "2593.75"}]},
    )
    assert Decimal(payment["unallocated"]) == Decimal("406.25")
    assert (
        command(env, f"/requests/{env['request_id']}/payments", body, expected=409)["code"]
        == "DUPLICATE_PAYMENT"
    )


def test_a15_a16_a17_a18_approval_reversal_and_partial_delivery(commerce):
    env = commerce
    state = prepare(env, with_invoice=True)
    assert (
        command(
            env,
            f"/requests/{env['request_id']}/approvals",
            {"execution_ids": [state["execution"]["id"]], "reviewer_id": env["owner_id"]},
            expected=422,
        )["code"]
        == "FUNDING_REQUIRED"
    )
    payment = fund(env, state)
    first_approval = command(
        env,
        f"/requests/{env['request_id']}/approvals",
        {"execution_ids": [state["execution"]["id"]], "reviewer_id": env["owner_id"]},
    )
    command(
        env,
        f"/approvals/{first_approval['id']}/decision",
        {"version": 1, "decision": "returned", "reason": "Проверить сроки"},
    )
    approval = approve(env, state)
    first, second, third = wave(env, "03"), wave(env, "04"), wave(env, "05")
    six = command(
        env,
        f"/waves/{first['id']}/allocations",
        {"execution_id": state["execution"]["id"], "approval_id": approval["id"], "quantity": "6"},
    )
    four = command(
        env,
        f"/waves/{second['id']}/allocations",
        {"execution_id": state["execution"]["id"], "approval_id": approval["id"], "quantity": "4"},
    )
    assert (
        command(
            env,
            f"/waves/{third['id']}/allocations",
            {"execution_id": state["execution"]["id"], "approval_id": approval["id"], "quantity": "1"},
            expected=422,
        )["code"]
        == "WAVE_OVERALLOCATED"
    )
    for kind in ("shipped", "arrived", "delivered"):
        command(
            env,
            f"/allocations/{six['id']}/events",
            {
                "kind": kind,
                "quantity": "6",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "reason": "Подтверждена тестовая партия",
            },
        )
    assert (
        command(
            env,
            f"/allocations/{six['id']}/transfer",
            {"version": 1, "target_wave_id": third["id"], "quantity": "1", "reason": "Недопустимый перенос"},
            expected=422,
        )["code"]
        == "TRANSFER_SHIPPED"
    )
    command(
        env,
        f"/allocations/{four['id']}/transfer",
        {
            "version": 1,
            "target_wave_id": third["id"],
            "quantity": "4",
            "reason": "Плановая партия перенесена",
        },
    )
    execution = get(env, f"/requests/{env['request_id']}/executions")["items"][0]
    assert execution["state"] == "partial"
    assert Decimal(execution["delivered"]) == Decimal("6")
    assert Decimal(execution["unallocated"]) == 0
    payment = command(
        env,
        f"/payments/{payment['id']}/reverse",
        {
            "version": 3,
            "amount": "100",
            "allocation_id": payment["allocations"][0]["id"],
            "kind": "unallocate",
            "reason": "Исправление распределения",
        },
    )
    execution = get(env, f"/requests/{env['request_id']}/executions")["items"][0]
    assert execution["financing_deficit"] is True
    assert Decimal(execution["delivered"]) == 6


def test_a20_no_financial_fields_or_idor(commerce):
    env = commerce
    state = prepare(env)
    env["user_id"] = env["other_id"]
    get(env, f"/requests/{env['request_id']}/quotes", expected=404)
    assert env["client"].get(f"/api/v1/documents/{state['proposal']['id']}/file").status_code == 404
    with env["sessions"]() as db:
        req = db.get(Request, env["request_id"])
        req.owner_id = env["other_id"]
        db.commit()
    quote = get(env, f"/requests/{env['request_id']}/quotes")["items"][0]
    assert "price" not in quote
    snapshot = get(env, f"/requests/{env['request_id']}/calculations")["items"][0]["snapshot"]
    assert "profile" not in snapshot and "input" not in snapshot
    assert "detail" not in snapshot["lines"][0] and "quote" not in snapshot["lines"][0]
    with env["sessions"]() as db:
        assert db.scalar(select(AuditEvent.id))

def test_financial_permissions_revocation_and_owner_change(commerce):
    env = commerce
    from tests.test_commerce import prepare, command
    from uuid import uuid4
    state = prepare(env, with_invoice=False)
    
    idem_key = str(uuid4())
    
    calc = env["client"].get(f"/api/v1/requests/{env['request_id']}/calculations").json()["items"][0]
    assert "cost" in calc["snapshot"]["lines"][0]["detail"]

    with env["sessions"].begin() as db:
        from app.core.models import PermissionGrant
        db.query(PermissionGrant).filter_by(user_id=env["owner_id"], code="finance.profit.read").delete()
        db.flush()

    replayed = env["client"].get(f"/api/v1/requests/{env['request_id']}/calculations").json()["items"][0]
    assert "cost" not in replayed["snapshot"]["lines"][0]["detail"]
    assert "profit" not in replayed["snapshot"]["lines"][0]["detail"]

    profile_view = env["client"].get(f"/api/v1/profiles").json()["items"][-1]
    assert "formulas" not in profile_view["definition"]

    with env["sessions"].begin() as db:
        from app.crm.models import Request as CRMRequest
        from app.core.models import PermissionGrant
        db.query(PermissionGrant).filter_by(user_id=env["owner_id"], code="requests.read").delete()
        db.add(PermissionGrant(user_id=env["owner_id"], code="requests.read", scope="own"))
        req = db.get(CRMRequest, env["request_id"])
        req.owner_id = env["other_id"]
        db.flush()

    response = env["client"].get(f"/api/v1/requests/{env['request_id']}/calculations")
    assert response.status_code == 404
