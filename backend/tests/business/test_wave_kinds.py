"""Planned and working waves, and the archive of waves no longer in use."""

from datetime import date, timedelta

from app.core.models import AuditEvent
from app.crm.models import QuoteItem, RequestItem
from tests.support.business import cmd, handoff, peer_request, release, structured, wave_row


def new_wave(env, supplier, kind="plan", status=200):
    return cmd(env, "/waves", {
        "kind": kind, "supplier_id": supplier["id"], "route": "Москва", "origin_country": "RU",
        "owner_id": env["admin"].id, "close_date": date.today().isoformat(),
        "departure_date": date.today().isoformat(), "arrival_date": (date.today() + timedelta(days=7)).isoformat(),
    }, status=status)


def archive(env, wave, archived=True, status=200):
    return cmd(env, f"/waves/{wave['id']}/archive", {"version": wave["version"], "archived": archived,
               "reason": "Волна больше не нужна"}, status=status)


def listed(env, query=""):
    response = env["client"].get("/api/v1/waves" + query)
    assert response.status_code == 200, response.text
    return {row["id"] for row in response.json()["items"]}


def quote_data(env, data):
    with env["sessions"]() as db:
        quote = db.get(QuoteItem, data["selections"][0]["quote_item_id"])
        return {"nomenclature_id": quote.nomenclature_id, "packing_id": quote.packing_id, "quantity": "1",
                "unit_price": "1000", "currency_id": quote.currency_id, "delivery_days": 7}


def released(env, req, data, quote_id=None):
    payload = {**data, "request_version": req["version"]}
    if quote_id:
        payload["selections"] = [{"quote_item_id": quote_id, "quantity": "1"}]
    calculation = cmd(env, f"/requests/{req['id']}/calculations", payload)
    return release(env, req, calculation, "1")[1]


def test_one_planned_wave_per_supplier_and_the_archive_takes_it_out_of_use(crm):  # noqa: F811
    req, data, _, supplier = structured(crm, mode="IMPORT", wave_kind="plan")
    plan = wave_row(crm, req["wave_id"])
    assert (plan["kind"], plan["archived"], plan["working_waves"]) == ("plan", False, [])
    duplicate = new_wave(crm, supplier, status=409)
    assert (duplicate["code"], duplicate["field"]) == ("WAVE_PLAN_EXISTS", "supplier_id")
    parallel = new_wave(crm, supplier, kind="work")
    assert listed(crm, "?kind=plan") == {plan["id"]} and parallel["id"] in listed(crm, "?kind=work&open=true")
    assert listed(crm, "?q=" + supplier["name"][:5]) == {plan["id"], parallel["id"]}
    assert listed(crm, "?q=" + parallel["number"]) == {parallel["id"]} and listed(crm, "?q=нет-такой") == set()

    archived = archive(crm, plan)
    assert archived["archived"] is True
    assert archive(crm, archived, status=409)["code"] == "WAVE_ARCHIVE_STATE"
    assert plan["id"] not in listed(crm) and listed(crm, "?archived=true") == {plan["id"]}
    assert wave_row(crm, plan["id"])["archived"] is True
    with crm["sessions"]() as db:
        assert db.query(AuditEvent).filter_by(entity_id=plan["id"], action="archived").count() == 1

    # Nothing new is planned, calculated or changed in the archived plan, though a request still names it.
    refused = cmd(crm, f"/requests/{req['id']}/calculations/preview", data, status=422)
    assert (refused["code"], refused["field"]) == ("WAVE_ARCHIVED", "wave_id")
    edit = {"version": archived["version"], "departure_week": 10, "departure_year": 2030, "reason": "Сдвиг"}
    assert cmd(crm, f"/waves/{plan['id']}", edit, method="patch", status=422)["code"] == "WAVE_ARCHIVED"
    successor = new_wave(crm, supplier)
    current = crm["client"].get(f"/api/v1/requests/{req['id']}").json()
    moved = cmd(crm, f"/requests/{req['id']}/wave", {"request_version": current["version"], "wave_id": successor["id"]},
                method="put")
    back = cmd(crm, f"/requests/{req['id']}/wave", {"request_version": moved["version"], "wave_id": plan["id"]},
               method="put", status=422)
    assert back["code"] == "WAVE_ARCHIVED"
    assert cmd(crm, f"/requests/{req['id']}/calculations/preview", {**data, "request_version": moved["version"]})

    # A plan comes back only while the supplier has no other plan in use.
    assert archive(crm, archived, archived=False, status=409)["code"] == "WAVE_PLAN_EXISTS"
    archive(crm, successor)
    assert archive(crm, archived, archived=False)["archived"] is False


def test_plans_have_no_shipping_stages_and_working_waves_take_no_planned_volume(crm):  # noqa: F811
    req, data, _, supplier = structured(crm, mode="IMPORT", wave_kind="plan")
    plan = wave_row(crm, req["wave_id"])
    stage = cmd(crm, f"/waves/{plan['id']}", {"version": plan["version"], "status": "closed", "reason": "Закрыть"},
                method="patch", status=422)
    assert (stage["code"], stage["field"]) == ("WAVE_PLAN_STATUS", "status")
    later = cmd(crm, f"/waves/{plan['id']}", {"version": plan["version"], "close_week": 8, "close_year": 2030,
                "departure_week": 10, "departure_year": 2030, "arrival_week": 12, "arrival_year": 2030,
                "reason": "Плановые сроки"}, method="patch")
    assert (later["departure_week"], later["departure_year"], later["status"]) == (10, 2030, "planned")
    with crm["sessions"]() as db:
        quote = db.get(QuoteItem, data["selections"][0]["quote_item_id"])
        group_id = db.get(RequestItem, quote.source_request_item_id).product_group_id
    work = new_wave(crm, supplier, kind="work")
    forecast = {"version": work["version"], "product_group_id": group_id, "target_quantity": "10",
                "unit_price_rub": "1000", "reason": "План"}
    refused = cmd(crm, f"/waves/{work['id']}/forecasts", forecast, method="put", status=422)
    assert (refused["code"], refused["field"]) == ("WAVE_FORECAST_KIND", "product_group_id")


def test_confirmed_orders_fill_the_open_working_wave_and_a_closed_one_gets_a_successor(crm):  # noqa: F811
    req, data, seller, supplier = structured(crm, mode="IMPORT", wave_kind="plan")
    item = quote_data(crm, data)
    plan_id = req["wave_id"]
    handoff(crm, req, released(crm, req, data))
    [first] = wave_row(crm, plan_id)["working_waves"]

    peer, quote = peer_request(crm, seller, supplier, item, plan_id)
    handoff(crm, peer, released(crm, peer, data, quote["id"]))
    plan = wave_row(crm, plan_id)
    assert [row["id"] for row in plan["working_waves"]] == [first["id"]]
    work = wave_row(crm, first["id"])
    assert len(work["allocations"]) == 2

    # Orders move only between working waves: a plan never holds them.
    allocation = work["allocations"][0]
    transfer = {"version": allocation["version"], "target_wave_id": plan_id, "quantity": "1", "reason": "Перенос"}
    assert cmd(crm, f"/allocations/{allocation['id']}/transfer", transfer, status=422)["code"] == "TARGET_WAVE"

    cmd(crm, f"/waves/{work['id']}", {"version": work["version"], "status": "closed",
        "reason": "Заказ поставщику отправлен"}, method="patch")
    late, quote = peer_request(crm, seller, supplier, item, plan_id)
    handoff(crm, late, released(crm, late, data, quote["id"]))
    plan = wave_row(crm, plan_id)
    assert [row["number"] for row in plan["working_waves"]] == [
        f"{plan['number']} — рабочая 1", f"{plan['number']} — рабочая 2"]
    assert [row["status"] for row in plan["working_waves"]] == ["closed", "assembling"]


def test_a_position_added_to_a_plan_lands_in_its_working_wave(crm):  # noqa: F811
    req, data, _, _ = structured(crm, mode="IMPORT", wave_kind="plan")
    plan_id = req["wave_id"]
    execution = released(crm, req, data)
    current = crm["client"].get(f"/api/v1/requests/{req['id']}").json()
    cmd(crm, f"/requests/{req['id']}/wave", {"request_version": current["version"], "wave_id": None}, method="put")
    approval = handoff(crm, req, execution)
    assert wave_row(crm, plan_id)["working_waves"] == []
    allocation = cmd(crm, f"/waves/{plan_id}/allocations", {"execution_id": execution["id"],
                     "approval_id": approval["id"], "quantity": "1"})
    [work] = wave_row(crm, plan_id)["working_waves"]
    assert allocation["wave_id"] == work["id"]


def test_working_waves_in_progress_stay_out_of_the_archive_and_archived_plans_take_no_orders(crm):  # noqa: F811
    req, data, seller, supplier = structured(crm, mode="IMPORT", wave_kind="plan")
    item = quote_data(crm, data)
    handoff(crm, req, released(crm, req, data))
    plan = wave_row(crm, req["wave_id"])
    work = wave_row(crm, plan["working_waves"][0]["id"])
    assert archive(crm, work, status=422)["code"] == "WAVE_IN_PROGRESS"
    cancelled = cmd(crm, f"/waves/{work['id']}", {"version": work["version"], "status": "cancelled",
                    "reason": "Поставщик отказал"}, method="patch")
    assert archive(crm, cancelled)["archived"] is True

    peer, quote = peer_request(crm, seller, supplier, item, plan["id"])
    execution = released(crm, peer, data, quote["id"])
    archive(crm, plan)
    approval = cmd(crm, f"/requests/{peer['id']}/approvals", {"execution_ids": [execution["id"]],
                   "reviewer_id": crm["admin"].id})
    refused = cmd(crm, f"/approvals/{approval['id']}/decision", {"version": approval["version"],
                  "decision": "approved", "reason": "Передать"}, status=422)
    assert refused["code"] == "WAVE_ARCHIVED"
