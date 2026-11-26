"""Repeat-order prices and arbitrary calculation volumes, business remarks of 14 November 2026."""

from copy import deepcopy
from decimal import Decimal

import pytest

from app.commerce.financial import calculation_shares, manual_forecasts
from app.commerce.itemized import calculate_itemized, itemized_profile
from app.commerce.models import Calculation, CalculationProfile
from app.core.errors import DomainError
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


def scenario_rows():
    own = quote_selection("own", "reference_standards", "2", "1000", "1")
    peer = quote_selection("peer", "reference_standards", "23", "1000", "1")
    for row in (own, peer):
        row["product_group"]["id"] = "group"
        row["currency_code"] = "RUB"
    profile = itemized_profile()
    profile.update(import_country_id="RU", customs_fee_brackets=[{
        "from_amount": "0", "to_amount": None, "fee": "0"}])
    return profile, own, peer


def test_wave_scenario_changes_airport_pickup_but_keeps_request_expenses_and_manual_parts():
    profile, own, peer = scenario_rows()
    expenses = [
        {"name": "Вывоз из аэропорта", "amount": "1000", "scope": "WAVE", "method": "BY_QUANTITY"},
        {"name": "Доставка клиенту", "amount": "5000", "scope": "REQUEST", "method": "BY_QUANTITY"},
        {"name": "Процент заявки", "amount": "10", "scope": "REQUEST", "method": "BY_QUANTITY",
         "calculation_type": "PERCENTAGE", "percent_base": "PURCHASE"},
        {"name": "Диапазон заявки", "scope": "REQUEST", "method": "BY_QUANTITY",
         "calculation_type": "BRACKET", "percent_base": "PURCHASE", "brackets": [
             {"from_amount": "0", "to_amount": "1999.99", "fee": "100"},
             {"from_amount": "2000", "to_amount": None, "fee": "300"}]},
        {"name": "Ручной расход заявки", "amount": "1000", "scope": "REQUEST", "method": "MANUAL",
         "manual": {"own": "1000"}},
        {"name": "Ручной расход волны", "amount": "250", "scope": "WAVE", "method": "MANUAL",
         "manual": {"own": "50", "peer": "200"}},
    ]
    original = calculate_itemized(profile, [own], expenses, [], wave_existing_selections=[peer])
    calculation_shares([own], [own, peer], {"group": Decimal("10")},
                       {"rows": [{"product_group_id": "group"}]}, wave_rows=[own, peer])
    scenario = calculate_itemized(profile, [own], expenses, [], wave_existing_selections=[peer])
    before = original["lines"][0]["expense_details"]
    after = scenario["lines"][0]["expense_details"]
    assert (Decimal(before["Вывоз из аэропорта"]), Decimal(after["Вывоз из аэропорта"])) == (80, 200)
    for name, amount in (("Доставка клиенту", 5000), ("Процент заявки", 200), ("Диапазон заявки", 300),
                         ("Ручной расход заявки", 1000), ("Ручной расход волны", 50)):
        assert Decimal(before[name]) == Decimal(after[name]) == amount
    for allocation in scenario["expense_allocations"]:
        if allocation["scope"] == "REQUEST":
            assert Decimal(allocation["distribution_quantity"]) == 2
            assert allocation["existing_wave_share"] == "0"


def test_wave_scenario_keeps_fixed_internal_commission_and_manual_sale_price():
    profile, own, peer = scenario_rows()
    own["sale_unit_price"] = "1999.37"
    adjustment = {"enabled": True, "type": "FIXED", "value": "300"}
    original = calculate_itemized(profile, [own], [], [], internal_adjustment=adjustment,
                                  wave_existing_selections=[peer])
    calculation_shares([own], [own, peer], {"group": Decimal("10")},
                       {"rows": [{"product_group_id": "group"}]}, wave_rows=[own, peer])
    scenario = calculate_itemized(profile, [own], [], [], internal_adjustment=adjustment,
                                  wave_existing_selections=[peer])
    for result in (original, scenario):
        detail = result["lines"][0]["detail"]
        assert Decimal(detail["internal_bonus"]) == 300
        assert Decimal(detail["bonus_withdrawal_fee"]) == 48
        assert Decimal(detail["sale_total"]) == Decimal("3998.74")
        assert result["lines"][0]["sale_unit_price"] == "1999.37"


def test_partial_client_purchase_keeps_its_previous_delivery_and_fixed_commission_pricing():
    profile, own, _ = scenario_rows()
    own["quote_item"]["quantity"] = "5"
    own["_share"] = "0.6"  # Earlier business case: five offered, only three taken.
    result = calculate_itemized(profile, [own], [{"name": "Доставка клиенту", "amount": "5000",
        "scope": "REQUEST", "method": "BY_QUANTITY"}], [],
        internal_adjustment={"enabled": True, "type": "FIXED", "value": "300"})
    assert Decimal(result["totals"]["client_delivery"]) == Decimal("8333.33")
    assert Decimal(result["totals"]["internal_bonus"]) == 500
    assert Decimal(result["expense_allocations"][0]["distribution_quantity"]) == 3


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


def test_unrecoverable_existing_wave_rows_join_the_explicit_volume_scenario(crm):
    req, data, seller, supplier, plan, group_id, item = planned(crm)
    data["selections"][0]["quantity"] = "45"
    first = cmd(crm, f"/requests/{req['id']}/calculations", data)
    _, execution = release(crm, req, first, "45")
    approval = cmd(crm, f"/requests/{req['id']}/approvals", {
        "execution_ids": [execution["id"]], "reviewer_id": crm["admin"].id})
    cmd(crm, f"/approvals/{approval['id']}/decision", {
        "version": approval["version"], "decision": "approved", "reason": "В работу", "wave_id": plan["id"]})
    working_id = crm["client"].get(f"/api/v1/requests/{req['id']}").json()["wave_id"]
    allocations_before = wave_row(crm, working_id)["allocations"]

    # Simulate an old accepted snapshot whose product group cannot be restored. The 45 pieces remain
    # a quantity aggregate rather than a detailed peer selection.
    with crm["sessions"].begin() as db:
        old = db.get(Calculation, first["id"])
        snapshot = deepcopy(old.snapshot)
        snapshot["lines"][0]["product_group"] = None
        old.snapshot = snapshot
        profile = db.get(CalculationProfile, data["profile_id"])
        definition = deepcopy(profile.definition)
        definition["customs_fee_brackets"] = [{"from_amount": "0", "to_amount": None, "fee": "0"}]
        definition["customs_fee_overrides"] = []
        profile.definition = definition

    peer, quote = peer_request(crm, seller, supplier, item, working_id)
    payload = {**data, "request_version": peer["version"],
               "selections": [{"quote_item_id": quote["id"], "quantity": "4"}],
               "planned_volume": [{"product_group_id": group_id, "quantity": "9"}],
               "planned_volume_comment": "Сценарий повторного заказа на девять штук"}
    preview = cmd(crm, f"/requests/{peer['id']}/calculations/preview", payload)["snapshot"]
    distribution = preview["wave_distribution"]
    assert Decimal(distribution["total_quantity"]) == 9
    assert Decimal(distribution["offered_quantity"]) == 4
    assert Decimal(distribution["existing_quantity"]) == 45
    assert Decimal(distribution["forecast_quantity"]) == 0
    assert Decimal(preview["lines"][0]["quantity"]) == 4
    assert Decimal(preview["totals"]["international_logistics"]) == Decimal("444.44")
    assert Decimal(preview["wave"]["planned_volume"]["rows"][0]["offered_quantity"]) == 4

    saved = cmd(crm, f"/requests/{peer['id']}/calculations", {
        **payload, "expected_wave_digest": distribution["digest"]})["snapshot"]
    assert Decimal(saved["wave_distribution"]["total_quantity"]) == 9
    assert Decimal(saved["wave_distribution"]["existing_quantity"]) == 45
    assert Decimal(saved["wave_distribution"]["forecast_quantity"]) == 0
    assert Decimal(saved["totals"]["international_logistics"]) == Decimal("444.44")
    assert wave_row(crm, working_id)["allocations"] == allocations_before
    assert Decimal(wave_row(crm, plan["id"])["forecasts"][0]["target_quantity"]) == 10


def test_aggregate_existing_wave_quantity_keeps_zero_and_overfilled_target_semantics():
    own = quote_selection("own", "reference_standards", "4", "1000", "1")
    own["product_group"].update(id="group", name="Стандарты")
    own["currency_code"] = "RUB"
    for target, expected_total, expected_forecast in (("0", 49, 0), ("60", 60, 11)):
        record = {"rows": [{"product_group_id": "group"}]}
        scenario = deepcopy(own)
        existing_share = calculation_shares([scenario], [scenario], {"group": Decimal(target)}, record,
                                            wave_rows=[scenario], existing_quantity=Decimal("45"))
        assert existing_share == 1
        assert "_share" not in scenario
        forecast = manual_forecasts([scenario], [scenario], {"group": Decimal(target)},
                                    existing_quantity=Decimal("45"))
        assert sum((Decimal(row["quote_item"]["quantity"]) for row in forecast), Decimal("0")) == expected_forecast
        profile = itemized_profile()
        profile.update(import_country_id="RU", customs_fee_overrides=[],
                       customs_fee_brackets=[{"from_amount": "0", "to_amount": None, "fee": "0"}])
        result = calculate_itemized(profile, [scenario], [], [], wave_existing_quantity=Decimal("45"),
                                    wave_existing_selections=forecast, wave_existing_share=existing_share)
        assert Decimal(result["wave_distribution"]["total_quantity"]) == expected_total
        assert Decimal(result["wave_distribution"]["existing_quantity"]) == 45


def test_aggregate_existing_wave_quantity_rejects_ambiguous_multiple_groups():
    first = quote_selection("first", "reference_standards", "4", "1000", "1")
    second = quote_selection("second", "other", "1", "1000", "1")
    first["product_group"]["id"] = "first-group"
    second["product_group"]["id"] = "second-group"
    with pytest.raises(DomainError, match="товарные группы") as raised:
        calculation_shares([first, second], [first, second], {"first-group": Decimal("9")},
                           {"rows": [{"product_group_id": "first-group"}]},
                           wave_rows=[first, second], existing_quantity=Decimal("45"))
    assert raised.value.code == "WAVE_BASIS_REQUIRED"
