"""Transactional outbox worker. Run: PYTHONPATH=backend python -m app.communication.worker.

The row lock remains held until side effects and completion commit together. A worker crash
rolls the transaction back; PostgreSQL SKIP LOCKED permits multiple worker processes.
"""
import argparse
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.communication.access import check_chat
from app.communication.files import scan_content, verified_content
from app.communication.models import FileRecord
from app.core.config import settings
from app.core.db import SessionLocal, utcnow
from app.core.errors import DomainError
from app.core.models import AppSetting, OutboxEvent, User
from app.core.security import can
from app.core.service import advisory, audit, notify
from app.crm.models import ImportBatch, Task

logger = logging.getLogger('crm.worker')
MAX_ATTEMPTS = 5


def safe_job_error(value: str | None) -> str:
    """Expose stable error codes, never exception text, paths, or legacy diagnostics."""
    return value if value and re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', value) else 'JOB_EXECUTION_FAILED'


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class ReminderPolicy(BaseModel):
    enabled: bool = True
    timezone: str = settings.company_timezone
    lead_working_days: int = Field(default=1, ge=0, le=30)
    lead_minutes: int | None = Field(default=None, ge=0, le=60 * 24 * 30)
    working_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4], min_length=1, max_length=7)
    holidays: list[date] = Field(default_factory=list, max_length=1000)

    @field_validator('working_days')
    @classmethod
    def weekdays(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value) or any(day < 0 or day > 6 for day in value):
            raise ValueError('Рабочие дни: уникальные числа от 0 (понедельник) до 6')
        return value

    @field_validator('timezone')
    @classmethod
    def valid_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError('Неизвестный часовой пояс IANA') from None
        return value


def reminder_at(due_at: datetime, policy: ReminderPolicy) -> datetime:
    if policy.lead_minutes is not None:
        return aware(due_at) - timedelta(minutes=policy.lead_minutes)
    local = aware(due_at).astimezone(ZoneInfo(policy.timezone))
    remaining = policy.lead_working_days
    while remaining:
        local -= timedelta(days=1)
        if local.weekday() in policy.working_days and local.date() not in policy.holidays:
            remaining -= 1
    return local.astimezone(timezone.utc)


def schedule_reminders(db: Session, now: datetime | None = None) -> int:
    now = aware(now or utcnow())
    setting = db.scalar(select(AppSetting).where(AppSetting.key == 'task_reminders'))
    try:
        policy = ReminderPolicy.model_validate(setting.value if setting else {})
    except ValidationError:
        raise DomainError('REMINDER_SETTINGS_INVALID', 'Проверьте настройки рабочего календаря и напоминаний', 422) from None
    if not policy.enabled:
        return 0
    created = 0
    # Streaming avoids loading the task register into memory, including overdue tasks.
    tasks = db.scalars(select(Task).where(Task.status.in_(['assigned', 'in_progress']))
                       .order_by(Task.due_at, Task.id).execution_options(yield_per=250))
    for task in tasks:
        due_at = aware(task.due_at)
        if due_at <= now:
            stage = 'overdue'
        elif reminder_at(due_at, policy) <= now:
            stage = 'approaching'
        else:
            continue
        stamp = due_at.astimezone(timezone.utc).isoformat()
        key = f'task-reminder:{task.id}:{task.assignee_id}:{stamp}:{stage}'
        advisory(db, key)
        if db.scalar(select(OutboxEvent.id).where(OutboxEvent.event_key == key)):
            continue
        db.add(OutboxEvent(event_key=key, kind='task.reminder', payload={
            'task_id': task.id, 'user_id': task.assignee_id, 'due_at': stamp, 'stage': stage}))
        db.flush()
        created += 1
    return created


def dispatch(db: Session, event: OutboxEvent) -> None:
    payload = event.payload
    if event.kind == 'file.scan':
        row = db.scalar(select(FileRecord).where(FileRecord.id == payload['file_id']).with_for_update())
        if row is None:
            raise DomainError('FILE_NOT_FOUND', 'Файл задания не найден', 404)
        if row.status in ('clean', 'infected'):
            return
        data = verified_content(row.storage_key, row.sha256, row.size)
        clean = scan_content(data)
        row.status = 'clean' if clean else 'infected'
        row.scan_result = 'clean' if clean else 'malware_detected'
        row.scanned_at = utcnow()
        row.version += 1
        audit(db, None, 'file', row.id, 'scan_completed', after={'status': row.status, 'sha256': row.sha256})
        notify(db, row.author_id, f'file-verdict:{row.id}',
               'Файл проверен и доступен' if clean else 'Вложение заблокировано антивирусом', 'file', row.id)
        return
    if event.kind == 'notification':
        user = db.get(User, payload['user_id'])
        if user is None or not user.active:
            return
        if payload['entity_type'] == 'chat':
            try:
                check_chat(db, user, payload['entity_id'])
            except DomainError as exc:
                if exc.status in (403, 404):
                    return
                raise
        notify(db, user.id, event.event_key, payload['title'], payload['entity_type'], payload['entity_id'])
        return
    if event.kind == 'task.reminder':
        task = db.get(Task, payload['task_id'])
        user = db.get(User, payload['user_id'])
        if (task is None or user is None or not user.active or task.assignee_id != user.id
                or task.status not in ('assigned', 'in_progress')
                or aware(task.due_at).astimezone(timezone.utc).isoformat() != payload['due_at']):
            return
        notify(db, user.id, event.event_key, 'Задача просрочена' if payload['stage'] == 'overdue'
               else 'Приближается срок задачи', 'task', task.id)
        return
    if event.kind == 'import.confirm':
        from app.crm.imports import process_import
        process_import(db, payload)
        return
    raise DomainError('OUTBOX_KIND_UNKNOWN', 'Для фонового события не зарегистрирован обработчик', 422)


def process_events(db: Session, limit: int = 25, now: datetime | None = None) -> dict[str, int]:
    """Process a bounded batch within the caller's transaction; caller commits or rolls back."""
    now = aware(now or utcnow())
    counts = {'succeeded': 0, 'retried': 0, 'failed': 0}
    events = db.scalars(select(OutboxEvent).where(OutboxEvent.status == 'pending')
                        .order_by(OutboxEvent.created_at, OutboxEvent.id).limit(limit)
                        .with_for_update(skip_locked=True)).all()
    for event in events:
        delay = min(300, 2 ** event.attempts)
        if event.attempts and event.started_at and aware(event.started_at) + timedelta(seconds=delay) > now:
            continue
        event.attempts += 1
        event.version += 1
        event.started_at = now
        event.status = 'running'
        event.progress = 10
        try:
            # Handler writes disappear if it fails, while the attempt and error remain observable.
            with db.begin_nested():
                dispatch(db, event)
                db.flush()
            event.status = 'succeeded'
            event.progress = 100
            event.error = None
            event.finished_at = now
            counts['succeeded'] += 1
        except Exception as exc:
            event.error = safe_job_error(exc.code if isinstance(exc, DomainError) else None)
            final = event.attempts >= MAX_ATTEMPTS
            event.status = 'failed' if final else 'pending'
            event.progress = 0
            event.finished_at = now if final else None
            counts['failed' if final else 'retried'] += 1
            logger.warning('outbox_failed event_id=%s kind=%s attempts=%s error=%s',
                           event.id, event.kind, event.attempts, event.error)
            if final:
                if event.kind == 'file.scan':
                    row = db.scalar(select(FileRecord).where(FileRecord.id == event.payload.get('file_id'))
                                    .with_for_update().execution_options(populate_existing=True))
                    if row is not None and row.status not in ('clean', 'infected'):
                        row.status = 'scan_failed'
                        row.scan_result = 'scanner_unavailable'
                        row.scanned_at = None
                        row.version += 1
                        audit(db, None, 'file', row.id, 'scan_failed', after={'status': row.status})
                elif event.kind == 'import.confirm':
                    batch = db.scalar(select(ImportBatch).where(ImportBatch.id == event.payload.get('batch_id'))
                                      .with_for_update().execution_options(populate_existing=True))
                    if batch is not None and batch.status != 'completed':
                        batch.status = 'failed'
                        batch.summary = {**batch.summary, 'error': event.error}
                        batch.version += 1
                        audit(db, None, 'import', batch.id, 'failed', after={'status': batch.status,
                                                                          'error': event.error})
                audit(db, None, 'job', event.id, 'failed', after={'kind': event.kind, 'status': event.status,
                                                               'attempts': event.attempts, 'error': event.error})
                # No payload, filenames, amounts, or technical exception messages in alerts.
                for target in db.scalars(select(User).where(User.active.is_(True))):
                    if can(db, target, 'admin.settings'):
                        notify(db, target.id, f'job-failed:{event.id}:{event.version}',
                               'Фоновая операция требует внимания', 'job', event.id)
    db.flush()
    return counts


def run_once(*, reminders: bool = True, limit: int = 25) -> dict[str, int]:
    if reminders:
        with SessionLocal.begin() as db:
            schedule_reminders(db)
    with SessionLocal.begin() as db:
        return process_events(db, limit=limit)


def main() -> None:
    # Populate all referenced ORM tables before worker queries are compiled.
    import app.commerce.models  # noqa: F401
    parser = argparse.ArgumentParser(description='Обработка очереди CRM и напоминаний')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--interval', type=float, default=2.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    last_reminder = 0.0
    while True:
        try:
            remind = time.monotonic() - last_reminder >= 60
            counts = run_once(reminders=remind)
            if remind:
                last_reminder = time.monotonic()
            if any(counts.values()):
                logger.info('outbox_batch %s', counts)
        except Exception:
            logger.exception('worker_iteration_failed')
            if args.once:
                raise
        if args.once:
            break
        time.sleep(max(0.2, min(args.interval, 30)))


if __name__ == '__main__':
    main()
