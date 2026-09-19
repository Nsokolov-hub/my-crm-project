"""Queue recovery, permission boundaries, and transactional handler failure checks."""
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.commerce.models  # noqa: F401
from app.communication import worker
from app.communication.files import store_quarantine
from app.communication.models import FileRecord
from app.core.config import settings
from app.core.db import Base, get_db, utcnow
from app.core.errors import DomainError
from app.core.models import (
    AuditEvent,
    AuthSession,
    IdempotencyRecord,
    Notification,
    OutboxEvent,
    PermissionGrant,
    Role,
    User,
    UserRole,
)
from app.core.routes import router
from app.core.security import digest
from app.crm.models import Contact, Counterparty, ImportBatch, ImportRow


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)

    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _record):
        connection.execute('PRAGMA foreign_keys=ON')

    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(settings, 'storage_dir', tmp_path / 'files')
    monkeypatch.setattr(settings, 'require_mfa', False)
    state = {'sessions': sessions}
    with sessions.begin() as db:
        for name in ('admin', 'author', 'role_admin', 'denied', 'inactive'):
            user = User(email=f'{name}@example.test', name=name, password_hash='fixture', active=name != 'inactive')
            db.add(user)
            db.flush()
            state[name] = user.id
            db.add(AuthSession(user_id=user.id, token_hash=digest(f'test-{name}'), csrf_token='test-csrf',
                               expires_at=utcnow() + timedelta(hours=1)))
        db.add(PermissionGrant(user_id=state['admin'], code='admin.settings', scope='all'))
        for code in ('imports.write', 'clients.write'):
            db.add(PermissionGrant(user_id=state['author'], code=code, scope='own'))
        role = Role(name='Настройки')
        db.add(role)
        db.flush()
        db.add(PermissionGrant(role_id=role.id, code='admin.settings', scope='all'))
        for name in ('role_admin', 'denied', 'inactive'):
            db.add(UserRole(role_id=role.id, user_id=state[name]))
        db.add(PermissionGrant(user_id=state['denied'], code='admin.settings', allow=False))
        client = Counterparty(name='Private customer', owner_id=state['admin'])
        db.add(client)
        db.flush()
        state['client_id'] = client.id

    app = FastAPI()
    app.include_router(router, prefix='/api/v1')

    @app.exception_handler(DomainError)
    async def domain_error(_request, exc):
        return JSONResponse({'code': exc.code, 'message': exc.message}, status_code=exc.status)

    def get_session():
        with sessions.begin() as db:
            yield db

    app.dependency_overrides[get_db] = get_session
    with TestClient(app) as client:
        client.cookies.set('crm_session', 'test-admin')
        client.headers['X-CSRF-Token'] = 'test-csrf'
        state['client'] = client
        yield state
    engine.dispose()


def add_job(env, **values):
    with env['sessions'].begin() as db:
        row = OutboxEvent(event_key=f'private-event:{uuid4()}', kind='notification',
                          payload={'user_id': env['author'], 'title': 'Private price 12345',
                                   'entity_type': 'task', 'entity_id': str(uuid4())})
        for key, value in values.items():
            setattr(row, key, value)
        db.add(row)
        db.flush()
        return row.id


def retry(env, job_id, version, *, key=None, expected=200, **body):
    response = env['client'].post(f'/api/v1/admin/jobs/{job_id}/retry',
                                  json={'version': version, **body},
                                  headers={'Idempotency-Key': key or str(uuid4())})
    assert response.status_code == expected, response.text
    return response.json()


def run(env, now=None):
    with env['sessions'].begin() as db:
        return worker.process_events(db, now=now)


def test_metadata_permissions_pagination_and_no_payload(jobs):
    first = add_job(jobs, status='failed', error='Connection failed: /private/contract supplier@example.test')
    add_job(jobs, kind='file.scan', status='pending', payload={'file_id': 'private-id'})
    client = jobs['client']
    response = client.get('/api/v1/admin/jobs', params={'page_size': 1, 'status': 'failed'})
    assert response.status_code == 200
    data = response.json()
    assert data['total'] == 1 and data['page'] == 1 and data['page_size'] == 1
    assert data['items'][0]['id'] == first
    assert data['items'][0]['error'] == 'JOB_EXECUTION_FAILED'
    assert set(data['items'][0]) == {'id', 'kind', 'status', 'attempts', 'progress', 'version',
                                     'created_at', 'started_at', 'finished_at', 'error'}
    assert 'private' not in response.text.lower() and '12345' not in response.text
    assert client.get('/api/v1/admin/jobs', params={'q': 'file.scan'}).json()['total'] == 1
    assert client.get('/api/v1/admin/jobs?page_size=101').status_code == 422
    assert client.get('/api/v1/admin/jobs?page=0').status_code == 422
    for name, expected in [('author', 403), ('denied', 403), ('inactive', 401), ('role_admin', 200)]:
        client.cookies.set('crm_session', f'test-{name}')
        assert client.get('/api/v1/admin/jobs').status_code == expected
    client.cookies.clear()
    assert client.get('/api/v1/admin/jobs').status_code == 401


def test_retry_requires_permission_version_key_and_failed_state(jobs):
    job_id = add_job(jobs, status='failed', attempts=5, error='FORBIDDEN')
    for name in ('author', 'denied'):
        jobs['client'].cookies.set('crm_session', f'test-{name}')
        assert retry(jobs, job_id, 1, expected=403)['code'] == 'FORBIDDEN'
    jobs['client'].cookies.set('crm_session', 'test-admin')
    assert retry(jobs, job_id, 2, expected=409)['code'] == 'VERSION_CONFLICT'
    response = jobs['client'].post(f'/api/v1/admin/jobs/{job_id}/retry', json={'version': 1})
    assert response.status_code == 422 and response.json()['code'] == 'IDEMPOTENCY_KEY_REQUIRED'
    response = jobs['client'].post(f'/api/v1/admin/jobs/{job_id}/retry', json={'version': 1},
                                   headers={'X-CSRF-Token': 'wrong', 'Idempotency-Key': str(uuid4())})
    assert response.status_code == 403 and response.json()['code'] == 'CSRF_INVALID'
    for status in ('pending', 'running', 'succeeded'):
        other_id = add_job(jobs, status=status)
        assert retry(jobs, other_id, 1, expected=409)['code'] == 'JOB_RETRY_NOT_ALLOWED'
    with jobs['sessions']() as db:
        assert db.get(OutboxEvent, job_id).status == 'failed'
        assert db.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0


def test_retry_is_idempotent_audited_and_rechecks_permissions(jobs):
    job_id = add_job(jobs, status='failed', attempts=5, progress=10, error='FORBIDDEN',
                     started_at=utcnow(), finished_at=utcnow())
    key = str(uuid4())
    result = retry(jobs, job_id, 1, key=key, reason='Доступ восстановлен')
    assert result['status'] == 'pending' and result['version'] == 2
    assert result['attempts'] == 0 and result['progress'] == 0
    assert result['error'] is None and result['started_at'] is None and result['finished_at'] is None
    assert retry(jobs, job_id, 1, key=key, reason='Доступ восстановлен') == result
    assert retry(jobs, job_id, 2, key=key, expected=409)['code'] == 'IDEMPOTENCY_CONFLICT'
    assert retry(jobs, job_id, 1, expected=409)['code'] == 'VERSION_CONFLICT'
    with jobs['sessions'].begin() as db:
        audits = db.scalars(select(AuditEvent).where(AuditEvent.entity_id == job_id)).all()
        assert len(audits) == 1 and audits[0].action == 'retry_queued'
        assert audits[0].actor_id == jobs['admin'] and audits[0].reason == 'Доступ восстановлен'
        assert 'payload' not in audits[0].before and 'event_key' not in audits[0].after
        record = db.scalar(select(IdempotencyRecord))
        assert record.result == result
        db.add(PermissionGrant(user_id=jobs['admin'], code='admin.settings', allow=False))
    assert retry(jobs, job_id, 1, key=key, reason='Доступ восстановлен', expected=403)['code'] == 'FORBIDDEN'


def test_body_idempotency_key_supported_and_mismatch_rejected(jobs):
    job_id = add_job(jobs, status='failed')
    response = jobs['client'].post(f'/api/v1/admin/jobs/{job_id}/retry',
                                   json={'version': 1, 'idempotency_key': 'body-key'})
    assert response.status_code == 200
    assert retry(jobs, job_id, 1, idempotency_key='different', expected=409)['code'] == 'IDEMPOTENCY_CONFLICT'


def test_retry_limit_backoff_versions_and_failure_notifications(jobs, monkeypatch):
    job_id = add_job(jobs)
    calls = []

    def broken(db, event):
        calls.append(event.id)
        raise RuntimeError('secret supplier price 12345 at /private/contract')

    monkeypatch.setattr(worker, 'dispatch', broken)
    now = utcnow()
    assert run(jobs, now) == {'succeeded': 0, 'retried': 1, 'failed': 0}
    assert run(jobs, now + timedelta(seconds=1)) == {'succeeded': 0, 'retried': 0, 'failed': 0}
    for attempt in range(2, worker.MAX_ATTEMPTS + 1):
        now += timedelta(minutes=6)
        counts = run(jobs, now)
        assert counts['failed' if attempt == worker.MAX_ATTEMPTS else 'retried'] == 1
    assert len(calls) == worker.MAX_ATTEMPTS
    assert run(jobs, now + timedelta(days=1)) == {'succeeded': 0, 'retried': 0, 'failed': 0}
    with jobs['sessions']() as db:
        row = db.get(OutboxEvent, job_id)
        assert row.status == 'failed' and row.attempts == worker.MAX_ATTEMPTS
        assert row.version == 1 + worker.MAX_ATTEMPTS
        assert row.error == 'JOB_EXECUTION_FAILED'
        notifications = db.scalars(select(Notification)).all()
        assert {n.user_id for n in notifications} == {jobs['admin'], jobs['role_admin']}
        assert all(n.title == 'Фоновая операция требует внимания' for n in notifications)
    result = retry(jobs, job_id, 1 + worker.MAX_ATTEMPTS)
    for attempt in range(worker.MAX_ATTEMPTS):
        now += timedelta(minutes=6)
        run(jobs, now)
    with jobs['sessions']() as db:
        assert db.get(OutboxEvent, job_id).version == result['version'] + worker.MAX_ATTEMPTS
        assert db.scalar(select(func.count()).select_from(Notification)) == 4


def test_import_partial_writes_rollback_terminal_failure_and_recovery(jobs):
    with jobs['sessions'].begin() as db:
        batch = ImportBatch(author_id=jobs['author'], source_name='Private.xlsx', file_key='private/source',
                            file_hash='a' * 64, mapping={}, status='queued', summary={'create': 1, 'update': 1})
        db.add(batch)
        db.flush()
        first = ImportRow(batch_id=batch.id, row_number=2, action='create',
                          data={'name': 'New imported customer', 'contact': 'Private contact', 'email': 'secret@example.test'})
        second = ImportRow(batch_id=batch.id, row_number=3, action='update', match_id=jobs['client_id'],
                           data={'name': 'Should not update'})
        db.add_all([first, second])
        db.flush()
        batch_id, first_id, second_id = batch.id, first.id, second.id
    job_id = add_job(jobs, kind='import.confirm', attempts=worker.MAX_ATTEMPTS - 1,
                     payload={'batch_id': batch_id, 'user_id': jobs['author']})
    assert run(jobs)['failed'] == 1
    with jobs['sessions'].begin() as db:
        assert db.scalar(select(func.count()).select_from(Counterparty)) == 1
        assert db.scalar(select(func.count()).select_from(Contact)) == 0
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == 'imported')) == 0
        assert db.get(ImportRow, first_id).action == 'create'
        assert db.get(ImportRow, first_id).match_id is None
        batch = db.get(ImportBatch, batch_id)
        assert batch.status == 'failed' and batch.summary['error'] == 'NOT_FOUND'
        assert batch.version == 2
        # The cause is fixed before the administrator explicitly retries the job.
        db.get(ImportRow, second_id).action = 'skip'
    result = retry(jobs, job_id, 2)
    with jobs['sessions']() as db:
        batch = db.get(ImportBatch, batch_id)
        assert batch.status == 'queued' and batch.version == 3 and 'error' not in batch.summary
    assert run(jobs)['succeeded'] == 1
    assert run(jobs)['succeeded'] == 0
    with jobs['sessions']() as db:
        assert db.get(ImportBatch, batch_id).status == 'completed'
        assert db.get(ImportRow, first_id).action == 'created'
        assert db.scalar(select(func.count()).select_from(Counterparty)) == 2
        assert db.scalar(select(func.count()).select_from(Contact)) == 1
        assert db.get(OutboxEvent, job_id).version == result['version'] + 1


def test_file_failure_retry_restores_quarantine_and_scans(jobs, monkeypatch):
    content = b'Private attachment'
    key, checksum = store_quarantine(content)
    with jobs['sessions'].begin() as db:
        file = FileRecord(name='Private.txt', media_type='text/plain', size=len(content), sha256=checksum,
                          storage_key=key, author_id=jobs['author'], client_id=jobs['client_id'])
        db.add(file)
        db.flush()
        file_id = file.id
    job_id = add_job(jobs, kind='file.scan', attempts=worker.MAX_ATTEMPTS - 1, payload={'file_id': file_id})

    def unavailable(_content):
        raise ConnectionError('private-scanner-host')

    monkeypatch.setattr(worker, 'scan_content', unavailable)
    assert run(jobs)['failed'] == 1
    with jobs['sessions']() as db:
        file = db.get(FileRecord, file_id)
        assert file.status == 'scan_failed' and file.scan_result == 'scanner_unavailable'
    result = retry(jobs, job_id, 2)
    with jobs['sessions']() as db:
        file = db.get(FileRecord, file_id)
        assert file.status == 'quarantined' and file.scan_result is None and file.scanned_at is None
    monkeypatch.setattr(worker, 'scan_content', lambda data: data == content)
    assert run(jobs)['succeeded'] == 1
    with jobs['sessions']() as db:
        assert db.get(FileRecord, file_id).status == 'clean'
        assert db.get(OutboxEvent, job_id).version == result['version'] + 1
        verdicts = db.scalars(select(Notification).where(Notification.entity_type == 'file')).all()
        assert len(verdicts) == 1 and verdicts[0].user_id == jobs['author']


def test_retry_completed_subject_does_not_reopen_or_partially_reset(jobs):
    with jobs['sessions'].begin() as db:
        batch = ImportBatch(author_id=jobs['author'], source_name='Private.xlsx', file_key='private/source',
                            file_hash='a' * 64, mapping={}, status='completed')
        db.add(batch)
        db.flush()
        batch_id = batch.id
    job_id = add_job(jobs, kind='import.confirm', status='failed', attempts=5,
                     payload={'batch_id': batch_id, 'user_id': jobs['author']})
    assert retry(jobs, job_id, 1, expected=409)['code'] == 'JOB_SUBJECT_STATE_CONFLICT'
    with jobs['sessions']() as db:
        assert db.get(ImportBatch, batch_id).status == 'completed'
        assert db.get(OutboxEvent, job_id).status == 'failed'
        assert db.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0


def test_worker_changes_disappear_when_outer_transaction_rolls_back(jobs):
    job_id = add_job(jobs)
    with jobs['sessions']() as db:
        assert worker.process_events(db)['succeeded'] == 1
        db.rollback()
    with jobs['sessions']() as db:
        row = db.get(OutboxEvent, job_id)
        assert row.status == 'pending' and row.attempts == 0 and row.version == 1
        assert db.scalar(select(func.count()).select_from(Notification)) == 0
