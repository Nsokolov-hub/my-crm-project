"""The request transaction is committed before the client receives a success response."""

import logging

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from test_crm import crm as crm  # noqa: F401
from test_crm import login

from app.core.db import get_db
from app.core.security import current_user
from app.crm.models import Counterparty
from app.main import app


def _api_routes(routes):
    # FastAPI keeps included routers as lazy wrappers instead of flattening them into app.routes.
    for route in routes:
        if getattr(route, "dependant", None) is not None:
            yield route
        nested = getattr(getattr(route, "original_router", None), "routes", None) or getattr(route, "routes", None)
        if nested:
            yield from _api_routes(nested)


def _dependants(dependant):
    for child in dependant.dependencies:
        yield child
        yield from _dependants(child)


def test_every_route_commits_inside_the_endpoint_scope():
    """A request-scoped session would commit after the response and report lost writes as success."""
    routes = list(_api_routes(app.routes))
    sessions = [(route, child) for route in routes for child in _dependants(route.dependant) if child.call is get_db]
    assert len(routes) > 150 and len(sessions) > 150
    offenders = sorted(f"{','.join(sorted(route.methods or []))} {route.path}"
                       for route, child in sessions if child.scope != "function")
    assert offenders == []


def test_failed_commit_is_reported_and_nothing_is_saved(crm, monkeypatch, caplog):
    login(crm)
    caplog.set_level(logging.ERROR, logger="crm")
    original_commit = Session.commit
    failures = []

    def failing_commit(session):
        failures.append(session)
        raise OperationalError("COMMIT", {}, Exception("connection lost during commit"))

    monkeypatch.setattr(Session, "commit", failing_commit)
    response = crm["client"].post("/api/v1/counterparties", json={"name": "Не должен сохраниться"})
    monkeypatch.setattr(Session, "commit", original_commit)

    assert failures, "the endpoint transaction must be committed while the request is processed"
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "TRANSACTION_RETRY"
    logged = [r for r in caplog.records if "database_unavailable" in r.getMessage()]
    assert logged and logged[0].exc_info is not None
    with crm["sessions"]() as db:
        saved = db.scalar(select(func.count()).select_from(Counterparty)
                          .where(Counterparty.name == "Не должен сохраниться"))
    assert saved == 0


def test_unhandled_error_is_logged_with_traceback_and_hidden_from_client(crm, caplog):
    caplog.set_level(logging.ERROR, logger="crm")

    def broken_user():
        raise RuntimeError("secret internal detail")

    app.dependency_overrides[current_user] = broken_user
    try:
        response = crm["client"].get("/api/v1/auth/me", headers={"X-Request-ID": "trace-500"})
    finally:
        app.dependency_overrides.pop(current_user, None)
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR" and response.json()["requestId"] == "trace-500"
    assert "secret internal detail" not in response.text
    record = next(r for r in caplog.records if "unhandled_error" in r.getMessage())
    assert "trace-500" in record.getMessage() and "/api/v1/auth/me" in record.getMessage()
    assert record.exc_info and record.exc_info[0] is RuntimeError
