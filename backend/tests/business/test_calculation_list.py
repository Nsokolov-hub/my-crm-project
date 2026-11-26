"""Saved calculation versions are listed with a fixed amount of work, including wave staleness."""

from decimal import ROUND_HALF_UP, Decimal

from tests.support.business import cmd, structured
from tests.support.queries import query_count


def test_calculation_versions_page_does_not_recompute_per_version(crm):
    req, data, _, _ = structured(crm, mode="IMPORT")
    path = f"/requests/{req['id']}/calculations"
    cmd(crm, path, data)
    few = query_count(crm, path)
    for _ in range(4):
        cmd(crm, path, {**data, "request_version": crm["client"].get(f"/api/v1/requests/{req['id']}").json()["version"]})
    listing = crm["client"].get("/api/v1" + path).json()
    assert listing["total"] == 5 and len(listing["items"]) == 5
    assert {row["version_number"] for row in listing["items"]} == {1, 2, 3, 4, 5}
    assert all("wave_stale" in row for row in listing["items"])
    assert query_count(crm, path) == few


def test_calculation_list_exposes_filtered_commerce_summary_without_changing_snapshot(crm):
    req, data, _, _ = structured(crm, mode="IMPORT")
    path = f"/requests/{req['id']}/calculations"
    saved = cmd(crm, path, data)

    [row] = crm["client"].get("/api/v1" + path).json()["items"]
    totals = row["snapshot"]["totals"]
    expected_profitability = (
        Decimal(totals["profit"]) / Decimal(totals["sale_net"]) * 100
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    assert row["supplier_names"] == ["Aozeal"]
    assert row["delivery_days"] == 7
    assert row["snapshot"]["input"]["delivery_days"] == 14
    assert row["purchase_amount"] == str(Decimal(totals["purchase_rub"]).quantize(Decimal("0.01")))
    assert row["purchase_currency"] == row["snapshot"]["management_currency"]
    assert row["sale_amount"] == totals["sale_total"]
    assert row["sale_currency"] == row["snapshot"]["currency"]
    assert row["profitability_percent"] == str(expected_profitability)
    assert row["snapshot"] == saved["snapshot"]
    assert "supplier_names" not in row["snapshot"]


def test_calculation_versions_are_paginated(crm):
    req, data, _, _ = structured(crm)
    path = f"/requests/{req['id']}/calculations"
    for _ in range(3):
        cmd(crm, path, {**data, "request_version": crm["client"].get(f"/api/v1/requests/{req['id']}").json()["version"]})
    page = crm["client"].get("/api/v1" + path, params={"page": 2, "page_size": 2}).json()
    assert page["total"] == 3 and page["page"] == 2 and len(page["items"]) == 1
    assert page["items"][0]["version_number"] == 1
