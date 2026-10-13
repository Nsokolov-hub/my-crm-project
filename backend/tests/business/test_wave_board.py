"""The board of waves: sales, profitability, pieces and logistics per unit of every wave in use."""

from decimal import Decimal

from app.core.models import PermissionGrant
from app.crm.models import Request
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
    assert shown["quantity"] == "1"
    assert Decimal(summary["sales"]) == Decimal(line["total"]).quantize(Decimal("0.01"))
    assert Decimal(summary["sale_net"]) == Decimal(line["net"]).quantize(Decimal("0.01"))
    assert Decimal(summary["profit"]) == Decimal(line["detail"]["profit"]).quantize(Decimal("0.01"))
    assert Decimal(summary["profitability_percent"]) == (
        Decimal(line["detail"]["profit"]) / Decimal(line["net"]) * 100).quantize(Decimal("0.01"))
    assert Decimal(summary["logistics_per_unit"]) == Decimal("500.00")
    assert (summary["offered_sales"], summary["accepted_sales"]) == (summary["sales"], "0.00")

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
    assert working["summary"]["sales"] == working["summary"]["accepted_sales"]
    assert [row["id"] for row in board(crm)["items"]].count(work["id"]) == 1


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
    assert (visible["requests"], visible["quantity"]) == (1, "1")
    assert visible["summary"]["sales"] != "0.00"
    assert (visible["summary"]["profit"], visible["summary"]["profitability_percent"]) == (None, None)
