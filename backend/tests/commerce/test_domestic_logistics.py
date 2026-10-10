"""One airport pickup per wave and independent deliveries to each customer."""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal

import pytest
from test_business_imports import request
from test_business_oct5 import cmd, structured
from test_crm import crm, post  # noqa: F401
from test_itemized_wave_calculation import _selection

from app.commerce.itemized import calculate_itemized, itemized_profile
from app.commerce.models import Calculation
from app.crm.models import QuoteItem


@pytest.mark.parametrize("deduct_vat", [True, False])
def test_pickup_is_shared_but_cdek_and_moscow_are_added_only_to_current_calculation(deduct_vat):
    profile = itemized_profile()
    profile.update(import_country_id="country", vat_deduction_mode=deduct_vat)
    profile["customs_fee_brackets"] = [{"from_amount": "0", "to_amount": None, "fee": "0"}]
    first = _selection("a", "reference_standards", "1", "1000", "1")
    second = _selection("b", "reference_standards", "1", "2000", "1")
    peer = _selection("peer", "reference_standards", "10", "3000", "1")
    for row in (first, second, peer):
        row["currency_code"] = "RUB"
    expenses = [
        {"name": name, "amount": amount, "scope": scope, "stage": "GENERAL", "method": "BY_QUANTITY"}
        for name, amount, scope in (
            ("Логистика РФ", "1200", "REQUEST"),  # A previously misclassified profile.
            ("Доставка СДЭК до 15 кг: Москва", "400", "WAVE"),
            ("Доставка клиенту в Москве", "600", "WAVE"),
        )
    ]
    expenses.insert(0, {"name": "Международная логистика", "amount": "1200",
                        "scope": "WAVE", "stage": "INTERNATIONAL_LOGISTICS", "method": "BY_QUANTITY"})
    original = deepcopy(expenses)
    result = calculate_itemized(profile, [first, second], expenses, [], wave_existing_selections=[peer])
    assert expenses == original
    totals = result["totals"]
    assert Decimal(totals["domestic_logistics"]) == 200  # 1 200 × 2 / 12.
    assert Decimal(totals["client_delivery"]) == 1000  # 400 + 600, only these two units.
    assert Decimal(totals["domestic_logistics_total"]) == 1200
    for line, base in zip(result["lines"], (Decimal("1155"), Decimal("2205"))):
        d = line["detail"]
        assert Decimal(d["general_expenses"]) == 600
        assert Decimal(d["markup_base"]) == base  # Purchase + international freight + duty.
        assert Decimal(d["cost_after_markup"]) == base * Decimal("1.5")
        assert Decimal(d["profit"]) == base * Decimal(".5")
        assert Decimal(d["sale_net"]) == base * Decimal("1.5") + 600 + (
            Decimal("0") if deduct_vat else base * Decimal(".22"))
    allocations = {row["name"]: row for row in result["expense_allocations"]}
    assert allocations["Логистика РФ"]["distribution_quantity"] == "12"
    assert allocations["Доставка клиенту в Москве"]["distribution_quantity"] == "2"
    assert allocations["Доставка СДЭК до 15 кг: Москва"]["existing_wave_share"] == "0"
    assert "peer" not in allocations["Доставка клиенту в Москве"]["wave_parts"]
    for row in result["expense_allocations"]:
        assert sum(Decimal(amount) for amount in row["wave_parts"].values()) == Decimal(row["amount"])


def test_explicit_logistics_stages_work_with_custom_names_and_fractional_penny_allocation():
    profile = itemized_profile()
    profile.update(import_country_id="country")
    rows = [_selection(str(i), "reference_standards", "1", "100", "1") for i in range(3)]
    for row in rows:
        row["currency_code"] = "RUB"
    result = calculate_itemized(profile, rows[:2], [
        {"name": "Машина из терминала", "amount": "10", "scope": "REQUEST",
         "stage": "DOMESTIC_LOGISTICS", "method": "EQUALLY_BY_POSITION"},
        {"name": "Курьер", "amount": "10.01", "scope": "WAVE",
         "stage": "CLIENT_DELIVERY", "method": "BY_PURCHASE_VALUE"},
    ], [], wave_existing_selections=rows[2:])
    pickup, courier = result["expense_allocations"]
    assert pickup["scope"] == "WAVE"
    assert courier["scope"] == "REQUEST"
    assert pickup["method"] == courier["method"] == "BY_QUANTITY"
    assert sum(Decimal(amount) for amount in pickup["wave_parts"].values()) == Decimal("10")
    assert sum(Decimal(amount) for amount in courier["parts"].values()) == Decimal("10.01")
    assert "2" not in courier["wave_parts"]


def test_two_clients_inherit_only_airport_pickup_and_keep_their_own_delivery(crm):  # noqa: F811
    req, data, _, supplier = structured(crm, mode="IMPORT")
    quote_id = data["selections"][0]["quote_item_id"]
    with crm["sessions"].begin() as db:
        quote = db.get(QuoteItem, quote_id)
        quote.quantity = Decimal("2")
        peer_item = {
            "nomenclature_id": quote.nomenclature_id, "packing_id": quote.packing_id,
            "currency_id": quote.currency_id, "quantity": "10", "unit_price": "1000",
            "delivery_days": 7,
        }
    data.update(delivery_required=True, delivery_city="Москва", expenses=[
        {"name": "Логистика РФ", "amount": "1200", "scope": "REQUEST", "method": "BY_QUANTITY"},
        {"name": "Доставка клиенту в Москве", "amount": "500", "scope": "WAVE", "method": "BY_QUANTITY"},
    ])
    first = cmd(crm, f"/requests/{req['id']}/calculations", data)
    with crm["sessions"]() as db:
        original_snapshot = deepcopy(db.get(Calculation, first["id"]).snapshot)

    proposal = cmd(crm, f"/requests/{req['id']}/proposals", {"calculation_id": first["id"],
        "valid_until": (date.today() + timedelta(days=7)).isoformat(), "terms": "Отсрочка"})
    accepted = cmd(crm, f"/proposals/{proposal['id']}/accept", {"version": proposal["version"], "reason": "Клиент подтвердил",
        "lines": [{"line_id": first["snapshot"]["lines"][0]["line_id"], "quantity": "2"}]})
    approval = cmd(crm, f"/requests/{req['id']}/approvals", {"execution_ids": [accepted["executions"][0]["id"]], "reviewer_id": crm["admin"].id})
    cmd(crm, f"/approvals/{approval['id']}/decision", {"version": approval["version"], "decision": "approved"})
    wave = crm["client"].get("/api/v1/waves").json()["items"][0]
    cmd(crm, f"/waves/{wave['id']}/budget", {"version": wave["version"], "profile_id": data["profile_id"],
        "expenses": [row for row in first["snapshot"]["resolved_expenses"] if row["scope"] == "WAVE"], "reason": "Общий бюджет"}, method="put")
    peer = request(crm)
    quote = post(crm, f"/requests/{peer['id']}/quote-sheets", {
        "supplier_id": supplier["id"], "items": [peer_item],
    })
    peer = crm["client"].get(f"/api/v1/requests/{peer['id']}").json()
    assigned = cmd(crm, f"/requests/{peer['id']}/wave", {
        "request_version": peer["version"], "wave_id": req["wave_id"],
    }, method="put")
    second = cmd(crm, f"/requests/{peer['id']}/calculations", {
        "request_version": assigned["version"], "profile_id": data["profile_id"],
        "selections": [{"quote_item_id": quote["items"][0]["id"], "markup_coefficient": "1.5"}],
        "delivery_required": True, "delivery_city": "Новосибирск",
    })["snapshot"]
    assert Decimal(second["wave_distribution"]["total_quantity"]) == 12
    assert Decimal(second["totals"]["domestic_logistics"]) == 1000
    assert Decimal(second["totals"]["client_delivery"]) == 10625
    assert Decimal(second["totals"]["general_expenses"]) == 11625
    assert "Доставка клиенту в Москве" not in {r["name"] for r in second["resolved_expenses"]}

    budget = crm["client"].get(f"/api/v1/requests/{req['id']}/wave-expenses").json()
    assert [(r["name"], r["amount"], r["stage"]) for r in budget["expenses"]] == [
        ("Логистика РФ", "1200", "DOMESTIC_LOGISTICS"),
    ]
    data["request_version"] = crm["client"].get(f"/api/v1/requests/{req['id']}").json()["version"]
    fresh = cmd(crm, f"/requests/{req['id']}/calculations/preview", data)["snapshot"]
    assert Decimal(fresh["wave_distribution"]["total_quantity"]) == 2
    assert Decimal(fresh["totals"]["domestic_logistics"]) == 1200
    assert Decimal(fresh["totals"]["client_delivery"]) == 4500  # CDEK 4 000 + Moscow 500.
    assert Decimal(fresh["totals"]["general_expenses"]) == 5700
    with crm["sessions"]() as db:
        assert db.get(Calculation, first["id"]).snapshot == original_snapshot
