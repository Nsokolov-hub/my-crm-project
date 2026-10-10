import os
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.schema import CreateSchema

from alembic import command as migrations
from app.core.config import settings

pytestmark = pytest.mark.postgres


@pytest.fixture
def postgres_history(monkeypatch):
    url = make_url(os.environ.get("POSTGRES_TEST_DATABASE_URL", settings.database_url))
    if url.get_backend_name() != "postgresql":
        pytest.skip("Requires PostgreSQL")
    
    schema = f"test_history_{uuid4().hex}"
    admin = create_engine(url, connect_args={"connect_timeout": 5}, hide_parameters=True)
    try:
        with admin.begin() as conn:
            conn.execute(CreateSchema(schema))
    except OperationalError:
        admin.dispose()
        pytest.fail("PostgreSQL unavailable")
        
    engine = create_engine(
        url,
        connect_args={"options": f"-csearch_path={schema}"},
        hide_parameters=True
    )
    
    try:
        with engine.begin() as conn:
            ini_path = 'alembic.ini' if os.path.exists('alembic.ini') else 'backend/alembic.ini'
            config = Config(ini_path)
            config.attributes['connection'] = conn
            
            # Step 1: Migrate up to initial_schema
            migrations.upgrade(config, '2abdec759d06')
            
            # Seed using the historical schema, independent of current ORM columns.
            conn.execute(text("INSERT INTO users (id,email,name,password_hash,active,mfa_enabled,mfa_last_step,created_at,version) VALUES ('user1','u@test.local','U','hash',true,false,-1,CURRENT_TIMESTAMP,1)"))
            conn.execute(text("INSERT INTO audit_events (id,entity_type,entity_id,action,actor_id,request_id,created_at,version) VALUES ('audit1','user','user1','create','user1','req1',CURRENT_TIMESTAMP,1)"))

            # Step 3: Upgrade to protect_history
            migrations.upgrade(config, '51eab019a784')
            
        yield engine
    finally:
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


def test_audit_events_immutable_delete(postgres_history):
    with postgres_history.begin() as conn:
        with pytest.raises((IntegrityError, OperationalError), match="CRM_RECORDED_FACT_IMMUTABLE"):
            conn.execute(text("DELETE FROM audit_events WHERE id = 'audit1'"))


def test_audit_events_immutable_update(postgres_history):
    with postgres_history.begin() as conn:
        with pytest.raises((IntegrityError, OperationalError), match="CRM_RECORDED_FACT_IMMUTABLE"):
            conn.execute(text("UPDATE audit_events SET action = 'other' WHERE id = 'audit1'"))


def test_audit_events_immutable_truncate(postgres_history):
    with postgres_history.begin() as conn:
        with pytest.raises((IntegrityError, OperationalError), match="CRM_RECORDED_FACT_IMMUTABLE"):
            conn.execute(text("TRUNCATE audit_events"))
