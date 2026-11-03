"""Business remarks of 3 November 2026: a position moved to a new working wave of its supplier, the columns of the
requests list kept per employee, the extra freight of a supplier's quote, the client taking fewer pieces than the
proposal holds, and the fixed customs fee of columns changed inside one calculation."""

from decimal import Decimal

from sqlalchemy import select

from app.commerce.itemized import calculate_itemized
from app.commerce.itemized import itemized_profile as current_profile
from app.core.models import AuditEvent
from app.crm.models import Currency, ProductGroup, QuoteItem, RequestItem
from tests.business.test_business_oct28 import planned
from tests.support.business import cmd, post, release, structured, wave_row
from tests.support.itemized import quote_selection


def test_a_position_moves_to_a_new_working_wave_of_its_plan(crm):  # noqa: F811
    req, data, _, _, plan, _, _ = planned(crm)
    first = cmd(crm, f"/requests/{req['id']}/calculations", data)
    _, execution = release(crm, req, first, "2")
    approval = cmd(crm, f"/requests/{req['id']}/approvals", {"execution_ids": [execution["id"]],
                   "reviewer_id": crm["admin"].id})
    cmd(crm, f"/approvals/{approval['id']}/decision", {
        "version": approval["version"], "decision": "approved", "reason": "В работу", "wave_id": plan["id"]})
    [working] = wave_row(crm, plan["id"])["working_waves"]
    [allocation] = wave_row(crm, working["id"])["allocations"]

    refused = cmd(crm, f"/allocations/{allocation['id']}/transfer", {
        "version": allocation["version"], "quantity": "2", "reason": "Без цели"}, status=422)
    assert refused["code"] == "VALIDATION_ERROR"

    moved = cmd(crm, f"/allocations/{allocation['id']}/transfer", {
        "version": allocation["version"], "new_wave": True, "quantity": "2",
        "reason": "Поедет отдельной поставкой"})
    numbers = [row["number"] for row in wave_row(crm, plan["id"])["working_waves"]]
    assert numbers == [f"{plan['number']} — рабочая 1", f"{plan['number']} — рабочая 2"]
    assert moved["wave"]["number"] == f"{plan['number']} — рабочая 2"
    assert wave_row(crm, working["id"])["allocations"] == []
    [new_allocation] = wave_row(crm, moved["wave"]["id"])["allocations"]
    assert Decimal(new_allocation["quantity"]) == 2
    # Nothing of the request is left in the old wave: the request and its next calculations follow the orders.
    assert crm["client"].get(f"/api/v1/requests/{req['id']}").json()["wave_id"] == moved["wave"]["id"]
    with crm["sessions"]() as db:
        event = db.query(AuditEvent).filter_by(entity_id=req["id"], action="assign_wave").all()[-1]
        assert event.reason == f"Позиции перенесены в волну {moved['wave']['number']}"

    # A new working wave is copied only from a plan.
    not_plan = cmd(crm, f"/allocations/{new_allocation['id']}/transfer", {
        "version": new_allocation["version"], "new_wave": True, "plan_id": working["id"], "quantity": "1",
        "reason": "Не из плана"}, status=422)
    assert (not_plan["code"], not_plan["field"]) == ("TARGET_WAVE", "plan_id")

    # A working wave of another supplier is refused.
    other = post(crm, "/counterparties", {"name": "Другой поставщик", "kind": "supplier",
                                          "email": "sales@other.example", "details": {"calculation_type": "IMPORT"}})
    foreign = cmd(crm, "/waves", {
        "kind": "work", "supplier_id": other["id"], "route": "Москва", "origin_country": "IN",
        "owner_id": crm["admin"].id, "close_date": plan["close_date"], "departure_date": plan["departure_date"],
        "arrival_date": plan["arrival_date"]})
    wrong = cmd(crm, f"/allocations/{new_allocation['id']}/transfer", {
        "version": new_allocation["version"], "target_wave_id": foreign["id"], "quantity": "1",
        "reason": "Не тот поставщик"}, status=422)
    assert wrong["code"] == "WAVE_SUPPLIER"


def test_the_requests_list_keeps_the_columns_of_each_employee(crm):  # noqa: F811
    req, _, _, _ = structured(crm)
    client = crm["client"]
    assert client.get("/api/v1/me/preferences/requests.columns").json()["value"] is None
    saved = client.put("/api/v1/me/preferences/requests.columns",
                       json={"value": {"columns": "number,client_name,wave_number,items_count,last_proposal_total"}})
    assert saved.status_code == 200, saved.text
    assert client.get("/api/v1/me/preferences/requests.columns").json()["value"] == {
        "columns": "number,client_name,wave_number,items_count,last_proposal_total"}
    [row] = [item for item in client.get("/api/v1/requests").json()["items"] if item["id"] == req["id"]]
    assert row["items_count"] == 1 and row["wave_number"] is None and row["last_proposal_total"] is None
    assert {"contact_name", "client_tax_id", "last_proposal_currency"} <= row.keys()
    by_client = client.get("/api/v1/requests", params={"sort": "client_name", "direction": "asc"})
    assert by_client.status_code == 200, by_client.text


def test_extra_freight_of_a_quote_is_set_shown_and_taken_off(crm):  # noqa: F811
    req, data, _, supplier = structured(crm, mode="IMPORT")
    with crm["sessions"].begin() as db:
        quote = db.get(QuoteItem, data["selections"][0]["quote_item_id"])
        usd = db.scalar(select(Currency).where(Currency.code == "USD"))
        if not usd:
            usd = Currency(code="USD", name="Доллар США")
            db.add(usd)
            db.flush()
        usd_id, sheet_id = usd.id, quote.quote_id
        item = {"nomenclature_id": quote.nomenclature_id, "packing_id": quote.packing_id, "quantity": "1",
                "unit_price": "10", "currency_id": quote.currency_id, "delivery_days": 7}
    created = post(crm, f"/requests/{req['id']}/quote-sheets", {
        "supplier_id": supplier["id"], "items": [item],
        "extra_freight": {"amount": "1500", "currency_id": usd_id, "comment": "Большой объём"}})
    assert created["extra_freight"]["amount"] == "1500" and created["extra_freight"]["currency"] == "USD"
    assert created["items"][0]["extra_freight"]["comment"] == "Большой объём"

    sheet = crm["client"].get(f"/api/v1/quote-sheets/{sheet_id}").json()
    assert sheet["extra_freight"] is None
    marked = cmd(crm, f"/quote-sheets/{sheet_id}/extra-freight", {
        "version": sheet["version"], "extra_freight": {"amount": "250.50", "currency_id": usd_id}}, method="put")
    assert (marked["extra_freight"]["amount"], marked["extra_freight"]["comment"]) == ("250.50", "")
    rows = crm["client"].get(f"/api/v1/requests/{req['id']}/quote-items?all=true").json()["items"]
    assert sorted(bool(row["extra_freight"]) for row in rows) == [True, True]
    cleared = cmd(crm, f"/quote-sheets/{sheet_id}/extra-freight", {
        "version": marked["version"], "extra_freight": None}, method="put")
    assert cleared["extra_freight"] is None
    stale = cmd(crm, f"/quote-sheets/{sheet_id}/extra-freight", {
        "version": marked["version"], "extra_freight": None}, method="put", status=409)
    assert stale["code"] == "VERSION_CONFLICT"


def test_the_client_takes_fewer_pieces_than_the_proposal(crm):  # noqa: F811
    """«Клиент просит КП на 5 шт., но возьмёт только 3»: the 1 000 ₽ of the wave are divided by 3 pieces, and each
    of the 5 pieces of the proposal is priced as a piece of a supply of 3."""
    req, data, _, _, _, group_id, _ = planned(crm)
    data["selections"][0]["quantity"] = "5"
    url = f"/requests/{req['id']}/calculations"
    plan = cmd(crm, f"{url}/preview", data)["snapshot"]
    assert Decimal(plan["wave_distribution"]["total_quantity"]) == 10
    assert Decimal(plan["totals"]["international_logistics"]) == 500

    fewer = {**data, "planned_volume": [{"product_group_id": group_id, "quantity": "3"}],
             "planned_volume_comment": "Клиент возьмёт 3 из 5"}
    saved = cmd(crm, url, fewer)["snapshot"]
    distribution = saved["wave_distribution"]
    assert (Decimal(distribution["total_quantity"]), Decimal(distribution["selected_quantity"]),
            Decimal(distribution["offered_quantity"]), Decimal(distribution["forecast_quantity"])) == (3, 3, 5, 0)
    [line] = saved["lines"]
    assert Decimal(line["quantity"]) == 5
    assert Decimal(saved["totals"]["international_logistics"]) == Decimal("1666.67")
    assert saved["wave"]["planned_volume"]["rows"][0] == {
        "product_group_id": group_id, "product_group_name": "Стандартные образцы", "wave_quantity": "10",
        "quantity": "3", "offered_quantity": "5", "counted_quantity": "3"}
    assert Decimal(line["unit_price"]) > Decimal(plan["lines"][0]["unit_price"])

    # 0 keeps its meaning of 28 October: no more plan, the five pieces carry the costs.
    alone = cmd(crm, f"{url}/preview", {**fewer, "request_version": crm["client"].get(
        f"/api/v1/requests/{req['id']}").json()["version"],
        "planned_volume": [{"product_group_id": group_id, "quantity": "0"}]})["snapshot"]
    assert Decimal(alone["wave_distribution"]["total_quantity"]) == 5
    assert Decimal(alone["totals"]["international_logistics"]) == 1000


def test_the_share_of_pieces_that_ship_prices_fixed_costs_and_the_fee():
    profile = current_profile()
    profile["import_country_id"] = "country-1"
    selection = quote_selection("columns", "columns", "5", "100", "1")
    selection["currency_code"] = "RUB"
    expenses = [{"name": "Декларант", "amount": "3000", "currency": "RUB", "method": "BY_QUANTITY",
                 "stage": "GENERAL", "calculation_type": "FIXED"}]
    full = calculate_itemized(profile, [selection], expenses, [])
    partial = calculate_itemized(profile, [{**selection, "_share": "0.6"}], expenses, [])
    assert full["totals"]["customs_fee_2"] == "73800.00" and full["lines"][0]["expense_details"]["Декларант"] == "3000.00"
    # Three of the five ship: a piece carries a third of the fee and of the declarant, the proposal keeps 5 pieces.
    assert partial["totals"]["customs_fee_2"] == "123000.00"
    assert partial["lines"][0]["expense_details"]["Декларант"] == "5000.00"
    assert partial["wave_distribution"]["selected_quantity"] == "3.0"
    assert partial["wave_distribution"]["offered_quantity"] == "5"


def test_the_columns_fee_is_changed_for_one_calculation_only_with_a_comment(crm):  # noqa: F811
    req, data, _, _ = structured(crm, mode="IMPORT")
    with crm["sessions"].begin() as db:
        quote = db.get(QuoteItem, data["selections"][0]["quote_item_id"])
        group = db.get(ProductGroup, db.get(RequestItem, quote.source_request_item_id).product_group_id)
        group.slug, group_id = "columns", group.id
    url = f"/requests/{req['id']}/calculations"
    usual = cmd(crm, f"{url}/preview", data)["snapshot"]
    assert usual["totals"]["customs_fee_2"] == "73800.00"

    fee = {"customs_fees": [{"product_group_id": group_id, "amount": "0"}]}
    refused = cmd(crm, f"{url}/preview", {**data, **fee}, status=422)
    assert (refused["code"], refused["field"]) == ("CUSTOMS_FEE_COMMENT", "customs_fee_comment")
    comment = "Сбор оплатил клиент ноябрьского заказа, колонки едут той же волной"
    saved = cmd(crm, url, {**data, **fee, "customs_fee_comment": comment})["snapshot"]
    assert Decimal(saved["totals"]["customs_fee"]) == 0 and Decimal(saved["totals"]["customs_fee_1"]) == 0
    assert saved["wave_distribution"]["customs_fee_scale_waived"] is False
    assert saved["customs_fee_adjustment"] == {"comment": comment, "rows": [{
        "product_group_id": group_id, "product_group_name": "Стандартные образцы", "profile_amount": "73800",
        "amount": "0"}]}
    assert Decimal(saved["totals"]["sale_net"]) < Decimal(usual["totals"]["sale_net"])

    # The profile's own amount needs no comment and leaves no trace; a group without a fixed fee is refused.
    current = crm["client"].get(f"/api/v1/requests/{req['id']}").json()["version"]
    same = cmd(crm, f"{url}/preview", {**data, "request_version": current,
               "customs_fees": [{"product_group_id": group_id, "amount": "73800"}]})["snapshot"]
    assert same["totals"]["customs_fee_2"] == "73800.00" and "customs_fee_adjustment" not in same
    unknown = cmd(crm, f"{url}/preview", {**data, "request_version": current, "customs_fee_comment": comment,
                  "customs_fees": [{"product_group_id": "missing", "amount": "0"}]}, status=422)
    assert (unknown["code"], unknown["field"]) == ("CUSTOMS_FEE_GROUP", "customs_fees.0.product_group_id")


def test_the_columns_fee_keeps_the_scale_waived_with_other_goods():
    """With the fee of the supply paid by another order, the scale is not charged on the other goods either."""
    profile = current_profile()
    profile["import_country_id"] = "country-1"
    profile["customs_fee_charged"] = {"columns": "0"}
    result = calculate_itemized(profile, [
        quote_selection("columns", "columns", "1", "1000", "1"),
        quote_selection("sample", "reference_standards", "2", "100", "1"),
    ], [], [{"currency": "USD", "management_per_unit": "100", "quoted_units": "1", "date": "2026-09-28",
             "source": "Профиль"}])
    assert Decimal(result["totals"]["customs_fee"]) == 0
    assert result["wave_distribution"]["customs_fee_scale_waived"] is True
