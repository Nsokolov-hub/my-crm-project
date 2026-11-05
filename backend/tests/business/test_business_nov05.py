"""Business remarks of 5 November 2026: a wave for each approved position, the name of a wave, requests of several
clients, and the lines of a site request — free to edit, loaded from Excel and sent to suppliers without the site's
«Аналоги» column."""

from uuid import uuid4

from sqlalchemy import select

from app.commerce.procurement import rfq_table
from app.crm.models import RequestItem
from tests.business.test_business_oct28 import planned
from tests.business.test_wave_kinds import new_wave
from tests.support.business import cmd, post, release, wave_row
from tests.support.crm import login
from tests.support.site import deliver, payload


def approval_of(crm, req, data):
    calculation = cmd(crm, f"/requests/{req['id']}/calculations", data)
    _, execution = release(crm, req, calculation, "2")
    approval = cmd(crm, f"/requests/{req['id']}/approvals", {"execution_ids": [execution["id"]],
                   "reviewer_id": crm["admin"].id})
    return execution, approval


def test_a_position_goes_to_its_own_wave_and_the_plan_stays(crm):  # noqa: F811
    req, data, _, supplier, plan, _, _ = planned(crm)
    nearest = new_wave(crm, supplier, kind="work")
    execution, approval = approval_of(crm, req, data)

    offered = crm["client"].get(f"/api/v1/approvals/{approval['id']}/waves").json()
    [line] = offered["lines"]
    assert line["execution_id"] == execution["id"] and line["supplier_id"] == supplier["id"]
    assert line["label"].endswith("— 2 pcs")
    assert {row["id"]: row["new"] for row in offered["items"]} == {nearest["id"]: False, plan["id"]: True}

    decision = {"version": approval["version"], "decision": "approved", "reason": "В работу"}
    stranger = cmd(crm, f"/approvals/{approval['id']}/decision",
                   {**decision, "line_waves": {str(uuid4()): plan["id"]}}, status=422)
    assert (stranger["code"], stranger["field"]) == ("WAVE_LINE", "line_waves")

    # The position named for the plan: a new working wave copied from it; the plan stays for other calculations.
    cmd(crm, f"/approvals/{approval['id']}/decision", {**decision, "line_waves": {execution["id"]: plan["id"]}})
    [working] = wave_row(crm, plan["id"])["working_waves"]
    assert working["number"] == f"{plan['number']} — рабочая 1"
    [allocation] = wave_row(crm, working["id"])["allocations"]
    assert allocation["execution_id"] == execution["id"]
    still = wave_row(crm, plan["id"])
    assert (still["kind"], still["archived"]) == ("plan", False)
    assert wave_row(crm, nearest["id"])["allocations"] == []
    assert crm["client"].get(f"/api/v1/requests/{req['id']}").json()["wave_id"] == working["id"]


def test_a_position_goes_with_the_nearest_supply(crm):  # noqa: F811
    req, data, _, supplier, plan, _, _ = planned(crm)
    nearest = new_wave(crm, supplier, kind="work")
    execution, approval = approval_of(crm, req, data)
    cmd(crm, f"/approvals/{approval['id']}/decision", {
        "version": approval["version"], "decision": "approved", "reason": "Готова, грузим с ближайшей",
        "line_waves": {execution["id"]: nearest["id"]}})
    [allocation] = wave_row(crm, nearest["id"])["allocations"]
    assert allocation["execution_id"] == execution["id"]
    assert wave_row(crm, plan["id"])["working_waves"] == []


def test_the_name_of_a_wave_is_edited(crm):  # noqa: F811
    _, _, _, supplier, plan, _, _ = planned(crm)
    other = new_wave(crm, supplier, kind="work")
    renamed = cmd(crm, f"/waves/{plan['id']}", {"version": plan["version"], "number": "  Аозеал   52 неделя ",
                  "reason": "Понятное название"}, method="patch")
    assert renamed["number"] == "Аозеал 52 неделя"
    taken = cmd(crm, f"/waves/{other['id']}", {"version": other["version"], "number": "Аозеал 52 неделя",
                "reason": "Повтор"}, method="patch", status=409)
    assert (taken["code"], taken["field"]) == ("WAVE_NUMBER", "number")


def test_requests_of_several_clients(crm):
    login(crm)
    clients = [post(crm, "/counterparties", {"name": f"Клиент {n}", "kind": "client"}) for n in range(3)]
    for client in clients:
        post(crm, "/requests", {"title": f"Заявка {client['name']}", "client_id": client["id"]})
    chosen = ",".join(client["id"] for client in clients[:2])
    listed = crm["client"].get(f"/api/v1/requests?client_ids={chosen}").json()["items"]
    assert sorted(row["client_id"] for row in listed) == sorted(client["id"] for client in clients[:2])
    names = crm["client"].get(f"/api/v1/counterparties?ids={chosen}").json()["items"]
    assert sorted(row["name"] for row in names) == ["Клиент 0", "Клиент 1"]
    # The saved filters keep the ids of the chosen clients.
    saved = crm["client"].put("/api/v1/me/preferences/requests.filters",
                              json={"value": {"client_ids": ",".join([clients[0]["id"]] * 40)}})
    assert saved.status_code == 200, saved.text


def test_site_lines_are_loaded_from_a_table_and_reach_the_supplier_whole(site):
    response = deliver(site, payload())
    assert response.status_code == 201, response.text
    site_request = response.json()["id"]
    login(site)
    table = "Наименование;Артикул;Фасовка;Количество\nEnoxaparin Sodium USP;1235831;1 ampoule;9 шт\n;;;\n"
    loaded = site["client"].post(f"/api/v1/site-requests/{site_request}/conversion/table",
                                 files={"file": ("lines.csv", table.encode(), "text/csv")})
    assert loaded.status_code == 200, loaded.text
    [line] = loaded.json()["items"]
    assert (line["description"], line["article"], line["packaging"], line["quantity"], line["unit"]) == (
        "Enoxaparin Sodium USP", "1235831", "1 ampoule", "9", "pcs")

    window = site["client"].get(f"/api/v1/site-requests/{site_request}/conversion").json()
    proposal = window["proposal"]
    body = {"version": 1, "client": proposal["client"], "contact": proposal["contact"],
            "title": proposal["title"], "items": [line]}
    converted = site["client"].post(f"/api/v1/site-requests/{site_request}/convert", json=body,
                                    headers={"Idempotency-Key": str(uuid4())})
    assert converted.status_code == 200, converted.text
    with site["sessions"]() as db:
        items = db.scalars(select(RequestItem).where(
            RequestItem.request_id == converted.json()["created"]["request_id"])).all()
        assert [item.article for item in items] == ["1235831"]
        columns, rows = rfq_table(db, items)
        assert "Аналоги (сайт)" not in columns
        values = dict(zip(columns, rows[0], strict=True))
        assert (values["Name"], values["Article"], values["Packing"], values["Quantity"]) == (
            "Enoxaparin Sodium USP", "1235831", "1 ampoule", "9")

        # A line converted before 5 November kept only the brand and the analogue choice as source columns: the
        # supplier now gets its own fields instead.
        old = items[0]
        old.source_columns, old.source_values = ["Бренд", "Аналоги (сайт)"], ["Sigma-Aldrich", "Только этот товар"]
        columns, rows = rfq_table(db, [old])
        assert "Аналоги (сайт)" not in columns and "Бренд" not in columns
        assert dict(zip(columns, rows[0], strict=True))["Article"] == "1235831"
        db.rollback()
