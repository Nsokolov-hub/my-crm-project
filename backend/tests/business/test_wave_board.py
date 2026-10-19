"""The board of waves: real sales, profitability, pieces and logistics per unit of every wave in use,
and the proposals not accepted yet apart."""

from decimal import Decimal

from app.core.models import PermissionGrant
from app.crm.models import QuoteItem, Request, RequestItem
from tests.support.business import cmd, handoff, issue, release, structured, wave_row
from tests.support.crm import login

LOGISTICS = {"name": "Международная логистика", "amount": "500", "currency": "RUB", "method": "BY_QUANTITY",
             "scope": "WAVE", "stage": "INTERNATIONAL_LOGISTICS", "basis": "Тариф поставщика"}


def board(env):
    response = env["client"].get("/api/v1/waves/board")
    assert response.status_code == 200, response.text
    return response.json()


def card(env, wave_id):
    return next(row for row in board(env)["items"] if row["id"] == wave_id)


def calculate(env, req, data):
    current = env["client"].get(f"/api/v1/requests/{req['id']}").json()
    return cmd(env, f"/requests/{req['id']}/calculations",
               {**data, "request_version": current["version"], "expenses": [LOGISTICS]})


def test_a_plan_shows_the_latest_proposal_of_each_request(crm):  # noqa: F811
    req, data, _, _ = structured(crm, mode="IMPORT", wave_kind="plan")
    empty = card(crm, req["wave_id"])
    assert (empty["kind"], empty["requests"], empty["requests_without_proposal"], empty["proposals"]) == (
        "plan", 1, 1, 0)
    assert (empty["quantity"], empty["summary"]["sales"], empty["summary"]["logistics_per_unit"]) == ("0", "0.00", None)

    issue(crm, req, calculate(crm, req, data))
    calculation = calculate(crm, req, {**data, "selections": [{**data["selections"][0], "markup_coefficient": "2"}]})
    issue(crm, req, calculation)
    line = calculation["snapshot"]["lines"][0]
    shown = card(crm, req["wave_id"])
    summary = shown["summary"]
    assert (shown["requests"], shown["requests_without_proposal"], shown["proposals"]) == (1, 0, 1)
    # Business remark of 19 October 2026: a proposal not accepted yet is not a sale; it is shown apart.
    assert (shown["quantity"], shown["offered_quantity"]) == ("0", "1")
    assert (summary["sales"], summary["accepted_sales"], summary["profitability_percent"]) == ("0.00", "0.00", None)
    assert summary["logistics_per_unit"] is None
    assert Decimal(summary["offered_sales"]) == Decimal(line["total"]).quantize(Decimal("0.01"))
    assert summary["potential_sales"] == summary["offered_sales"]
    assert Decimal(summary["offered_profitability_percent"]) == (
        Decimal(line["detail"]["profit"]) / Decimal(line["net"]) * 100).quantize(Decimal("0.01"))

    with crm["sessions"].begin() as db:
        db.get(Request, req["id"]).commercial_stage = "closed_lost"
    lost = card(crm, req["wave_id"])
    assert (lost["requests"], lost["quantity"], lost["summary"]["sales"]) == (0, "0", "0.00")


def test_accepted_positions_move_from_the_plan_to_the_working_wave(crm):  # noqa: F811
    req, data, _, _ = structured(crm, mode="IMPORT", wave_kind="plan")
    plan_id = req["wave_id"]
    calculation = calculate(crm, req, data)
    line = calculation["snapshot"]["lines"][0]
    _, execution = release(crm, req, calculation, "1")
    waiting = card(crm, plan_id)
    assert (waiting["requests"], waiting["proposals"], waiting["quantity"]) == (1, 0, "1")
    assert Decimal(waiting["summary"]["accepted_sales"]) == Decimal(line["total"]).quantize(Decimal("0.01"))

    handoff(crm, req, execution)
    [work] = wave_row(crm, plan_id)["working_waves"]
    plan, working = card(crm, plan_id), card(crm, work["id"])
    assert (plan["requests"], plan["quantity"], plan["summary"]["sales"]) == (0, "0", "0.00")
    assert (working["kind"], working["plan_number"], working["requests"], working["quantity"]) == (
        "work", plan["number"], 1, "1")
    assert working["summary"]["accepted_sales"] == waiting["summary"]["accepted_sales"]
    summary = working["summary"]
    assert Decimal(summary["sales"]) == Decimal(line["total"]).quantize(Decimal("0.01"))
    assert Decimal(summary["sale_net"]) == Decimal(line["net"]).quantize(Decimal("0.01"))
    # The card shows the same result as the wave itself: document prices and the current shared costs.
    actual = wave_row(crm, work["id"])["financial_summary"]["actual"]
    assert actual["status"] == "current" and summary["profit_basis"] == "current"
    assert (summary["sales"], summary["sale_net"], summary["profit"], summary["profitability_percent"]) == (
        actual["sales"], actual["sale_net"], actual["profit"], actual["profitability_percent"])
    assert Decimal(summary["logistics_per_unit"]) == Decimal("500.00")
    assert (summary["offered_sales"], summary["offered_quantity"]) == ("0.00", "0")
    # A dearer delivery in the wave budget lowers the profit on the card, as inside the wave.
    cmd(crm, f"/waves/{work['id']}/budget", {"version": wave_row(crm, work["id"])["version"],
        "profile_id": data["profile_id"], "expenses": [{**LOGISTICS, "amount": "1500"}],
        "reason": "Дороже доставка"}, method="put")
    dearer = card(crm, work["id"])["summary"]
    assert Decimal(dearer["profit"]) < Decimal(summary["profit"])
    assert dearer["profit"] == wave_row(crm, work["id"])["financial_summary"]["actual"]["profit"]
    assert [row["id"] for row in board(crm)["items"]].count(work["id"]) == 1


def test_an_assembling_wave_shares_its_costs_with_the_planned_rest_as_its_proposal_did(crm):  # noqa: F811
    """Business remark of 19 October 2026: «Аозеал 4 — рабочая 1» showed −4,46 % against 20 % of its proposal.
    A wave without a budget keeps the shared costs of its proposal (the declarant too, not only the supplier
    tariff), and while it is assembled the planned rest shares them, as when the proposal was priced."""
    req, data, _, _ = structured(crm, mode="IMPORT", wave_kind="plan")
    with crm["sessions"]() as db:
        quote = db.get(QuoteItem, data["selections"][0]["quote_item_id"])
        group_id = db.get(RequestItem, quote.source_request_item_id).product_group_id
    plan = wave_row(crm, req["wave_id"])
    cmd(crm, f"/waves/{plan['id']}/forecasts", {
        "version": plan["version"], "product_group_id": group_id, "target_quantity": "4", "unit_price_rub": "1000",
        "weight_per_unit": "1", "reason": "План поставки"}, method="put")
    shared = [{**LOGISTICS, "amount": "2000"}, {"name": "Декларант", "amount": "2000", "currency": "RUB",
                                                "method": "BY_QUANTITY", "scope": "WAVE", "stage": "GENERAL"}]
    current = crm["client"].get(f"/api/v1/requests/{req['id']}").json()
    calculation = cmd(crm, f"/requests/{req['id']}/calculations",
                      {**data, "request_version": current["version"], "expenses": shared})
    assert Decimal(calculation["snapshot"]["wave_distribution"]["total_quantity"]) == 4
    _, execution = release(crm, req, calculation, "1")
    handoff(crm, req, execution)
    [work] = wave_row(crm, plan["id"])["working_waves"]
    assert wave_row(crm, work["id"])["budget_expenses"] is None

    actual = wave_row(crm, work["id"])["financial_summary"]["actual"]
    assert (actual["costs_source"], actual["planned_quantity"]) == ("proposal", "3")
    assert "плановый остаток 3 шт." in actual["basis"]
    assert actual["profit"] == format(Decimal(calculation["snapshot"]["totals"]["profit"]), ".2f")
    # Shipped alone, the accepted packing would carry all 4 000 ₽ of shared costs, not a quarter of them.
    assert Decimal(actual["profit"]) - Decimal(actual["accepted_only_profit"]) >= 3000
    summary = card(crm, work["id"])["summary"]
    assert (summary["profit"], summary["profitability_percent"], summary["planned_rest_quantity"]) == (
        actual["profit"], actual["profitability_percent"], "3")
    assert summary["accepted_only_profitability_percent"] == actual["accepted_only_profitability_percent"]

    # Closed by the leader, the wave takes no more orders: the accepted packing carries the costs alone.
    cmd(crm, f"/waves/{work['id']}", {"version": wave_row(crm, work["id"])["version"], "status": "closed",
                                      "reason": "Поставка собрана"}, method="patch")
    closed = wave_row(crm, work["id"])["financial_summary"]["actual"]
    assert (closed["planned_quantity"], closed["profit"]) == ("0", actual["accepted_only_profit"])
    assert "accepted_only_profit" not in closed
    shipped = card(crm, work["id"])["summary"]
    assert (shipped["profit"], shipped["planned_rest_quantity"]) == (closed["profit"], None)


def test_amounts_need_the_finance_rights_on_every_request_of_the_wave(crm):  # noqa: F811
    req, data, _, _ = structured(crm, mode="IMPORT", wave_kind="plan")
    issue(crm, req, calculate(crm, req, data))
    login(crm, "manager@example.com")
    hidden = board(crm)
    assert (hidden["amounts"], hidden["profitability"]) == (False, False)
    shown = next(row for row in hidden["items"] if row["id"] == req["wave_id"])
    assert (shown["requests"], shown["hidden_requests"], shown["quantity"], shown["summary"]) == (0, 1, None, None)

    with crm["sessions"].begin() as db:
        db.query(PermissionGrant).filter_by(user_id=crm["manager"].id, code="requests.read").update({"scope": "all"})
        db.add_all(PermissionGrant(user_id=crm["manager"].id, code=code, scope="all")
                   for code in ("finance.purchase.read", "finance.calculations.read"))
    visible = card(crm, req["wave_id"])
    assert (visible["requests"], visible["quantity"], visible["offered_quantity"]) == (1, "0", "1")
    assert visible["summary"]["offered_sales"] != "0.00"
    assert (visible["summary"]["profit"], visible["summary"]["profitability_percent"]) == (None, None)
