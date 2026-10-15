"""Commerce routes with a fixed all-rights actor, one request, item, client, supplier and seller."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.commerce.calculator import example_profile
from app.commerce.routes import router
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import DomainError
from app.core.models import PermissionGrant, User
from app.core.security import PERMISSIONS, current_user
from app.crm.models import Counterparty, Request, RequestItem, Seller


@pytest.fixture
def commerce(tmp_path, monkeypatch, database):
    sessions = sessionmaker(database, expire_on_commit=False)
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
        "profile_id": profile["id"], "delivery_days": 14,
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
    with env["sessions"]() as db:
        payload["request_version"] = db.get(Request, env["request_id"]).version
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
            "supplier_id": env["supplier_id"],
            "route": "Тестовый маршрут",
            "origin_country": "Условная страна",
            "owner_id": env["owner_id"],
            "close_date": date.today().isoformat(),
            "departure_date": date.today().isoformat(),
            "arrival_date": (date.today() + timedelta(days=10)).isoformat(),
        },
    )
