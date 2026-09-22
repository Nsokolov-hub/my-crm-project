import os
from uuid import uuid4

import pytest
from alembic import command as migrations
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.schema import CreateSchema

from app.core.config import settings
from app.core.db import SessionLocal


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
            
            from app.core.models import User, AuditEvent
            from sqlalchemy.orm import Session
            
            with Session(conn) as session:
                user = User(id='user1', email='u@test.local', name='U', password_hash='hash')
                session.add(user)
                session.flush()
                
                audit = AuditEvent(id='audit1', entity_type='user', entity_id='user1', action='create', actor_id='user1', request_id='req1')
                session.add(audit)
                
                session.commit()
                
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
