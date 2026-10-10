"""The request transaction is committed before the client receives a success response."""

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from test_crm import crm as crm  # noqa: F401
from test_crm import login

from app.core.db import get_db
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


def test_failed_commit_is_reported_and_nothing_is_saved(crm, monkeypatch):
    login(crm)
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
    with crm["sessions"]() as db:
        saved = db.scalar(select(func.count()).select_from(Counterparty)
                          .where(Counterparty.name == "Не должен сохраниться"))
    assert saved == 0
