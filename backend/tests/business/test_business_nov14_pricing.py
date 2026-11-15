"""Repeat-order prices and arbitrary calculation volumes, business remarks of 14 November 2026."""

from copy import deepcopy
from decimal import Decimal

import pytest

from app.commerce.financial import calculation_shares
from app.commerce.itemized import calculate_itemized, itemized_profile
from app.commerce.models import CalculationProfile
from tests.business.test_business_oct28 import planned
from tests.support.business import cmd, issue, peer_request, release, structured, wave_row
from tests.support.itemized import quote_selection


@pytest.mark.parametrize("mode", ["IMPORT", "DAP", "RUSSIA"])
@pytest.mark.parametrize("sale_vat", ["22", "0"])
def test_manual_gross_price_recalculates_tax_profit_and_totals_without_changing_costs(mode, sale_vat):
    profile = itemized_profile()
    profile.update(import_country_id="RU", sale_vat_rate=sale_vat, round_sale_up_to_ruble=True,
                   financing_annual_rate="20")
    row = quote_selection("q1", "reference_standards", "3", "1000", "1.1")
    row.update(currency_code="RUB", calculation_type=mode)
    kwargs = {"payment_terms": {"prepayment_percent": "50", "deferred_percent": "50", "deferred_days": 60}}
    before = calculate_itemized(profile, [row], [], [], **kwargs)
    manual = calculate_itemized(profile, [{**row, "sale_unit_price": "500.37"}], [], [], **kwargs)
    [line] = manual["lines"]
    detail = line["detail"]
    assert line["sale_unit_price"] == "500.37"
    assert Decimal(line["unit_price"]) == Decimal("500.37")  # Bypasses the profile's whole-ruble ceiling.
    assert Decimal(line["total"]) == Decimal("1501.11")
    assert Decimal(line["net"]) + Decimal(line["tax"]) == Decimal(line["total"])
    expected_tax = (Decimal(line["total"]) * Decimal(sale_vat) / (100 + Decimal(sale_vat))).quantize(
        Decimal(".01"))
    assert Decimal(line["tax"]) == expected_tax
    for key in ("cost", "purchase_rub", "customs_fee", "internal_bonus", "bonus_withdrawal_fee",
                "service_fee", "financing_cost", "cash_need"):
        assert detail[key] == before["lines"][0]["detail"][key]
    assert Decimal(detail["profit"]) == Decimal(line["net"]) - Decimal(detail["cost"]) < 0
    assert Decimal(detail["profitability_percent"]) < 0
    assert Decimal(detail["vat_payable"]) >= 0
    for key in ("net", "tax", "total"):
        assert manual["totals"][key] == line[key]
    assert manual["totals"]["profit"] == detail["profit"]


def test_manual_price_survives_save_copy_proposal_and_reset(crm):
    req, data, _, _ = structured(crm)
    url = f"/requests/{req['id']}/calculations"
    data["selections"][0].update(quantity="3", sale_unit_price="1999.37")
    preview = cmd(crm, f"{url}/preview", data)["snapshot"]
    saved = cmd(crm, url, {**data, "expected_wave_digest": preview["wave_distribution"]["digest"]})
    assert saved["snapshot"]["lines"][0]["total"] == "5998.11"
    assert saved["snapshot"]["input"]["selections"][0]["sale_unit_price"] == "1999.37"
    proposal = issue(crm, req, saved)
    assert proposal["snapshot"]["lines"][0]["total"] == "5998.11"

    current = crm["client"].get(f"/api/v1/requests/{req['id']}").json()
    copied_data = deepcopy(saved["snapshot"]["input"])
    copied_data.update(request_version=current["version"], copy_of_id=saved["id"], previous_id=saved["id"])
    copied = cmd(crm, url, copied_data)
    assert copied["snapshot"]["lines"][0]["total"] == "5998.11"
    assert copied["snapshot"]["lines"][0]["sale_unit_price"] == "1999.37"
    copied_data["selections"][0]["sale_unit_price"] = None
    copied_data["previous_id"] = copied["id"]
    copied_data["request_version"] = crm["client"].get(f"/api/v1/requests/{req['id']}").json()["version"]
    reset = cmd(crm, f"{url}/preview", copied_data)["snapshot"]
    assert reset["lines"][0]["sale_unit_price"] is None
    assert reset["lines"][0]["total"] != "5998.11"
    # Resetting a new scenario does not rewrite the saved price or the issued proposal.
    previous = crm["client"].get(f"/api/v1/requests/{req['id']}/calculations").json()["items"]
    assert next(row for row in previous if row["id"] == saved["id"])["snapshot"]["lines"][0]["total"] == "5998.11"


@pytest.mark.parametrize("value", ["0", "-1", "1.001", "NaN", "Infinity", "1000000000000000000000000"])
def test_invalid_manual_sale_price_is_rejected(crm, value):
    req, data, _, _ = structured(crm)
    data["selections"][0]["sale_unit_price"] = value
    result = cmd(crm, f"/requests/{req['id']}/calculations/preview", data, status=422)
    assert result["code"] == "VALIDATION_ERROR"


def test_scenario_below_real_volume_uses_full_precision_and_only_its_own_working_wave():
    own = quote_selection("own", "reference_standards", "3", "1000", "1")
    peer = quote_selection("peer", "reference_standards", "23", "1000", "1")
    sibling = quote_selection("other-working-wave", "reference_standards", "10", "1000", "1")
    for row in (own, peer, sibling):
        row["product_group"]["id"] = "group"
        row["currency_code"] = "RUB"
    record = {"rows": [{"product_group_id": "group"}]}
    calculation_shares([own], [own, peer, sibling], {"group": Decimal("1")}, record, wave_rows=[own, peer])
    assert "_share" not in sibling
    profile = itemized_profile()
    profile["import_country_id"] = "RU"
    calculated = calculate_itemized(profile, [own], [{"name": "Общая логистика", "amount": "1000",
        "currency": "RUB", "method": "BY_QUANTITY"}], [], wave_existing_selections=[peer])
    assert Decimal(calculated["wave_distribution"]["total_quantity"]) == 1
    assert Decimal(calculated["totals"]["general_expenses"]) == 3000


@pytest.mark.parametrize("target", ["1", "10", "23"])
def test_working_wave_volume_can_be_smaller_than_its_23_existing_pieces(crm, target):
    req, data, seller, supplier, plan, group_id, item = planned(crm)
    data["selections"][0]["quantity"] = "23"
    first = cmd(crm, f"/requests/{req['id']}/calculations", data)
    _, execution = release(crm, req, first, "23")
    approval = cmd(crm, f"/requests/{req['id']}/approvals", {
        "execution_ids": [execution["id"]], "reviewer_id": crm["admin"].id})
    cmd(crm, f"/approvals/{approval['id']}/decision", {
        "version": approval["version"], "decision": "approved", "reason": "В работу", "wave_id": plan["id"]})
    working_id = crm["client"].get(f"/api/v1/requests/{req['id']}").json()["wave_id"]
    working = wave_row(crm, working_id)
    assert Decimal(working["allocations"][0]["quantity"]) == 23
    peer, quote = peer_request(crm, seller, supplier, item, working_id)
    payload = {**data, "request_version": peer["version"],
               "selections": [{"quote_item_id": quote["id"], "quantity": "2"}],
               "planned_volume": [{"product_group_id": group_id, "quantity": target}],
               "planned_volume_comment": "Расчёт повторного заказа на другой объём"}
    # The existing plan has ten pieces: in that case this is still an explicit scenario override.
    with crm["sessions"].begin() as db:
        profile = db.get(CalculationProfile, data["profile_id"])
        definition = deepcopy(profile.definition)
        definition["customs_fee_brackets"] = [{"from_amount": "0", "to_amount": None, "fee": "0"}]
        profile.definition = definition
    preview = cmd(crm, f"/requests/{peer['id']}/calculations/preview", payload)["snapshot"]
    assert Decimal(preview["wave_distribution"]["total_quantity"]) == Decimal(target)
    expected = (Decimal("1000") * 2 / Decimal(target)).quantize(Decimal(".01"))
    assert abs(Decimal(preview["totals"]["international_logistics"]) - expected) <= Decimal(".02")
    saved = cmd(crm, f"/requests/{peer['id']}/calculations", {
        **payload, "expected_wave_digest": preview["wave_distribution"]["digest"]})
    assert Decimal(saved["snapshot"]["lines"][0]["quantity"]) == 2
    assert Decimal(saved["snapshot"]["wave_distribution"]["total_quantity"]) == Decimal(target)
    assert Decimal(wave_row(crm, working_id)["allocations"][0]["quantity"]) == 23
    assert Decimal(wave_row(crm, plan["id"])["forecasts"][0]["target_quantity"]) == 10
