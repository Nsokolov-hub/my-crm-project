"""3 November 2026: an employee forgot the password. The CRM keeps only its hash, so an administrator sets a new one."""

import json

from sqlalchemy import func, select

from app.core.models import AuditEvent, AuthSession
from tests.support.crm import PASSWORD, login

NEW_PASSWORD = "Новый-пароль-сотрудника-2026"


def open_sessions(crm, user_id):
    with crm["sessions"]() as db:
        return db.scalar(
            select(func.count())
            .select_from(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked.is_(False))
        )


def test_an_administrator_sets_a_new_password_and_ends_the_employee_sessions(crm):
    client, admin, manager = crm["client"], crm["admin"], crm["manager"]
    login(crm, "manager@example.com")
    assert open_sessions(crm, manager.id) == 1

    # Only someone who manages users may do it.
    refused = client.post(f"/api/v1/admin/users/{admin.id}/password", json={"new_password": NEW_PASSWORD})
    assert (refused.status_code, refused.json()["code"]) == (403, "FORBIDDEN")

    client.cookies.clear()
    login(crm)
    short = client.post(f"/api/v1/admin/users/{manager.id}/password", json={"new_password": "short"})
    assert short.status_code == 422
    own = client.post(f"/api/v1/admin/users/{admin.id}/password", json={"new_password": NEW_PASSWORD})
    assert (own.status_code, own.json()["code"]) == (422, "SELF_PASSWORD")

    done = client.post(f"/api/v1/admin/users/{manager.id}/password", json={"new_password": NEW_PASSWORD})
    assert done.status_code == 200, done.text
    assert open_sessions(crm, manager.id) == 0
    with crm["sessions"]() as db:
        event = db.scalars(
            select(AuditEvent).where(AuditEvent.entity_id == manager.id, AuditEvent.action == "password_reset")
        ).one()
        assert event.actor_id == admin.id
        assert NEW_PASSWORD not in json.dumps([event.before, event.after, event.reason], ensure_ascii=False)

    client.cookies.clear()
    old = client.post("/api/v1/auth/login", json={"email": "manager@example.com", "password": PASSWORD})
    assert old.status_code == 401
    assert login(crm, "manager@example.com", password=NEW_PASSWORD)["user"]["email"] == "manager@example.com"
