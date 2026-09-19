"""Administrative queue metadata and audited, explicit recovery commands."""
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.communication.models import FileRecord
from app.communication.worker import safe_job_error
from app.core.db import get_db
from app.core.errors import DomainError
from app.core.models import OutboxEvent, User
from app.core.security import current_user, require_permission
from app.core.service import audit, check_version, idem, lock, plain
from app.crm.models import ImportBatch

router = APIRouter(tags=['Фоновые операции'])
JOB_FIELDS = ('id', 'kind', 'status', 'attempts', 'progress', 'version', 'created_at',
              'started_at', 'finished_at', 'error')


def job_view(row: Any) -> dict[str, Any]:
    # Never serialize event_key or payload: both can contain private business data.
    result = {key: plain(getattr(row, key)) for key in JOB_FIELDS}
    result['error'] = safe_job_error(result['error']) if result['error'] is not None else None
    return result


class RetryInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    version: int = Field(ge=1)
    reason: str | None = Field(default=None, min_length=1, max_length=2000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


@router.get('/admin/jobs')
def jobs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    q: str = Query(default='', max_length=100),
    status: Literal['pending', 'running', 'succeeded', 'failed'] | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    require_permission(db, user, 'admin.settings')
    statement = select(*(getattr(OutboxEvent, key) for key in JOB_FIELDS))
    if status:
        statement = statement.where(OutboxEvent.status == status)
    if q:
        statement = statement.where(or_(OutboxEvent.kind.ilike(f'%{q}%'), OutboxEvent.status.ilike(f'%{q}%')))
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = db.execute(statement.order_by(OutboxEvent.created_at.desc(), OutboxEvent.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {'items': [job_view(row) for row in rows], 'total': total, 'page': page, 'page_size': page_size}


def restore_subject(db: Session, event: OutboxEvent, user: User, reason: str | None) -> None:
    if event.kind == 'import.confirm':
        batch = lock(db, ImportBatch, event.payload.get('batch_id'))
        if batch.status not in ('failed', 'queued'):
            raise DomainError('JOB_SUBJECT_STATE_CONFLICT', 'Состояние импорта не допускает повторный запуск', 409)
        before = {'status': batch.status, 'version': batch.version}
        batch.status = 'queued'
        batch.summary = {key: value for key, value in batch.summary.items() if key != 'error'}
        batch.version += 1
        audit(db, user, 'import', batch.id, 'retry_queued', before,
              {'status': batch.status, 'version': batch.version}, reason)
    elif event.kind == 'file.scan':
        file = lock(db, FileRecord, event.payload.get('file_id'))
        if file.status not in ('scan_failed', 'quarantined'):
            raise DomainError('JOB_SUBJECT_STATE_CONFLICT', 'Файл уже имеет результат проверки', 409)
        before = {'status': file.status, 'version': file.version}
        file.status = 'quarantined'
        file.scan_result = None
        file.scanned_at = None
        file.version += 1
        audit(db, user, 'file', file.id, 'scan_retry_queued', before,
              {'status': file.status, 'version': file.version}, reason)


@router.post('/admin/jobs/{entity_id}/retry')
def retry_job(
    entity_id: str,
    body: RetryInput,
    idempotency_key: str | None = Header(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    require_permission(db, user, 'admin.settings')
    if idempotency_key and body.idempotency_key and idempotency_key != body.idempotency_key:
        raise DomainError('IDEMPOTENCY_CONFLICT', 'Ключи повторяемости в заголовке и теле различаются', 409)

    def operation() -> dict[str, Any]:
        event = lock(db, OutboxEvent, entity_id)
        check_version(event, body.version)
        if event.status != 'failed':
            raise DomainError('JOB_RETRY_NOT_ALLOWED', 'Повторный запуск доступен только для завершённых с ошибкой операций', 409)
        before = job_view(event)
        restore_subject(db, event, user, body.reason)
        event.status = 'pending'
        event.attempts = 0
        event.progress = 0
        event.started_at = None
        event.finished_at = None
        event.error = None
        event.version += 1
        result = job_view(event)
        audit(db, user, 'job', event.id, 'retry_queued', before, result, body.reason)
        return result

    return idem(db, user, idempotency_key or body.idempotency_key, f'jobs.retry:{entity_id}',
                body.model_dump(exclude={'idempotency_key'}), operation)
