"""Database role passwords are mandatory, never defaults, and are quoted safely in DDL."""

from uuid import uuid4

import psycopg
import pytest
from sqlalchemy import create_engine, make_url, text

from app.core import init_roles as roles
from app.core.config import settings

TRICKY = "it's:a;'' --$$ pass\\word"


@pytest.mark.parametrize("value", ["", "crm_api_password", "crm_backup_password", "short-secret",
                                   "replace-with-an-independent-random-api-password"])
def test_missing_default_or_weak_passwords_are_rejected(monkeypatch, value):
    monkeypatch.setenv("CRM_API_PASSWORD", value)
    monkeypatch.setattr(settings, "crm_api_password", "")
    with pytest.raises(SystemExit, match="CRM_API_PASSWORD"):
        roles.role_password("CRM_API_PASSWORD")


def test_independent_password_is_accepted(monkeypatch):
    monkeypatch.setenv("CRM_BACKUP_PASSWORD", TRICKY)
    assert roles.role_password("CRM_BACKUP_PASSWORD") == TRICKY


def test_role_setup_is_skipped_outside_postgresql(capsys):
    roles.init_roles("sqlite://")
    assert "Skipping role init" in capsys.readouterr().out


@pytest.mark.postgres
def test_passwords_with_quotes_create_working_restricted_roles(monkeypatch):
    if not settings.database_url.startswith("postgresql"):
        pytest.skip("Requires PostgreSQL database")
    owner = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    with owner.connect() as connection:
        if not connection.scalar(text("SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user")):
            pytest.skip("The test database user cannot create roles")
    suffix = uuid4().hex[:8]
    names = {"crm_api": f"test_api_{suffix}", "crm_backup": f"test_backup_{suffix}"}
    monkeypatch.setenv("CRM_API_PASSWORD", TRICKY + "-api")
    monkeypatch.setenv("CRM_BACKUP_PASSWORD", TRICKY + "-backup")
    url = make_url(settings.database_url)

    def connect(role, password):
        return psycopg.connect(host=url.host, port=url.port, dbname=url.database, user=role, password=password)

    try:
        roles.init_roles(names=names)
        roles.init_roles(names=names)  # idempotent for an existing cluster
        with connect(names["crm_api"], TRICKY + "-api") as api, api.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM users")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cursor.execute("DROP TABLE users CASCADE")
        with connect(names["crm_backup"], TRICKY + "-backup") as backup, backup.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM users")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cursor.execute("INSERT INTO users (id) VALUES ('denied')")
    finally:
        with owner.connect() as connection:
            for name in names.values():
                if connection.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": name}):
                    connection.execute(text(f'DROP OWNED BY "{name}"'))
                    connection.execute(text(f'DROP ROLE "{name}"'))
        owner.dispose()
