"""Business workflow helpers on top of the ``crm`` fixture: requests, imports, quotes and releases."""

from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy import select

from app.commerce.itemized import itemized_profile
from app.commerce.models import CalculationProfile
from app.crm.imports import xlsx
from app.crm.models import Country, Currency, ProductGroup, Request
from app.crm.table_imports import QUOTE_COLUMNS
from tests.support.crm import login, post


def request(crm):
    with crm["sessions"].begin() as db:
        if not db.scalar(select(ProductGroup.id).where(ProductGroup.slug == "other")):
            db.add(ProductGroup(name="Прочее", slug="other"))
        if not db.scalar(select(Currency.id).where(Currency.code == "RUB")):
            db.add(Currency(name="Рубль", code="RUB"))
    client = post(crm, "/counterparties", {"name": "Рабочий клиент"})
    return post(crm, "/requests", {"title": "Исходная заявка", "client_id": client["id"]})


def preview(crm, request_id, columns, rows, kind="items"):
    response = crm["client"].post(
        f"/api/v1/requests/{request_id}/table-imports/preview",
        files={
            "file": (
                "table.xlsx",
                xlsx([columns, *rows]),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"kind": kind},
    )
    assert response.status_code == 200, response.text
    return response.json()


def confirm(crm, request_id, batch_id, status=200, key=None):
    return post(crm, f"/requests/{request_id}/table-imports/{batch_id}/confirm", {}, status, key=key)


def quote_file(supplier_code, overrides=None):
    row = {
        "source_row": "1",
        "supplier_code": str(supplier_code),
        "article": "0341",
        "name": "Новый товар",
        "manufacturer": "0",
        "cas": "0",
        "linear_formula": "0",
        "purity": "0",
        "packing_value": "0",
        "packing_unit": "0",
        "quantity": "1",
        "unit_price": "100,25",
        "currency": "rub",
        "delivery_days": "14",
    }
    row.update(overrides or {})
    return [row.get(key, "") for key in QUOTE_COLUMNS]


def configured_profile():
    profile = itemized_profile()
    profile["import_country_id"] = "country-test"
    return profile


def cmd(env, path, body=None, status=200, method="post", key=None):
    result = getattr(env["client"], method)(
        "/api/v1" + path, json={**(body or {}), "idempotency_key": key or str(uuid4())}
    )
    assert result.status_code == status, result.text
    return result.json()


def structured(env, mode="RUSSIA", markup="1.5", owner=None):
    login(env)
    req = request(env)
    seller = post(
        env,
        "/sellers",
        {
            "name": "ОГК-ХИМ",
            "currency": "RUB",
            "details": {"Банк": "Банк A", "БИК": "123456789", "Расчётный счёт": "40700000000000000000"},
        },
    )
    supplier = post(
        env,
        "/counterparties",
        {
            "name": "Aozeal",
            "kind": "supplier",
            "email": "sales@aozeal.example",
            "details": {"calculation_type": mode, "contract": "C-123"},
        },
    )
    group = post(env, "/product-groups", {"name": "Стандартные образцы", "slug": "reference_standards"})
    n = post(
        env,
        "/nomenclatures",
        {
            "name": "Sample",
            "article": "A123",
            "manufacturer": "Aozeal",
            "product_group_id": group["id"],
            "packings": [{"value": "100", "unit": "mg"}],
        },
    )
    with env["sessions"].begin() as db:
        db.get(Request, req["id"]).seller_id = seller["id"]
        if owner:
            db.get(Request, req["id"]).owner_id = owner
        country = Country(name="Россия", iso2="RU")
        db.add(country)
        db.flush()
        profile = itemized_profile()
        profile["import_country_id"] = country.id
        saved = CalculationProfile(
            name="2026",
            status="published",
            effective_from=date(2026, 1, 1),
            definition=profile,
            reason="Тест",
            author_id=env["admin"].id,
        )
        db.add(saved)
        db.flush()
        profile_id = saved.id
        currency_id = db.scalar(select(Currency.id).where(Currency.code == "RUB"))
    quote = post(
        env,
        f"/requests/{req['id']}/quote-sheets",
        {
            "supplier_id": supplier["id"],
            "items": [
                {
                    "nomenclature_id": n["id"],
                    "packing_id": n["packings"][0]["id"],
                    "quantity": "1",
                    "unit_price": "1000",
                    "currency_id": currency_id,
                    "delivery_days": 7,
                }
            ],
        },
    )
    req = env["client"].get("/api/v1/requests/" + req["id"]).json()
    if mode == "IMPORT":
        wave = cmd(
            env,
            "/waves",
            {
                "supplier_id": supplier["id"],
                "route": "Москва",
                "origin_country": "RU",
                "owner_id": env["admin"].id,
                "close_date": date.today().isoformat(),
                "departure_date": date.today().isoformat(),
                "arrival_date": (date.today() + timedelta(days=7)).isoformat(),
            },
        )
        req = {
            **req,
            **cmd(
                env,
                f"/requests/{req['id']}/wave",
                {"request_version": req["version"], "wave_id": wave["id"]},
                method="put",
            ),
        }
    data = {
        "request_version": req["version"],
        "profile_id": profile_id,
        "selections": [{"quote_item_id": quote["items"][0]["id"], "markup_coefficient": markup}],
        "expenses": [],
        "delivery_required": False,
    }
    return req, data, seller, supplier


def wave_row(env, wave_id):
    return next(row for row in env["client"].get("/api/v1/waves").json()["items"] if row["id"] == wave_id)


def release(env, req, calculation, quantity):
    proposal = cmd(env, f"/requests/{req['id']}/proposals", {
        "calculation_id": calculation["id"], "valid_until": (date.today() + timedelta(days=15)).isoformat(),
        "terms": "Отсрочка, оплата после поставки",
    })
    accepted = cmd(env, f"/proposals/{proposal['id']}/accept", {
        "version": proposal["version"], "reason": "Клиент подтвердил новый объём",
        "lines": [{"line_id": calculation["snapshot"]["lines"][0]["line_id"], "quantity": quantity}],
    })
    return proposal, accepted["executions"][0]


def handoff(env, req, execution):
    approval = cmd(env, f"/requests/{req['id']}/approvals", {
        "execution_ids": [execution["id"]], "reviewer_id": env["admin"].id,
    })
    return cmd(env, f"/approvals/{approval['id']}/decision", {
        "version": approval["version"], "decision": "approved", "reason": "Передать с отсрочкой",
    })


def issue(env, req, calculation, days=30):
    return cmd(env, f"/requests/{req['id']}/proposals", {
        "calculation_id": calculation["id"],
        "valid_until": (date.today() + timedelta(days=days)).isoformat(),
        "terms": "Оплата согласно договору.",
    })
