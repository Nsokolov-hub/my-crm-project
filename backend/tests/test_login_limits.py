"""Sign-in throttling is per account and per client address, not shared by everyone behind a proxy."""

from sqlalchemy import event
from test_crm import PASSWORD
from test_crm import crm as crm  # noqa: F401

from app.core.config import settings
from app.core.models import LoginAttempt

TEST_CLIENT_IP = "testclient"


def failed_attempts(env, count, *, identity, ip=TEST_CLIENT_IP):
    with env["sessions"].begin() as db:
        db.add_all(LoginAttempt(identity=identity, ip=ip, successful=False) for _ in range(count))


def sign_in(env, email):
    return env["client"].post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})


def test_failures_of_one_account_do_not_block_colleagues_from_the_same_address(crm):
    failed_attempts(crm, settings.login_identity_attempts, identity="owner@example.com")

    blocked = sign_in(crm, "owner@example.com")
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "LOGIN_RATE_LIMIT"
    assert sign_in(crm, "manager@example.com").status_code == 200


def test_many_failures_from_one_address_block_that_address(crm):
    per_account = settings.login_identity_attempts - 1
    accounts = -(-settings.login_ip_attempts // per_account)
    for index in range(accounts):
        failed_attempts(crm, per_account, identity=f"guess-{index}@example.com")

    assert sign_in(crm, "manager@example.com").status_code == 429


def test_failures_from_other_addresses_do_not_count_against_this_one(crm):
    failed_attempts(crm, settings.login_ip_attempts, identity="someone@example.com", ip="203.0.113.7")

    assert sign_in(crm, "manager@example.com").status_code == 200


def test_authenticated_requests_skip_privilege_lookups_when_mfa_is_not_required(crm):
    login_response = sign_in(crm, "manager@example.com")
    crm["client"].headers["X-CSRF-Token"] = login_response.json()["csrf_token"]
    engine = crm["sessions"].kw["bind"]
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert crm["client"].get("/api/v1/sellers").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert statements and not any("permission_grants" in statement for statement in statements)
