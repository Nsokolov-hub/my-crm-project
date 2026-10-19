"""Who was asked for prices and who answered, down to request positions and brands."""

from datetime import date, timedelta
from uuid import uuid4

from app.business.models import SupplierMail
from app.commerce.itemized import itemized_profile
from app.commerce.models import CalculationProfile
from app.core.config import settings
from app.core.db import utcnow
from app.crm.models import Currency
from app.crm.table_imports import QUOTE_COLUMNS
from tests.support.business import cmd, confirm, preview, quote_file, request
from tests.support.crm import login, post

TABLE = ["Name", "Producer", "Qty"]


def get(env, path, status=200):
    response = env["client"].get("/api/v1" + path)
    assert response.status_code == status, response.text
    return response.json()


def setup(env, monkeypatch):
    login(env)
    req = request(env)
    post(env, "/manufacturers", {"name": "Merck"})
    post(env, "/manufacturers", {"name": "Waters"})
    raw = preview(env, req["id"], TABLE, [["Atropine", "Merck", "1"], ["Caffeine", "merck", "1"],
                                         ["Column C18", "Waters", "1"]])
    confirm(env, req["id"], raw["id"])
    items = get(env, f"/requests/{req['id']}/items")["items"]
    alpha = post(env, "/counterparties", {"name": "Alpha Chem", "kind": "supplier", "email": "alpha@example.com"})
    beta = post(env, "/counterparties", {"name": "Beta Labs", "kind": "supplier", "email": "beta@example.com"})
    monkeypatch.setattr(settings, "smtp_host", "smtp.example")
    monkeypatch.setattr(settings, "smtp_from", "crm@example.com")
    mails = cmd(env, f"/requests/{req['id']}/supplier-mail/send", {
        "supplier_ids": [alpha["id"], beta["id"]], "item_ids": [row["id"] for row in items], "subject": "Request"})
    with env["sessions"].begin() as db:
        for row in mails["items"]:
            mail = db.get(SupplierMail, row["id"])
            assert mail.item_ids == [item["id"] for item in items]
            mail.status, mail.sent_at = "sent", utcnow() - timedelta(hours=30)
    return req, items, alpha, beta, {row["supplier_id"]: row["id"] for row in mails["items"]}


def quote(env, req, supplier, rows):
    batch = preview(env, req["id"], list(QUOTE_COLUMNS.values()), [
        quote_file(supplier["internal_code"], {"source_row": str(number), "article": f"ART-{number}"})
        for number in rows], "quotes")
    confirm(env, req["id"], batch["id"])


def test_positions_are_priced_declined_or_waiting_per_supplier(crm, monkeypatch):
    req, items, alpha, beta, mails = setup(crm, monkeypatch)
    quote(crm, req, alpha, [1, 3])

    matrix = get(crm, f"/requests/{req['id']}/supplier-responses")
    columns = {row["name"]: row for row in matrix["suppliers"]}
    assert set(columns) == {"Alpha Chem", "Beta Labs"}
    assert columns["Alpha Chem"]["hours"] >= 30 and columns["Beta Labs"]["answered_at"] is None
    assert [(row["number"], row["description"], row["manufacturer_name"]) for row in matrix["items"]] == [
        (1, "Новый товар", "Merck"), (2, "Caffeine", "Merck"), (3, "Новый товар", "Waters")]
    statuses = [{column: row["cells"][column]["status"] for column in row["cells"]} for row in matrix["items"]]
    assert statuses == [
        {alpha["id"]: "priced", beta["id"]: "waiting"},
        {alpha["id"]: "declined", beta["id"]: "waiting"},
        {alpha["id"]: "priced", beta["id"]: "waiting"},
    ]

    report = {row["name"]: row for row in get(crm, "/analytics/suppliers")["items"]}
    first = report["Alpha Chem"]
    assert (first["requests"], first["answered_requests"], first["answer_rate"]) == (1, 1, 100.0)
    assert (first["positions"], first["priced"], first["declined"], first["waiting"]) == (3, 2, 1, 0)
    assert [(row["name"], row["declined"], row["positions"]) for row in first["declined_brands"]] == [("Merck", 1, 2)]
    second = report["Beta Labs"]
    assert (second["answered_requests"], second["answer_rate"], second["average_hours"], second["waiting"]) == (
        0, 0.0, None, 3)

    reply = {"version": 2, "note": "Не работаем с Merck и Waters", "idempotency_key": str(uuid4())}
    with crm["sessions"]() as db:
        reply["version"] = db.get(SupplierMail, mails[beta["id"]]).version
    recorded = cmd(crm, f"/supplier-mails/{mails[beta['id']]}/reply", reply)
    assert recorded["reply_note"] == "Не работаем с Merck и Waters"
    stale = cmd(crm, f"/supplier-mails/{mails[beta['id']]}/reply", {**reply, "idempotency_key": str(uuid4())},
                status=409)
    assert stale["code"] == "VERSION_CONFLICT"
    early = {**reply, "version": recorded["version"], "replied_at": (utcnow() - timedelta(days=5)).isoformat(),
             "idempotency_key": str(uuid4())}
    assert cmd(crm, f"/supplier-mails/{mails[beta['id']]}/reply", early, status=422)["code"] == "REPLY_BEFORE_MAIL"
    detail = get(crm, f"/analytics/suppliers/{beta['id']}")
    assert (detail["supplier"]["declined"], detail["supplier"]["answered_requests"]) == (3, 1)
    assert [(row["name"], row["declined"]) for row in detail["brands"]] == [("Merck", 2), ("Waters", 1)]
    assert {row["status"] for row in detail["positions"]} == {"declined"}
    assert {row["request_number"] for row in detail["positions"]} == {req["number"]}
    assert get(crm, "/analytics/suppliers?date_from=2000-01-01&date_to=2000-01-02")["items"] == []


def test_mails_sent_before_positions_were_recorded_are_matched_by_their_table(crm, monkeypatch):
    req, items, alpha, beta, mails = setup(crm, monkeypatch)
    with crm["sessions"].begin() as db:
        legacy = db.get(SupplierMail, mails[alpha["id"]])
        legacy.item_ids = None
        # The mail asked for the first and the third position only.
        lines = legacy.body.split("\n")
        header = next(index for index, line in enumerate(lines) if "\t" in line)
        legacy.body = "\n".join([*lines[:header + 1], lines[header + 1], lines[header + 3]])
    matrix = get(crm, f"/requests/{req['id']}/supplier-responses")
    asked = [sorted(row["cells"]) for row in matrix["items"]]
    assert asked == [sorted([alpha["id"], beta["id"]]), [beta["id"]], sorted([alpha["id"], beta["id"]])]


def test_supplier_responses_need_the_request_and_the_quote_right(crm, monkeypatch):
    req, *_ = setup(crm, monkeypatch)
    login(crm, "manager@example.com")
    assert get(crm, f"/requests/{req['id']}/supplier-responses", status=404)["code"] == "NOT_FOUND"
    assert get(crm, "/analytics/suppliers", status=403)["code"] == "FORBIDDEN"


def test_priced_cells_show_rubles_and_term_and_suppliers_with_more_prices_come_first(crm, monkeypatch):
    """Business remark of 19 October 2026: compare suppliers in rubles, with the delivery term."""
    req, items, alpha, beta, mails = setup(crm, monkeypatch)
    with crm["sessions"].begin() as db:
        db.add(Currency(name="Доллар США", code="USD"))
        profile = itemized_profile()
        profile["exchange_rates"] = [{"currency": "USD", "management_per_unit": "90", "quoted_units": "1",
                                      "date": "2026-10-01", "source": "ЦБ РФ", "reason": "Тест"}]
        db.add(CalculationProfile(name="Профиль 2026", status="published", effective_from=date(2026, 1, 1),
                                  definition=profile, reason="Тест", author_id=crm["admin"].id))
    quote(crm, req, alpha, [3])
    batch = preview(crm, req["id"], list(QUOTE_COLUMNS.values()), [
        quote_file(beta["internal_code"], {"source_row": str(number), "article": f"B-{number}",
                                          "currency": "usd", "unit_price": "10", "delivery_days": "21"})
        for number in (1, 2)], "quotes")
    confirm(crm, req["id"], batch["id"])

    matrix = get(crm, f"/requests/{req['id']}/supplier-responses")
    assert [(row["name"], row["priced"]) for row in matrix["suppliers"]] == [("Beta Labs", 2), ("Alpha Chem", 1)]
    assert matrix["rates_profile"] == "Профиль 2026"
    cell = matrix["items"][0]["cells"][beta["id"]]
    assert (cell["status"], cell["currency"], cell["unit_price_rub"], cell["delivery_days"]) == (
        "priced", "USD", "900.00", 21)
    rubles = matrix["items"][2]["cells"][alpha["id"]]
    assert (rubles["currency"], rubles["unit_price_rub"], rubles["delivery_days"]) == ("RUB", "100.25", 14)
    assert matrix["items"][2]["cells"][beta["id"]]["status"] == "declined"
    quoted = get(crm, f"/requests/{req['id']}/quote-items?all=true")
    assert {row["currency_code"]: row["unit_price_rub"] for row in quoted["items"]} == {"USD": "900.00",
                                                                                        "RUB": "100.25"}
    assert quoted["rates_profile"] == "Профиль 2026"

