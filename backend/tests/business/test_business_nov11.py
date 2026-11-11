"""Business remarks of 11 November 2026: approvals named by their request, client, suppliers and waves; positions of
the waves with their supplier and the term of the quote, filtered by supplier and manufacturer; saved filters of the
waves; a screen that failed in the browser reaches the API log."""

import json
import logging

from tests.business.test_business_nov05 import approval_of
from tests.business.test_business_oct28 import planned
from tests.support.business import cmd, structured, wave_row


def test_an_approval_is_named_by_its_request_client_supplier_and_wave(crm):  # noqa: F811
    req, data, _, supplier, plan, _, _ = planned(crm)
    execution, approval = approval_of(crm, req, data)
    client = crm["client"].get(f"/api/v1/counterparties/{req['client_id']}").json()["name"]

    [listed] = crm["client"].get(f"/api/v1/requests/{req['id']}/approvals").json()["items"]
    assert listed["title"] == f"Заявка №{req['number']} · {client}"
    assert listed["details"] == f"1 позиция · {supplier['name']} · волна {plan['number']}"
    assert (listed["supplier_names"], listed["wave_numbers"], listed["positions"]) == (
        [supplier["name"]], [plan["number"]], 1)
    assert listed["position_names"]
    [registry] = [row for row in crm["client"].get("/api/v1/approvals").json()["items"] if row["id"] == approval["id"]]
    assert (registry["title"], registry["details"], registry["reviewer_name"]) == (
        listed["title"], listed["details"], crm["admin"].name)

    # Approved: the working wave the position went to.
    cmd(crm, f"/approvals/{approval['id']}/decision", {
        "version": approval["version"], "decision": "approved", "reason": "В работу",
        "line_waves": {execution["id"]: plan["id"]}})
    [working] = wave_row(crm, plan["id"])["working_waves"]
    [decided] = crm["client"].get(f"/api/v1/requests/{req['id']}/approvals").json()["items"]
    assert decided["wave_numbers"] == [working["number"]]
    assert decided["details"].endswith(f"волна {working['number']}")


def test_wave_positions_show_the_supplier_and_the_term_of_the_quote(crm):  # noqa: F811
    req, data, _, _ = structured(crm, mode="IMPORT", wave_kind="plan")
    cmd(crm, f"/requests/{req['id']}/calculations", data)
    listed = crm["client"].get("/api/v1/waves/positions").json()
    [row] = listed["items"]
    assert (row["supplier_name"], row["delivery_days"], row["manufacturer"]) == ("Aozeal", 7, "Aozeal")
    assert "quote_item_id" not in row
    assert (listed["suppliers"], listed["manufacturers"]) == (["Aozeal"], ["Aozeal"])

    def found(**params):
        return crm["client"].get("/api/v1/waves/positions", params=params).json()["items"]

    assert len(found(supplier="Aozeal")) == 1 and found(supplier="Другой") == []
    assert len(found(manufacturer="Aozeal")) == 1 and found(manufacturer="Sigma") == []
    assert len(found(q="aoze")) == 1


def test_the_filters_of_the_waves_are_saved(crm):  # noqa: F811
    structured(crm, mode="IMPORT", wave_kind="plan")
    for key in ("waves.filters", "wave-positions.filters"):
        saved = crm["client"].put(f"/api/v1/me/preferences/{key}", json={"value": {"supplier": "Aozeal", "sort": "x"}})
        assert saved.status_code == 200, saved.text
        assert crm["client"].get(f"/api/v1/me/preferences/{key}").json()["value"] == {"supplier": "Aozeal", "sort": "x"}
    assert crm["client"].get("/api/v1/me/preferences/waves.other").status_code == 404


def test_a_screen_that_failed_reaches_the_log(crm, caplog):  # noqa: F811
    structured(crm, mode="IMPORT", wave_kind="plan")
    with caplog.at_level(logging.ERROR, logger="crm.client"):
        answer = crm["client"].post("/api/v1/client-errors", json={
            "message": "Cannot read properties of undefined", "stack": "at Waves", "path": "/waves"},
            headers={"X-CSRF-Token": crm["client"].headers.get("X-CSRF-Token", "")})
    assert answer.status_code == 204, answer.text
    [record] = [row for row in caplog.records if row.name == "crm.client"]
    entry = json.loads(record.getMessage())
    assert (entry["event"], entry["path"], entry["error"]) == (
        "client_error", "/waves", "Cannot read properties of undefined")
    too_long = crm["client"].post("/api/v1/client-errors", json={"message": "x" * 2001})
    assert too_long.status_code == 422
