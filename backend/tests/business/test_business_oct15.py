"""Business remarks of 15 October 2026: quote imports, quantity, delivery term, wave split and the calculated mark."""

import time
from decimal import Decimal

from app.core.models import PermissionGrant
from app.crm.models import QuoteItem, Request, RequestItem
from app.crm.table_imports import QUOTE_COLUMNS
from tests.support.business import cmd, confirm, preview, quote_file, structured, wave_row
from tests.support.crm import login


def standard(supplier_code, position, article):
    return quote_file(supplier_code, {
        "source_row": str(position), "article": article, "name": f"Standard {article}",
        "packing_value": "100", "packing_unit": "mg", "unit_price": "6000", "currency": "rub",
        "product_group": "Стандартные образцы",
    })


def test_quote_file_without_two_positions_names_them_and_imports_the_rest(crm):  # noqa: F811
    req, _, _, supplier = structured(crm)
    # The request has 40 positions; the setup quote is a quote-only line, not one of them.
    items = preview(crm, req["id"], ["Name", "Article", "Quantity"],
                    [[f"Позиция {number}", f"REQ-{number}", "1"] for number in range(1, 41)])
    assert items["summary"]["errors"] == 0
    confirm(crm, req["id"], items["id"])
    # Like the supplier's file: positions 7 and 21 have no row, so 38 rows for 40 positions.
    positions = [number for number in range(1, 41) if number not in (7, 21)]
    rows = [standard(supplier["internal_code"], number, f"SRD-{number}") for number in positions]
    batch = preview(crm, req["id"], list(QUOTE_COLUMNS.values()), rows, kind="quotes")
    assert batch["summary"]["total"] == 38 and batch["summary"]["errors"] == 0, [
        row["errors"] for row in batch["rows"] if row["errors"]]
    assert batch["summary"]["request_positions"] == 40
    assert batch["summary"]["missing_positions"] == [7, 21]
    assert confirm(crm, req["id"], batch["id"])["result"]["imported"] == 38
    with crm["sessions"]() as db:
        imported = db.query(QuoteItem).filter(QuoteItem.supplier_id == supplier["id"],
                                              QuoteItem.archived.is_(False)).count()
        assert imported == 38 + 1


def test_large_quote_file_imports_every_row(crm):  # noqa: F811
    req, _, _, supplier = structured(crm)
    rows = [quote_file(supplier["internal_code"], {
        "source_row": "", "article": f"BULK-{index:05}", "name": f"Стандарт {index}",
        "packing_value": "50", "packing_unit": "mg", "product_group": "Стандартные образцы",
    }) for index in range(2000)]
    started = time.perf_counter()
    batch = preview(crm, req["id"], list(QUOTE_COLUMNS.values()), rows, kind="quotes")
    assert batch["summary"]["total"] == 2000 and batch["summary"]["errors"] == 0, batch["rows"][0]
    assert confirm(crm, req["id"], batch["id"])["result"]["imported"] == 2000
    assert time.perf_counter() - started < 120
    all_quotes = crm["client"].get(f"/api/v1/requests/{req['id']}/quote-items?all=true").json()
    assert all_quotes["total"] == 2001
    with crm["sessions"]() as db:
        # The setup quote is a quote-only position as well.
        assert db.query(RequestItem).filter_by(request_id=req["id"], quote_only=True).count() == 2001


def plan_with_volume(crm, req, data, target="6"):
    with crm["sessions"]() as db:
        quote = db.get(QuoteItem, data["selections"][0]["quote_item_id"])
        group_id = db.get(RequestItem, quote.source_request_item_id).product_group_id
    plan = wave_row(crm, req["wave_id"])
    plan = cmd(crm, f"/waves/{plan['id']}/forecasts", {
        "version": plan["version"], "product_group_id": group_id, "target_quantity": target,
        "unit_price_rub": "50000", "weight_per_unit": "0", "reason": "Плановый объём"}, method="put")
    return cmd(crm, f"/waves/{plan['id']}/budget", {
        "version": plan["version"], "profile_id": data["profile_id"], "reason": "Бюджет",
        "expenses": [{"name": "Логистика", "amount": "6000", "currency": "RUB", "scope": "WAVE",
                      "stage": "INTERNATIONAL_LOGISTICS", "method": "BY_QUANTITY"}]}, method="put")


def test_the_request_quantity_by_default_and_the_planned_volume_shares_the_costs(crm):  # noqa: F811
    """Business remark of 21 October 2026 (it replaces one piece by default of 15 October): the demand of the
    request position is priced unless another quantity is entered; the planned volume still shares the costs."""
    req, data, _, _ = structured(crm, mode="IMPORT", wave_kind="plan")
    with crm["sessions"].begin() as db:
        quote = db.get(QuoteItem, data["selections"][0]["quote_item_id"])
        quote.quantity = Decimal("40")
        db.get(RequestItem, quote.source_request_item_id).quantity = Decimal("4")
    plan_with_volume(crm, req, data)
    data.pop("expenses")
    data["selections"][0].pop("quantity", None)
    snapshot = cmd(crm, f"/requests/{req['id']}/calculations/preview", data)["snapshot"]
    assert snapshot["lines"][0]["quantity"] == "4"
    distribution = snapshot["wave_distribution"]
    assert (Decimal(distribution["selected_quantity"]), Decimal(distribution["forecast_quantity"]),
            Decimal(distribution["total_quantity"])) == (4, 2, 6)
    assert Decimal(snapshot["totals"]["international_logistics"]) == 4000
    one = cmd(crm, f"/requests/{req['id']}/calculations/preview",
              {**data, "selections": [{**data["selections"][0], "quantity": "1"}]})["snapshot"]
    assert (one["lines"][0]["quantity"], Decimal(one["wave_distribution"]["forecast_quantity"])) == ("1", 5)
    quoted = crm["client"].get(f"/api/v1/requests/{req['id']}/quote-items?all=true").json()["items"]
    assert [row["request_packings"] for row in quoted] == ["4"]


def test_leader_loads_all_wave_costs_into_one_calculation(crm):  # noqa: F811
    req, data, _, _ = structured(crm, mode="IMPORT", wave_kind="plan")
    plan_with_volume(crm, req, data)
    data.pop("expenses")
    undivided = cmd(crm, f"/requests/{req['id']}/calculations", {**data, "divide_wave_expenses": False})
    snapshot = undivided["snapshot"]
    assert snapshot["wave"]["id"] == req["wave_id"] and snapshot["wave"]["divided"] is False
    assert Decimal(snapshot["wave_distribution"]["total_quantity"]) == 1
    assert Decimal(snapshot["wave_distribution"]["forecast_quantity"]) == 0
    assert Decimal(snapshot["totals"]["international_logistics"]) == 6000
    divided = cmd(crm, f"/requests/{req['id']}/calculations/preview", data)["snapshot"]
    assert divided["wave"]["divided"] is True
    assert Decimal(divided["totals"]["international_logistics"]) == 1000
    # A sales manager calculates, but only a leader keeps the costs out of the wave split.
    with crm["sessions"].begin() as db:
        db.get(Request, req["id"]).owner_id = crm["manager"].id
        db.add_all(PermissionGrant(user_id=crm["manager"].id, code=code, scope="own") for code in (
            "calculations.write", "finance.purchase.read", "finance.calculations.read"))
    login(crm, "manager@example.com")
    latest = crm["client"].get(f"/api/v1/requests/{req['id']}").json()
    denied = cmd(crm, f"/requests/{req['id']}/calculations/preview",
                 {**data, "request_version": latest["version"], "divide_wave_expenses": False}, status=403)
    assert denied["code"] == "FORBIDDEN"


def test_total_delivery_term_is_entered_by_hand_and_calculated_positions_are_marked(crm):  # noqa: F811
    req, data, _, _ = structured(crm)
    data.pop("delivery_days")
    preview_snapshot = cmd(crm, f"/requests/{req['id']}/calculations/preview", data)["snapshot"]
    assert preview_snapshot["delivery_days"] is None
    missing = cmd(crm, f"/requests/{req['id']}/calculations", data, status=422)
    assert (missing["code"], missing["field"]) == ("DELIVERY_DAYS_REQUIRED", "delivery_days")
    quote_id = data["selections"][0]["quote_item_id"]
    before = crm["client"].get(f"/api/v1/requests/{req['id']}/quote-items?all=true").json()["items"]
    assert [row["last_calculation"] for row in before] == [None]
    saved = cmd(crm, f"/requests/{req['id']}/calculations", {**data, "delivery_days": 45})
    assert saved["snapshot"]["delivery_days"] == 45
    assert saved["snapshot"]["lines"][0]["delivery_days"] == 7
    [row] = crm["client"].get(f"/api/v1/requests/{req['id']}/quote-items?all=true").json()["items"]
    assert row["id"] == quote_id
    assert row["last_calculation"]["id"] == saved["id"]
    assert row["last_calculation"]["number"] == saved["snapshot"]["calculation_number"]


def test_a_sales_manager_sees_only_the_sales_by_managers_report(crm):  # noqa: F811
    with crm["sessions"].begin() as db:
        db.add_all(PermissionGrant(user_id=crm["manager"].id, code=code, scope="own")
                   for code in ("analytics.read", "finance.profit.read", "exports.download"))
    login(crm, "manager@example.com")
    period = "date_from=2026-01-01&date_to=2026-12-31"
    sales = crm["client"].get("/api/v1/analytics/sales-managers?from_date=2026-01-01&to_date=2026-12-31")
    assert sales.status_code == 200, sales.text
    for path in (f"/analytics/manufacturers?{period}", f"/analytics/suppliers?{period}",
                 "/analytics/invoices", f"/analytics/export.xlsx?{period}"):
        response = crm["client"].get("/api/v1" + path)
        assert response.status_code == 403, (path, response.text)
    login(crm)
    assert crm["client"].get(f"/api/v1/analytics/manufacturers?{period}").status_code == 200


def test_tasks_filter_by_due_assignee_and_client_and_group(crm):  # noqa: F811
    from datetime import datetime, timedelta, timezone

    from tests.support.crm import post

    login(crm)
    client = post(crm, "/counterparties", {"name": "ООО ТСМЕД"})
    request_ = post(crm, "/requests", {"title": "Заявка ТСМЕД", "client_id": client["id"]})
    now = datetime.now(timezone.utc)
    created = {}
    for title, assignee, due, link in (
        ("Обзвонить клиента", crm["admin"].id, now - timedelta(days=2), ("counterparty", client["id"])),
        ("Согласовать состав", crm["admin"].id, now + timedelta(days=3), ("request", request_["id"])),
        ("Запрос квот", crm["manager"].id, now + timedelta(days=30), None),
    ):
        body = {"title": title, "assignee_id": assignee, "due_at": due.isoformat(), "priority": "normal"}
        if link:
            body.update(entity_type=link[0], entity_id=link[1])
        created[title] = post(crm, "/tasks", body)

    def listed(query):
        response = crm["client"].get("/api/v1/tasks?page_size=100&" + query)
        assert response.status_code == 200, response.text
        return response.json()

    assert [row["title"] for row in listed("due=overdue")["items"]] == ["Обзвонить клиента"]
    assert [row["title"] for row in listed(f"assignee_id={crm['admin'].id}")["items"]] == [
        "Обзвонить клиента", "Согласовать состав"]
    by_client = listed(f"client_id={client['id']}")["items"]
    assert {row["title"] for row in by_client} == {"Обзвонить клиента", "Согласовать состав"}
    assert {row["client_name"] for row in by_client} == {"ООО ТСМЕД"}
    assert next(row for row in by_client if row["entity_type"] == "request")["request_number"] == request_["number"]
    grouped = listed("group=assignee")
    assert sorted((group["label"], group["count"]) for group in grouped["groups"]) == [
        ("Владелец теста", 2), ("Менеджер теста", 1)]
    assert [row["group"] for row in grouped["items"]] == [group["key"] for group in grouped["groups"]
                                                          for _ in range(group["count"])]
    by_due = listed("group=due")
    assert [group["key"] for group in by_due["groups"]] == ["overdue", "week", "later"]
    assert by_due["items"][0]["due_group"] == "overdue"
    assert crm["client"].get("/api/v1/tasks?group=unknown").status_code == 422
