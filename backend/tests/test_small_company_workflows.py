"""Daily workflow checks with real authentication and an isolated database."""

from test_crm import PASSWORD, login, post
from test_crm import crm as crm

from app import bootstrap
from app.core.settings_registry import SETTING_META


def test_bootstrapped_admin_can_update_dictionary_and_assign_setting_permissions(crm, monkeypatch):
    monkeypatch.setattr(bootstrap, "SessionLocal", crm["sessions"])
    assert bootstrap.provision("new.admin@example.com", "Администратор", PASSWORD)
    session = login(crm, "new.admin@example.com")
    setting_permissions = {meta.permission for meta in SETTING_META.values()}
    assert setting_permissions <= session["permissions"].keys()
    permissions = crm["client"].get("/api/v1/admin/permissions").json()["items"]
    assert setting_permissions <= {item["code"] for item in permissions}

    settings = crm["client"].get("/api/v1/settings").json()["items"]
    current = next(setting for setting in settings if setting["key"] == "loss_reasons")
    saved = post(crm, "/settings", {
        "key": "loss_reasons", "version": current["version"],
        "value": {"reasons": ["no_budget", "Потребность отпала"]},
    }, 200)
    assert saved["value"]["reasons"] == ["no_budget", "Потребность отпала"]
    assert saved["version"] == current["version"] + 1
    role = post(crm, "/admin/roles", {
        "name": "Настройки для руководителя",
        "grants": [{"code": code, "scope": "all", "allow": True} for code in sorted(setting_permissions)],
    }, 200)
    assert role["name"] == "Настройки для руководителя"


def test_manager_selects_configured_loss_reason_and_closes_own_request(crm):
    login(crm, "manager@example.com")
    assert crm["client"].get("/api/v1/settings").status_code == 403
    options = crm["client"].get("/api/v1/dictionaries/loss_reasons")
    assert options.status_code == 200, options.text
    assert {"id": "no_budget", "name": "Нет бюджета"} in options.json()["items"]
    client = post(crm, "/counterparties", {"name": "Клиент без бюджета"})
    request = post(crm, "/requests", {"title": "Поставка", "client_id": client["id"]})
    saved = crm["client"].patch("/api/v1/requests/" + request["id"], json={
        "version": request["version"], "title": request["title"],
        "owner_id": crm["manager"].id, "commercial_stage": "closed_lost",
        "loss_reason": "no_budget", "reason": "Клиент сообщил об отмене закупки",
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["commercial_stage"] == "closed_lost"
    assert saved.json()["loss_reason"] == "no_budget"
    assert saved.json()["closed_at"]


def test_manager_call_form_uses_configured_results_and_cannot_read_other_settings(crm):
    login(crm, "manager@example.com")
    options = crm["client"].get("/api/v1/dictionaries/call_results")
    assert options.status_code == 200, options.text
    assert {"id": "meeting_scheduled", "name": "Назначена встреча"} in options.json()["items"]
    client = post(crm, "/counterparties", {"name": "Клиент для встречи"})
    saved = post(crm, "/calls", {"client_id": client["id"], "result": "meeting_scheduled"})
    assert saved["result"] == "meeting_scheduled"
    assert crm["client"].get("/api/v1/dictionaries/file_policy").status_code == 404
    login(crm, "owner@example.com")
    permissions = crm["client"].patch("/api/v1/admin/users/" + crm["manager"].id, json={
        "version": crm["manager"].version, "reason": "Ограничить просмотр результатов",
        "grants": [{"code": "calls.write", "scope": "own", "allow": False}],
    })
    assert permissions.status_code == 200, permissions.text
    login(crm, "manager@example.com")
    assert crm["client"].get("/api/v1/dictionaries/call_results").status_code == 403
