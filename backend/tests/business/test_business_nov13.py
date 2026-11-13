"""Business remarks of 13 November 2026: the decision window shows each position's supplier, the term of its quote and
its profitability, and the expenses of the calculation; a wave keeps the history of its changes with their comments;
rows of a checked Excel file are removed before they get into the request."""

from decimal import Decimal

from tests.business.test_business_nov05 import approval_of
from tests.business.test_business_oct28 import planned
from tests.support.business import cmd, confirm, preview, request, wave_row
from tests.support.crm import login, post


def test_the_decision_window_shows_supplier_term_profitability_and_expenses(crm):  # noqa: F811
    req, data, _, supplier, _, _, _ = planned(crm)
    execution, approval = approval_of(crm, req, data)

    offered = crm["client"].get(f"/api/v1/approvals/{approval['id']}/waves").json()
    [position] = offered["positions"]
    assert position["execution_id"] == execution["id"]
    assert (position["supplier_name"], position["delivery_days"], position["quantity"]) == (supplier["name"], 7, "2")
    assert position["profitability_percent"] is not None
    [expense] = offered["expenses"]
    assert (expense["name"], expense["method"]) == ("Общая логистика", "BY_QUANTITY")
    # The calculation divides 1 000 ₽ by the planned volume: these 2 pieces carry their share, not all of it.
    assert Decimal("0") < Decimal(expense["share"]) < Decimal(expense["amount"])
    # The wave choice is still there.
    assert offered["required"] is True and offered["lines"][0]["execution_id"] == execution["id"]


def test_the_history_of_a_wave_lists_its_changes_with_comments(crm):  # noqa: F811
    req, data, _, supplier, plan, _, _ = planned(crm)
    plan = wave_row(crm, plan["id"])
    cmd(crm, f"/waves/{plan['id']}", {"version": plan["version"], "number": "Индия авиа 47",
                                      "reason": "Поставщик сменил маршрут"}, method="patch")

    history = crm["client"].get(f"/api/v1/waves/{plan['id']}/history").json()["items"]
    titles = [row["title"] for row in history]
    assert "Волна изменена" in titles and "Добавлен плановый объём" in titles and "Изменён бюджет волны" in titles
    [change] = [row for row in history if row["title"] == "Волна изменена"]
    assert change["comment"] == "Поставщик сменил маршрут"
    assert [(row["label"], row["before"], row["after"]) for row in change["changes"]] == [
        ("Название", plan["number"], "Индия авиа 47")]
    assert change["actor_name"] == crm["admin"].name
    [forecast] = [row for row in history if row["title"] == "Добавлен плановый объём"]
    assert forecast["comment"] == "План запуска" and forecast["details"].endswith("10 шт.")
    # Money of the budget and of the planned volume is not repeated in the history.
    assert "1000" not in str(history)
    assert crm["client"].get("/api/v1/waves/00000000-0000-0000-0000-000000000000/history").status_code == 404


def test_rows_of_a_checked_file_are_removed_before_writing(crm):
    login(crm)
    req = request(crm)
    columns = ["Наименование", "Количество"]
    rows = [["Ацетонитрил", "1"], ["Лишняя строка", "2"], ["Метанол", "3"]]
    batch = preview(crm, req["id"], columns, rows)
    assert [row["row_number"] for row in batch["rows"]] == [2, 3, 4]

    path = f"/requests/{req['id']}/table-imports/{batch['id']}/remove-rows"
    trimmed = post(crm, path, {"row_numbers": [3]}, 200)
    # The rows keep their numbers in the file; the check counts the rest only.
    assert [row["row_number"] for row in trimmed["rows"]] == [2, 4]
    assert trimmed["summary"]["total"] == 2
    post(crm, path, {"row_numbers": [3]}, 422)
    post(crm, path, {"row_numbers": [2, 4]}, 422)

    assert confirm(crm, req["id"], batch["id"])["result"]["imported"] == 2
    items = crm["client"].get(f"/api/v1/requests/{req['id']}/items").json()["items"]
    assert sorted(item["description"] for item in items) == ["Ацетонитрил", "Метанол"]
    post(crm, path, {"row_numbers": [2]}, 409)
