"""Business remarks of 28 October 2026: the planned volume of the wave changed by hand inside a calculation,
and the wave named by the leader in the approval that sends the orders to work."""

from decimal import Decimal

from app.core.models import AuditEvent
from app.crm.models import QuoteItem, RequestItem
from tests.business.test_wave_kinds import new_wave
from tests.support.business import cmd, peer_request, post, release, structured, wave_row

LOGISTICS = [{"name": "Общая логистика", "amount": "1000", "currency": "RUB", "scope": "WAVE",
              "stage": "INTERNATIONAL_LOGISTICS", "method": "BY_QUANTITY"}]


def planned(env, target="10"):
    """An import request on a planned wave with `target` planned pieces and 1 000 ₽ of shared logistics."""
    req, data, seller, supplier = structured(env, mode="IMPORT", wave_kind="plan")
    with env["sessions"]() as db:
        quote = db.get(QuoteItem, data["selections"][0]["quote_item_id"])
        group_id = db.get(RequestItem, quote.source_request_item_id).product_group_id
        item = {"nomenclature_id": quote.nomenclature_id, "packing_id": quote.packing_id, "quantity": "1",
                "unit_price": "1000", "currency_id": quote.currency_id, "delivery_days": 7}
    plan = wave_row(env, req["wave_id"])
    plan = cmd(env, f"/waves/{plan['id']}/forecasts", {
        "version": plan["version"], "product_group_id": group_id, "target_quantity": target,
        "unit_price_rub": "1000", "weight_per_unit": "1", "reason": "План запуска"}, method="put")
    plan = cmd(env, f"/waves/{plan['id']}/budget", {"version": plan["version"], "profile_id": data["profile_id"],
               "expenses": LOGISTICS, "reason": "Бюджет запуска"}, method="put")
    data["selections"][0]["quantity"] = "2"
    data["expenses"] = LOGISTICS
    return req, data, seller, supplier, plan, group_id, item


def test_the_planned_volume_is_changed_inside_a_calculation_only_with_a_comment(crm):  # noqa: F811
    req, data, _, _, plan, group_id, _ = planned(crm)
    offered = crm["client"].get(f"/api/v1/requests/{req['id']}/wave-expenses").json()["planned_volume"]
    assert [(row["product_group_id"], Decimal(row["wave_quantity"])) for row in offered] == [(group_id, 10)]

    volume = [{"product_group_id": group_id, "quantity": "20"}]
    refused = cmd(crm, f"/requests/{req['id']}/calculations/preview", {**data, "planned_volume": volume}, status=422)
    assert (refused["code"], refused["field"]) == ("PLANNED_VOLUME_COMMENT", "planned_volume_comment")
    blank = {**data, "planned_volume": volume, "planned_volume_comment": "  "}
    assert cmd(crm, f"/requests/{req['id']}/calculations", blank, status=422)["code"] == "PLANNED_VOLUME_COMMENT"

    # 20 planned pieces: 2 of this calculation and 18 more share the 1 000 ₽ — 100 ₽ for the two.
    saved = cmd(crm, f"/requests/{req['id']}/calculations", {
        **data, "planned_volume": volume, "planned_volume_comment": "Клиент берёт ещё 10 шт. в ноябре"})
    snapshot = saved["snapshot"]
    assert Decimal(snapshot["wave_distribution"]["total_quantity"]) == 20
    assert Decimal(snapshot["totals"]["international_logistics"]) == 100
    assert snapshot["wave"]["planned_volume"] == {"comment": "Клиент берёт ещё 10 шт. в ноябре", "rows": [{
        "product_group_id": group_id, "product_group_name": "Стандартные образцы", "wave_quantity": "10",
        "quantity": "20"}]}
    assert saved["snapshot"]["input"]["planned_volume_comment"] == "Клиент берёт ещё 10 шт. в ноябре"

    # The wave itself keeps its volume; other calculations still divide by 10.
    assert Decimal(wave_row(crm, plan["id"])["forecasts"][0]["target_quantity"]) == 10
    current = crm["client"].get(f"/api/v1/requests/{req['id']}").json()
    plain = cmd(crm, f"/requests/{req['id']}/calculations/preview", {**data, "request_version": current["version"]})["snapshot"]
    assert Decimal(plain["wave_distribution"]["total_quantity"]) == 10
    assert "planned_volume" not in plain["wave"]

    # The wave's own volume needs no comment, and an unknown group is refused.
    same = cmd(crm, f"/requests/{req['id']}/calculations/preview", {
        **data, "request_version": current["version"],
        "planned_volume": [{"product_group_id": group_id, "quantity": "10"}]})["snapshot"]
    assert Decimal(same["wave_distribution"]["total_quantity"]) == 10 and "planned_volume" not in same["wave"]
    unknown = cmd(crm, f"/requests/{req['id']}/calculations/preview", {
        **data, "request_version": current["version"], "planned_volume_comment": "Другая группа",
        "planned_volume": [{"product_group_id": "missing", "quantity": "5"}]}, status=422)
    assert (unknown["code"], unknown["field"]) == ("PLANNED_VOLUME_GROUP", "planned_volume.0.product_group_id")

    # Fewer pieces than the calculation holds: nothing more is planned, the two carry the 1 000 ₽ alone.
    small = cmd(crm, f"/requests/{req['id']}/calculations/preview", {
        **data, "request_version": current["version"], "planned_volume_comment": "Плана больше нет",
        "planned_volume": [{"product_group_id": group_id, "quantity": "0"}]})["snapshot"]
    assert Decimal(small["wave_distribution"]["total_quantity"]) == 2
    assert Decimal(small["totals"]["international_logistics"]) == 1000


def test_the_leader_names_the_wave_of_the_approved_orders(crm):  # noqa: F811
    req, data, seller, supplier, plan, _, item = planned(crm)
    first = cmd(crm, f"/requests/{req['id']}/calculations", data)
    _, execution = release(crm, req, first, "2")
    approval = cmd(crm, f"/requests/{req['id']}/approvals", {"execution_ids": [execution["id"]],
                   "reviewer_id": crm["admin"].id})
    choice = crm["client"].get(f"/api/v1/approvals/{approval['id']}/waves").json()
    # No working wave yet: a new one from the plan is offered by default.
    assert choice["required"] is True and choice["wave_id"] == plan["id"]
    assert [(row["id"], row["new"]) for row in choice["items"]] == [(plan["id"], True)]
    assert choice["items"][0]["label"] == f"Новая рабочая волна из плановой «{plan['number']}»"

    decided = cmd(crm, f"/approvals/{approval['id']}/decision", {
        "version": approval["version"], "decision": "approved", "reason": "В работу", "wave_id": plan["id"]})
    [working] = wave_row(crm, plan["id"])["working_waves"]
    assert decided["wave_id"] == working["id"]
    assert len(wave_row(crm, working["id"])["allocations"]) == 1
    # The request now lives in the wave its orders went to.
    assert crm["client"].get(f"/api/v1/requests/{req['id']}").json()["wave_id"] == working["id"]
    with crm["sessions"]() as db:
        moved = db.query(AuditEvent).filter_by(entity_id=req["id"], action="assign_wave").all()
        assert moved[-1].reason == f"Волна указана руководителем при согласовании: {working['number']}"

    # The next order: the open working wave is offered first; the leader may still start a new one.
    peer, quote = peer_request(crm, seller, supplier, item, plan["id"])
    calculation = cmd(crm, f"/requests/{peer['id']}/calculations", {
        **data, "request_version": peer["version"], "selections": [{"quote_item_id": quote["id"], "quantity": "1"}]})
    _, execution = release(crm, peer, calculation, "1")
    approval = cmd(crm, f"/requests/{peer['id']}/approvals", {"execution_ids": [execution["id"]],
                   "reviewer_id": crm["admin"].id})
    choice = crm["client"].get(f"/api/v1/approvals/{approval['id']}/waves").json()
    assert choice["wave_id"] == working["id"]
    assert [(row["id"], row["new"]) for row in choice["items"]] == [(working["id"], False), (plan["id"], True)]
    cmd(crm, f"/approvals/{approval['id']}/decision", {
        "version": approval["version"], "decision": "approved", "reason": "Отдельной отправкой",
        "wave_id": plan["id"]})
    numbers = [row["number"] for row in wave_row(crm, plan["id"])["working_waves"]]
    assert numbers == [f"{plan['number']} — рабочая 1", f"{plan['number']} — рабочая 2"]
    assert len(wave_row(crm, working["id"])["allocations"]) == 1


def test_the_wave_of_an_approval_must_be_open_and_of_the_supplier(crm):  # noqa: F811
    req, data, _, _, plan, _, _ = planned(crm)
    other = post(crm, "/counterparties", {"name": "Другой поставщик", "kind": "supplier",
                 "details": {"calculation_type": "IMPORT"}})
    foreign = new_wave(crm, other, kind="work")["id"]
    first = cmd(crm, f"/requests/{req['id']}/calculations", data)
    _, execution = release(crm, req, first, "2")
    approval = cmd(crm, f"/requests/{req['id']}/approvals", {"execution_ids": [execution["id"]],
                   "reviewer_id": crm["admin"].id})
    decision = {"version": approval["version"], "decision": "approved", "reason": "В работу"}
    refused = cmd(crm, f"/approvals/{approval['id']}/decision", {**decision, "wave_id": foreign}, status=422)
    assert (refused["code"], refused["field"]) == ("WAVE_SUPPLIER", "wave_id")
    missing = cmd(crm, f"/approvals/{approval['id']}/decision", {**decision, "wave_id": "missing"}, status=422)
    assert (missing["code"], missing["field"]) == ("WAVE_REQUIRED", "wave_id")
    # Returning needs no wave; without a choice the request's plan still fills its open working wave.
    cmd(crm, f"/approvals/{approval['id']}/decision", decision)
    [working] = wave_row(crm, plan["id"])["working_waves"]
    assert len(wave_row(crm, working["id"])["allocations"]) == 1
    assert crm["client"].get(f"/api/v1/requests/{req['id']}").json()["wave_id"] == plan["id"]


def test_a_group_the_plan_has_no_volume_for_gets_one_inside_the_calculation(crm):  # noqa: F811
    """Remark of 30 October 2026: a plan without planned volume (Asnail) — the volume is typed in the calculation;
    the planned pieces are priced and weighed like the calculation's position."""
    req, data, _, _ = structured(crm, mode="IMPORT", wave_kind="plan")
    with crm["sessions"]() as db:
        quote = db.get(QuoteItem, data["selections"][0]["quote_item_id"])
        group_id = db.get(RequestItem, quote.source_request_item_id).product_group_id
    plan = wave_row(crm, req["wave_id"])
    cmd(crm, f"/waves/{plan['id']}/budget", {"version": plan["version"], "profile_id": data["profile_id"],
        "expenses": LOGISTICS, "reason": "Бюджет запуска"}, method="put")
    assert crm["client"].get(f"/api/v1/requests/{req['id']}/wave-expenses").json()["planned_volume"] == []
    current = crm["client"].get(f"/api/v1/requests/{req['id']}").json()
    data.update(request_version=current["version"], expenses=LOGISTICS)
    data["selections"][0]["quantity"] = "2"
    alone = cmd(crm, f"/requests/{req['id']}/calculations/preview", data)["snapshot"]
    assert Decimal(alone["totals"]["international_logistics"]) == 1000

    volume = {"planned_volume": [{"product_group_id": group_id, "quantity": "10"}]}
    assert cmd(crm, f"/requests/{req['id']}/calculations/preview", {**data, **volume},
               status=422)["code"] == "PLANNED_VOLUME_COMMENT"
    saved = cmd(crm, f"/requests/{req['id']}/calculations", {
        **data, **volume, "planned_volume_comment": "План Asnail на ноябрь"})["snapshot"]
    assert Decimal(saved["wave_distribution"]["total_quantity"]) == 10
    assert Decimal(saved["wave_distribution"]["forecast_quantity"]) == 8
    assert Decimal(saved["totals"]["international_logistics"]) == 200
    assert saved["wave"]["planned_volume"]["rows"] == [{
        "product_group_id": group_id, "product_group_name": "Стандартные образцы", "wave_quantity": "0",
        "quantity": "10", "in_plan": False}]
    # The wave itself still has no planned volume.
    assert wave_row(crm, plan["id"])["forecasts"] == []
