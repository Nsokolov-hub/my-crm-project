"""Business regressions: VAT extraction, proposal stages, numbers and wave remainder."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from test_business_oct5 import cmd, configured_profile, structured
from test_business_oct6 import handoff, release, wave
from test_business_oct7 import release as issue
from test_crm import crm  # noqa: F401
from test_itemized_wave_calculation import _selection

from app.commerce.itemized import calculate_itemized
from app.commerce.models import Calculation
from app.core.models import AuditEvent, PermissionGrant
from app.crm.models import Nomenclature, QuoteItem, Request


def current(env, request_id):
    return env["client"].get(f"/api/v1/requests/{request_id}").json()


@pytest.mark.parametrize("rate", ["0", "10", "22"])
def test_rounded_sale_extracts_vat_from_gross_and_recalculates_profit(rate):
    profile = configured_profile()
    profile.update(vat_rate=rate, round_sale_up_to_ruble=True)
    row = _selection("line", "reference_standards", "4", "100.17", "1")
    row.update(currency_code="RUB", calculation_type="RUSSIA")
    line = calculate_itemized(profile, [row], [], [])["lines"][0]
    total, tax, net = (Decimal(line[key]) for key in ("total", "tax", "net"))
    expected_tax = (total * Decimal(rate) / (100 + Decimal(rate))).quantize(Decimal(".01"))
    assert tax == expected_tax
    assert net + tax == total
    assert Decimal(line["unit_price"]) * 4 == total
    detail = line["detail"]
    assert Decimal(detail["profit"]) == net - Decimal(detail["cost"])
    assert abs(Decimal(detail["profitability_percent"]) - Decimal(detail["profit"]) / net * 100) < Decimal("1e-25")


def test_vat_matches_business_screenshot_851998():
    profile = configured_profile()
    profile["round_sale_up_to_ruble"] = True
    rows = []
    for index, (quantity, gross_price) in enumerate(((4, 79189), (2, 187971), (5, 31860))):
        purchase = Decimal(gross_price) / Decimal("1.22") - Decimal(".01")
        row = _selection(str(index), "reference_standards", str(quantity), str(purchase), "1")
        row.update(currency_code="RUB", calculation_type="RUSSIA", markup_coefficient="1")
        rows.append(row)
    result = calculate_itemized(profile, rows, [], [])
    assert Decimal(result["totals"]["total"]) == Decimal("851998.00")
    assert Decimal(result["totals"]["tax"]) == Decimal("153638.98")
    assert Decimal(result["totals"]["net"]) == Decimal("698359.02")


def test_proposal_advances_request_once_and_uses_request_and_calculation_number(crm):  # noqa: F811
    req, data, _, _ = structured(crm)
    first = cmd(crm, f"/requests/{req['id']}/calculations", data)
    second = cmd(crm, f"/requests/{req['id']}/calculations", {**data, "previous_id": first["id"]})
    with crm["sessions"].begin() as db:
        db.get(Calculation, second["id"]).created_at = datetime(2026, 10, 9, 11, 54, tzinfo=timezone.utc)
    row = next(row for row in crm["client"].get(f"/api/v1/requests/{req['id']}/calculations").json()["items"]
               if row["id"] == second["id"])
    assert row["selection_label"] == "№2 · 09.10.2026 14:54"
    key = str(uuid4())
    payload = {"calculation_id": second["id"], "valid_until": datetime.now(timezone.utc).date().isoformat(),
               "terms": "По договору"}
    proposal = cmd(crm, f"/requests/{req['id']}/proposals", payload, key=key)
    state = current(crm, req["id"])
    assert state["commercial_stage"] == "proposal_sent"
    assert state["version"] == req["version"] + 1
    assert proposal["display_number"] == f"{req['number']}-2"
    assert cmd(crm, f"/requests/{req['id']}/proposals", payload, key=key) == proposal
    assert current(crm, req["id"])["version"] == state["version"]
    with crm["sessions"]() as db:
        assert db.query(AuditEvent).filter_by(entity_id=req["id"], action="proposal_sent").count() == 1


@pytest.mark.parametrize("stage", ["proposal_sent", "composition_agreed", "awaiting_payment"])
def test_manual_commercial_stage_is_saved_with_version_protection(crm, stage):  # noqa: F811
    req, _, _, _ = structured(crm)
    payload = {"version": req["version"], "commercial_stage": stage, "reason": "Подтверждено менеджером"}
    response = crm["client"].patch(f"/api/v1/requests/{req['id']}", json=payload)
    assert response.status_code == 200, response.text
    assert current(crm, req["id"])["commercial_stage"] == stage
    assert crm["client"].patch(f"/api/v1/requests/{req['id']}", json=payload).status_code == 409


def test_legacy_calculation_chooser_has_number_and_company_local_time(crm):  # noqa: F811
    req, data, _, _ = structured(crm)
    calculation = cmd(crm, f"/requests/{req['id']}/calculations", data)
    with crm["sessions"].begin() as db:
        row = db.get(Calculation, calculation["id"])
        legacy = {key: value for key, value in row.snapshot.items() if key != "version_number"}
        row.snapshot = legacy
        row.created_at = datetime(2026, 10, 9, 11, 54, tzinfo=timezone.utc)
    row = crm["client"].get(f"/api/v1/requests/{req['id']}/calculations").json()["items"][0]
    assert row["selection_label"] == "№1 · 09.10.2026 14:54"
    assert row["version_number"] == 1
    assert "version_number" not in row["snapshot"]


@pytest.mark.parametrize("stage", ["composition_agreed", "awaiting_payment", "sale_confirmed", "closed_lost"])
def test_another_proposal_does_not_regress_or_reopen_request(crm, stage):  # noqa: F811
    req, data, _, _ = structured(crm)
    calculation = cmd(crm, f"/requests/{req['id']}/calculations", data)
    with crm["sessions"].begin() as db:
        db.get(Request, req["id"]).commercial_stage = stage
    issue(crm, req, calculation)
    assert current(crm, req["id"])["commercial_stage"] == stage


def test_wave_plan_ten_minus_three_and_quote_manufacturer(crm):  # noqa: F811
    req, data, _, _ = structured(crm, mode="IMPORT")
    with crm["sessions"].begin() as db:
        quote = db.get(QuoteItem, data["selections"][0]["quote_item_id"])
        product = db.get(Nomenclature, quote.nomenclature_id)
        product.manufacturer = "Производитель образца"
        group_id = product.product_group_id
    quote_view = crm["client"].get(f"/api/v1/requests/{req['id']}/quote-items").json()["items"][0]
    assert quote_view["article"] == "A123"
    assert quote_view["manufacturer"] == "Производитель образца"
    initial = wave(crm, req["wave_id"])
    cmd(crm, f"/waves/{initial['id']}/forecasts", {
        "version": initial["version"], "product_group_id": group_id, "target_quantity": "10",
        "unit_price_rub": "1000", "reason": "План запуска",
    }, method="put")
    data["selections"][0]["quantity"] = "3"
    calculation = cmd(crm, f"/requests/{req['id']}/calculations", data)
    _, execution = release(crm, req, calculation, "3")
    assert current(crm, req["id"])["commercial_stage"] == "composition_agreed"
    assert Decimal(wave(crm, initial["id"])["forecasts"][0]["remaining_quantity"]) == 10
    handoff(crm, req, execution)
    forecast = wave(crm, initial["id"])["forecasts"][0]
    assert Decimal(forecast["target_quantity"]) == 10
    assert Decimal(forecast["filled_quantity"]) == 3
    assert Decimal(forecast["remaining_quantity"]) == 7
    assert current(crm, req["id"])["commercial_stage"] == "sale_confirmed"


def test_wave_remainder_does_not_disclose_inaccessible_request_quantities(crm):  # noqa: F811
    req, data, _, _ = structured(crm, mode="IMPORT")
    with crm["sessions"]() as db:
        quote = db.get(QuoteItem, data["selections"][0]["quote_item_id"])
        group_id = db.get(Nomenclature, quote.nomenclature_id).product_group_id
    initial = wave(crm, req["wave_id"])
    cmd(crm, f"/waves/{initial['id']}/forecasts", {
        "version": initial["version"], "product_group_id": group_id, "target_quantity": "10",
        "unit_price_rub": "1000", "reason": "План запуска",
    }, method="put")
    calculation = cmd(crm, f"/requests/{req['id']}/calculations", data)
    _, execution = release(crm, req, calculation, "1")
    handoff(crm, req, execution)
    with crm["sessions"].begin() as db:
        db.get(Request, req["id"]).owner_id = crm["manager"].id
        db.query(PermissionGrant).filter_by(user_id=crm["admin"].id, code="requests.read").update({"scope": "own"})
    visible = wave(crm, initial["id"])
    assert visible["allocations"] == []
    assert visible["forecasts"][0]["filled_quantity"] is None
    assert visible["forecasts"][0]["remaining_quantity"] is None
