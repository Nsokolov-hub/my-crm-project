"""The real application with HTTP sign-in on a test database: an owner and an own-scope manager."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.db import get_db
from app.core.models import PermissionGrant, User
from app.core.security import PERMISSIONS, password_hasher
from app.main import app

PASSWORD = "Only-for-automated-tests-1234"


@pytest.fixture
def crm(tmp_path, monkeypatch, database_with_foreign_keys):
    sessions = sessionmaker(database_with_foreign_keys, expire_on_commit=False)
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "files")
    monkeypatch.setattr(settings, "require_mfa", False)
    with sessions.begin() as db:
        admin = User(
            email="owner@example.com", name="Владелец теста", password_hash=password_hasher.hash(PASSWORD)
        )
        manager = User(email="manager@example.com", name="Менеджер теста", password_hash=admin.password_hash)
        db.add_all([admin, manager])
        db.flush()
        db.add_all(PermissionGrant(user_id=admin.id, code=code, scope="all") for code in PERMISSIONS)
        db.add_all(
            PermissionGrant(user_id=manager.id, code=code, scope="own")
            for code in (
                "clients.read",
                "clients.write",
                "requests.read",
                "requests.write",
                "calls.write",
                "tasks.read",
                "tasks.write",
            )
        )

    def get_session():
        with sessions() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    app.dependency_overrides[get_db] = get_session
    try:
        with TestClient(app) as client:
            res1 = client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
            assert res1.status_code == 200, res1.text
            client.headers["X-CSRF-Token"] = res1.json()["csrf_token"]
            res2 = client.post("/api/v1/setup/wizard", json={"company_name": "Test Company"})
            assert res2.status_code == 200, res2.text
            client.headers.pop("X-CSRF-Token", None)
            client.cookies.clear()
            yield {"client": client, "sessions": sessions, "admin": admin, "manager": manager}
    finally:
        app.dependency_overrides.pop(get_db, None)


def login(env, email="owner@example.com", **extra):
    response = env["client"].post("/api/v1/auth/login", json={"email": email, "password": PASSWORD, **extra})
    assert response.status_code == 200, response.text
    env["client"].headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response.json()


def post(env, path, data, status=201, key=None):
    response = env["client"].post(
        "/api/v1" + path, json=data, headers={"Idempotency-Key": key or str(uuid4())}
    )
    assert response.status_code == status, response.text
    return response.json()
