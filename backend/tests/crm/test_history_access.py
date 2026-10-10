"""Request history reveals change snapshots only with finance rights for that very request."""

from test_crm import PASSWORD, login, post
from test_crm import crm as crm  # noqa: F401

from app.core.models import PermissionGrant, User
from app.core.security import password_hasher

FINANCE = ("finance.purchase.read", "finance.calculations.read", "finance.reward.read")


def foreign_request_with_priced_item(env):
    login(env)
    currency = post(env, "/currencies", {"code": "EUR", "name": "Евро"})
    client = post(env, "/counterparties", {"name": "Клиент владельца"})
    request = post(env, "/requests", {"client_id": client["id"], "title": "Чужая заявка"})
    post(env, f"/requests/{request['id']}/items", {
        "description": "Реактив", "quantity": "2", "unit": "kg",
        "purchase_price": "123.45", "purchase_currency_id": currency["id"],
    })
    return request


def add_reviewer(env, scope):
    with env["sessions"].begin() as db:
        reviewer = User(email="reviewer@example.com", name="Проверяющий", password_hash=password_hasher.hash(PASSWORD))
        db.add(reviewer)
        db.flush()
        db.add(PermissionGrant(user_id=reviewer.id, code="requests.read", scope="all"))
        db.add_all(PermissionGrant(user_id=reviewer.id, code=code, scope=scope) for code in FINANCE)


def history(env, request_id):
    response = env["client"].get(f"/api/v1/requests/{request_id}/history")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_own_scope_finance_rights_do_not_open_snapshots_of_foreign_requests(crm):
    request = foreign_request_with_priced_item(crm)
    assert any("purchase_price" in (row.get("after") or {}) for row in history(crm, request["id"]))

    add_reviewer(crm, "own")
    login(crm, "reviewer@example.com")
    rows = history(crm, request["id"])
    assert rows
    assert all("before" not in row and "after" not in row for row in rows)


def test_finance_rights_for_all_requests_keep_snapshots_visible(crm):
    request = foreign_request_with_priced_item(crm)
    add_reviewer(crm, "all")
    login(crm, "reviewer@example.com")
    assert any("purchase_price" in (row.get("after") or {}) for row in history(crm, request["id"]))
