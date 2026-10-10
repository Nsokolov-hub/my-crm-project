"""Test database: in-memory SQLite by default, a throwaway PostgreSQL schema with TEST_DATABASE_URL."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.core.db import Base


@contextmanager
def database_engine(*, sqlite_foreign_keys: bool = False) -> Iterator[Engine]:
    """Yield an engine with every table created.

    Set TEST_DATABASE_URL to a PostgreSQL URL to run the regular suite against PostgreSQL: each
    test gets its own schema, so advisory locks, row locks and constraints behave as in production.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        if sqlite_foreign_keys:
            @event.listens_for(engine, "connect")
            def foreign_keys(connection, _record):
                connection.execute("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(engine)
        try:
            yield engine
        finally:
            engine.dispose()
        return

    schema = f"test_{uuid4().hex}"
    admin = create_engine(url, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    # Timeouts turn an accidental lock wait between a test session and the API into a failure.
    engine = create_engine(url, hide_parameters=True, connect_args={
        "options": f"-csearch_path={schema} -ctimezone=UTC -cstatement_timeout=20000 -clock_timeout=10000",
    })
    try:
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


@pytest.fixture
def database():
    with database_engine() as engine:
        yield engine


@pytest.fixture
def database_with_foreign_keys():
    with database_engine(sqlite_foreign_keys=True) as engine:
        yield engine
