"""The audit log scopes finance rights to the CRM request a record belongs to."""

from decimal import Decimal
from uuid import uuid4

from test_crm import PASSWORD, login, post
from test_crm import crm as crm  # noqa: F401

from app.core.models import PermissionGrant, User
from app.core.security import password_hasher

FINANCE = ("finance.purchase.read", "finance.calculations.read", "finance.reward.read", "finance.profit.read")


def priced_request(env, correlation="audit-trace-1"):
    login(env)
    currency = post(env, "/currencies", {"code": "EUR", "name": "Евро"})
    client = post(env, "/counterparties", {"name": "Клиент владельца"})
    request = post(env, "/requests", {"client_id": client["id"], "title": "Заявка владельца"})
    response = env["client"].post(
        f"/api/v1/requests/{request['id']}/items",
        json={"description": "Реактив", "quantity": "2", "unit": "kg",
              "purchase_price": "123.45", "purchase_currency_id": currency["id"]},
        headers={"Idempotency-Key": str(uuid4()), "X-Request-ID": correlation},
    )
    assert response.status_code == 201, response.text
    return request, response.json()


def audit_rows(env):
    response = env["client"].get("/api/v1/admin/audit", params={"page_size": 100})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def add_reviewer(env, scope):
    with env["sessions"].begin() as db:
        reviewer = User(email="auditor@example.com", name="Аудитор", password_hash=password_hasher.hash(PASSWORD))
        db.add(reviewer)
        db.flush()
        db.add_all(PermissionGrant(user_id=reviewer.id, code=code, scope="all") for code in ("audit.read", "requests.read"))
        db.add_all(PermissionGrant(user_id=reviewer.id, code=code, scope=scope) for code in FINANCE)


def test_owner_with_finance_rights_sees_request_item_snapshots(crm):
    _, item = priced_request(crm)
    row = next(r for r in audit_rows(crm) if r["entity_type"] == "request_item" and r["entity_id"] == item["id"])
    assert Decimal(row["after"]["purchase_price"]) == Decimal("123.45")


def test_audit_keeps_the_http_correlation_code_apart_from_requests(crm):
    request, item = priced_request(crm, correlation="trace-for-support")
    row = next(r for r in audit_rows(crm) if r["entity_type"] == "request_item" and r["entity_id"] == item["id"])
    assert row["correlation_id"] == "trace-for-support"
    assert "request_id" not in row
    assert row["entity_id"] != request["id"]


def test_own_scope_finance_rights_hide_snapshots_of_foreign_requests_only(crm):
    _, item = priced_request(crm)
    add_reviewer(crm, "own")
    login(crm, "auditor@example.com")
    rows = audit_rows(crm)
    item_row = next(r for r in rows if r["entity_type"] == "request_item" and r["entity_id"] == item["id"])
    assert "before" not in item_row and "after" not in item_row
    currency_row = next(r for r in rows if r["entity_type"] == "currency")
    assert currency_row["after"]["code"] == "EUR"
