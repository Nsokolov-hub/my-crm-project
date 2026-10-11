"""Commerce routes on real PostgreSQL: a fresh migrated schema per test and concurrent requests."""

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Lock
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from alembic import command as migrations
from app.commerce.routes import router
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import DomainError
from app.core.models import PermissionGrant, User
from app.core.security import PERMISSIONS, current_user
from app.crm.models import Counterparty, Request, RequestItem, Seller
from tests.support.paths import BACKEND_DIR


def insert_row(connection, model, **values):
    """Insert one row with the model's Python-side defaults, skipping columns the schema does not have yet."""
    table = sa.Table(model.__tablename__, sa.MetaData(), autoload_with=connection)
    row = {}
    for column in model.__table__.columns:
        if column.key in values:
            row[column.name] = values[column.key]
        elif column.default is not None and column.default.is_scalar:
            row[column.name] = column.default.arg
        elif column.default is not None and column.default.is_callable:
            row[column.name] = column.default.arg(None)
    connection.execute(table.insert().values({name: value for name, value in row.items() if name in table.c}))
    return row["id"]


@pytest.fixture
def postgres_commerce(tmp_path, monkeypatch):
    url = make_url(os.environ.get("POSTGRES_TEST_DATABASE_URL", settings.database_url))
    if url.get_backend_name() != "postgresql":
        pytest.skip("The postgres acceptance gate requires a PostgreSQL database URL")
    schema = f"test_commerce_{uuid4().hex}"
    admin = create_engine(url, connect_args={"connect_timeout": 5}, hide_parameters=True)
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
    except OperationalError:
        admin.dispose()
        pytest.fail(
            "PostgreSQL is unavailable. Start the local database or set POSTGRES_TEST_DATABASE_URL.",
            pytrace=False,
        )

    engine = create_engine(
        url,
        connect_args={
            "connect_timeout": 5,
            "options": f"-csearch_path={schema} -cstatement_timeout=20000 -clock_timeout=10000",
        },
        pool_size=8,
        max_overflow=0,
        hide_parameters=True,
    )
    try:
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT current_schema()")) == schema
            config = Config(str(BACKEND_DIR / "alembic.ini"))
            config.attributes["connection"] = connection
            migrations.upgrade(config, "head")
        sessions = sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(settings, "storage_dir", tmp_path / "files")
        with engine.begin() as connection:
            # Core inserts into the reflected schema: works at whatever revision a test migrated to.
            owner_id = insert_row(connection, User, email="owner@example.test", name="Владелец",
                                  password_hash="fixture-only")
            for code in PERMISSIONS:
                insert_row(connection, PermissionGrant, user_id=owner_id, code=code, scope="all")
            seller_id = insert_row(connection, Seller, name="Тестовый продавец", currency="RUB",
                                   details={"tax_id": "TEST"})
            client_id = insert_row(connection, Counterparty, name="Клиент", kind="client", owner_id=owner_id,
                                   internal_code=1)
            supplier_id = insert_row(connection, Counterparty, name="Поставщик", kind="supplier",
                                     owner_id=owner_id, internal_code=2)
            request_id = insert_row(connection, Request, number="TEST-PG-001", title="Конкурентная тестовая заявка",
                                    seller_id=seller_id, client_id=client_id, owner_id=owner_id, is_test=True)
            item_id = insert_row(connection, RequestItem, request_id=request_id, description="Этанол для теста",
                                 cas="64-17-5", quantity=Decimal("10"), unit="pcs", purity="99%",
                                 packaging="1 штука", allow_analogue=False)
        with sessions() as db:
            owner = db.get(User, owner_id)
        state = {
            "owner_id": owner_id,
            "seller_id": seller_id,
            "client_id": client_id,
            "supplier_id": supplier_id,
            "request_id": request_id,
            "item_id": item_id,
            "sessions": sessions,
            "barrier": None,
            "backend_pids": [],
        }

        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        pid_lock = Lock()

        @app.exception_handler(DomainError)
        async def domain_error(_request, exc):
            return JSONResponse(
                {"code": exc.code, "message": exc.message, "field": exc.field},
                status_code=exc.status,
            )

        def get_session():
            with sessions() as db:
                try:
                    if state["barrier"] is not None:
                        # Reserve independent connections and begin every transaction before
                        # releasing any route. No service locks or queries are mocked.
                        pid, active_schema = db.execute(
                            text("SELECT pg_backend_pid(), current_schema()")
                        ).one()
                        assert active_schema == schema
                        with pid_lock:
                            state["backend_pids"].append(pid)
                        state["barrier"].wait(timeout=10)
                    yield db
                    db.commit()
                except Exception:
                    db.rollback()
                    raise

        app.dependency_overrides[get_db] = get_session
        app.dependency_overrides[current_user] = lambda: owner
        state["app"] = app
        with TestClient(app) as api:
            state["client"] = api
            yield state
    finally:
        engine.dispose()
        try:
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        finally:
            admin.dispose()


def concurrent_posts(env, requests):
    """Perform simultaneous API calls and prove they used distinct PostgreSQL sessions."""
    env["barrier"] = Barrier(len(requests))
    env["backend_pids"] = []

    def post(request):
        path, payload = request
        with TestClient(env["app"]) as client:
            return client.post("/api/v1" + path, json=payload)

    try:
        with ThreadPoolExecutor(max_workers=len(requests)) as executor:
            futures = [executor.submit(post, request) for request in requests]
            responses = [future.result(timeout=30) for future in futures]
    finally:
        env["barrier"] = None
    assert len(env["backend_pids"]) == len(requests)
    assert len(set(env["backend_pids"])) == len(requests)
    return responses
