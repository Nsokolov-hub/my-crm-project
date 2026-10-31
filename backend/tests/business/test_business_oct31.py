"""Business remarks of 31 October 2026: leaders only in the leader field, phones checked, two addresses of a supplier
for requests, positions of the waves named and listed, saved filters and the date of the latest proposal."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.crm.models import Request
from tests.support.business import (
    cmd,
    confirm,
    handoff,
    issue,
    preview,
    release,
    request,
    structured,
    wave_row,
)
from tests.support.crm import login, post


def test_the_leader_field_offers_only_those_who_may_decide(crm):  # noqa: F811
    login(crm)
    everyone = crm["client"].get("/api/v1/users").json()["items"]
    assert {row["name"] for row in everyone} == {"Владелец теста", "Менеджер теста"}
    leaders = crm["client"].get("/api/v1/users", params={"permission": "approvals.decide"}).json()["items"]
    assert [row["name"] for row in leaders] == ["Владелец теста"]
    unknown = crm["client"].get("/api/v1/users", params={"permission": "nothing"})
    assert unknown.status_code == 422 and unknown.json()["code"] == "PERMISSION_INVALID"


def test_phones_are_checked_and_kept_as_entered(crm):  # noqa: F811
    login(crm)
    for bad in ("нет", "12345", "+7 999 123", "+7 (999) 123-45-678", "8 800 555 35 35 звонить", "1111111111",
                "+7 999 1234567+1"):
        response = crm["client"].post("/api/v1/counterparties", json={"name": "ООО Тест", "phone": bad})
        assert response.status_code == 422, (bad, response.text)
    good = "+7 (495) 123-45-67 доб. 12, 8 912 345 67 89"
    client = post(crm, "/counterparties", {"name": "ООО Тест", "phone": good})
    assert client["phone"] == good
    uzbek = post(crm, "/counterparties", {"name": "ООО Ташкент", "phone": "+998 90 123 45 67"})
    contact = post(crm, f"/counterparties/{uzbek['id']}/contacts", {"name": "Анвар", "phone": "+998901234567"})
    assert contact["phone"] == "+998901234567"
    refused = crm["client"].post(f"/api/v1/counterparties/{uzbek['id']}/contacts",
                                 json={"name": "Анвар", "phone": "+9987546428"})
    assert refused.status_code == 422


def test_a_request_for_quotes_goes_to_the_shared_and_the_manager_address(crm):  # noqa: F811
    login(crm)
    req = request(crm)
    batch = preview(crm, req["id"], ["Name", "Qty"], [["Lornoxicam", "1"]])
    confirm(crm, req["id"], batch["id"])
    items = crm["client"].get(f"/api/v1/requests/{req['id']}/items").json()["items"]
    supplier = post(crm, "/counterparties", {
        "name": "Aozeal", "kind": "supplier", "email": "sales@aozeal.example",
        "details": {"rfq_email": "rfq@aozeal.example", "rfq_email_manager": "anna@aozeal.example"}})
    drafts = cmd(crm, f"/requests/{req['id']}/supplier-mail/preview", {
        "supplier_ids": [supplier["id"]], "item_ids": [row["id"] for row in items],
        "cc": ["info@ogk-chem.ru", "ANNA@aozeal.example"]})["items"]
    assert drafts[0]["recipients"] == ["rfq@aozeal.example", "anna@aozeal.example"]
    assert drafts[0]["recipient"] == "rfq@aozeal.example, anna@aozeal.example"
    assert drafts[0]["cc"] == ["info@ogk-chem.ru"]
    wrong = crm["client"].patch(f"/api/v1/counterparties/{supplier['id']}", json={
        "version": supplier["version"], "details": {"rfq_email_manager": "anna@"}})
    assert wrong.status_code == 422 and "Почта менеджера поставщика" in wrong.text


def test_the_wave_names_its_positions_and_lists_them_with_their_figures(crm):  # noqa: F811
    req, data, _, _ = structured(crm, mode="IMPORT", wave_kind="plan")
    plan_id = req["wave_id"]
    calculation = cmd(crm, f"/requests/{req['id']}/calculations", data)
    line = calculation["snapshot"]["lines"][0]
    listed = crm["client"].get("/api/v1/waves/positions").json()
    [row] = listed["items"]
    assert (row["status"], row["status_label"], row["name"], row["article"], row["packaging"]) == (
        "calculation", "Расчёт в работе", "Sample", "A123", line["product"]["packaging"])
    assert (row["request_number"], row["wave_id"], row["quantity"]) == (req["number"], plan_id, "1")
    assert Decimal(row["purchase"]) == Decimal(line["detail"]["purchase_rub"]).quantize(Decimal("0.01"))
    assert Decimal(row["expenses"]) == Decimal(line["detail"]["expenses_total"]).quantize(Decimal("0.01"))
    assert Decimal(row["profitability_percent"]) == (
        Decimal(line["detail"]["profit"]) / Decimal(line["net"]) * 100).quantize(Decimal("0.01"))
    assert listed["waves"] == [{"id": plan_id, "number": row["wave_number"]}]

    issue(crm, req, calculation)
    [waiting] = crm["client"].get("/api/v1/waves/positions").json()["items"]
    assert waiting["status_label"] == "Ждём ответа покупателя" and waiting["proposal_number"]

    _, execution = release(crm, req, calculation, "1")
    [ordered] = crm["client"].get("/api/v1/waves/positions", params={"status": "order"}).json()["items"]
    assert (ordered["status_label"], ordered["wave_id"], ordered["allocation_id"]) == (
        "Заказ подтверждён", plan_id, None)
    handoff(crm, req, execution)
    [work] = wave_row(crm, plan_id)["working_waves"]
    [moved] = crm["client"].get("/api/v1/waves/positions").json()["items"]
    assert (moved["wave_id"], moved["status"]) == (work["id"], "order") and moved["client_name"]

    # The working wave shows its allocation by name, article, packaging and client, not by id.
    [allocation] = wave_row(crm, work["id"])["allocations"]
    assert (allocation["position"], allocation["article"], allocation["request_number"]) == (
        "Sample", "A123", req["number"])
    assert allocation["client_name"] == moved["client_name"]


def test_closed_requests_leave_the_list_of_wave_positions(crm):  # noqa: F811
    req, data, _, _ = structured(crm, mode="IMPORT", wave_kind="plan")
    cmd(crm, f"/requests/{req['id']}/calculations", data)
    assert len(crm["client"].get("/api/v1/waves/positions").json()["items"]) == 1
    with crm["sessions"].begin() as db:
        db.get(Request, req["id"]).commercial_stage = "closed_lost"
    assert crm["client"].get("/api/v1/waves/positions").json()["items"] == []


def test_filters_of_the_requests_list_are_saved_and_removed(crm):  # noqa: F811
    login(crm)
    path = "/api/v1/me/preferences/requests.filters"
    assert crm["client"].get(path).json()["value"] is None
    saved = crm["client"].put(path, json={"value": {"stage": "calculation", "owner_id": crm["admin"].id}})
    assert saved.status_code == 200, saved.text
    assert crm["client"].get(path).json()["value"] == {"stage": "calculation", "owner_id": crm["admin"].id}
    assert crm["client"].put(path, json={"value": {"stage": "new"}}).json()["value"] == {"stage": "new"}
    login(crm, "manager@example.com")
    assert crm["client"].get(path).json()["value"] is None
    login(crm)
    assert crm["client"].delete(path).status_code == 200
    assert crm["client"].get(path).json()["value"] is None
    assert crm["client"].get("/api/v1/me/preferences/other").status_code == 404


def test_requests_show_and_filter_by_their_latest_proposal(crm):  # noqa: F811
    req, data, _, _ = structured(crm)
    other = post(crm, "/requests", {"title": "Без КП", "client_id": req["client_id"]})
    calculation = cmd(crm, f"/requests/{req['id']}/calculations", data)
    issue(crm, req, calculation)
    rows = {row["id"]: row for row in crm["client"].get("/api/v1/requests").json()["items"]}
    assert rows[req["id"]]["last_proposal_at"] and rows[other["id"]]["last_proposal_at"] is None
    today = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
    found = crm["client"].get("/api/v1/requests", params={"proposal_from": today, "proposal_to": today}).json()
    assert [row["id"] for row in found["items"]] == [req["id"]]
    later = (date.fromisoformat(today) + timedelta(days=2)).isoformat()
    assert crm["client"].get("/api/v1/requests", params={"proposal_from": later}).json()["items"] == []
    ordered = crm["client"].get("/api/v1/requests", params={"sort": "last_proposal_at"}).json()["items"]
    assert ordered[0]["id"] == req["id"]
    wrong = crm["client"].get("/api/v1/requests", params={"proposal_from": later, "proposal_to": today})
    assert wrong.status_code == 422
