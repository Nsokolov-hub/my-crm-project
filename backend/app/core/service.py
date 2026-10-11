import hashlib
import json
from collections.abc import Callable, Iterable
from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal
from typing import Any, TypeVar

from sqlalchemy import func, inspect, select, text, tuple_
from sqlalchemy.orm import Session

from app.core.db import Entity
from app.core.errors import DomainError
from app.core.events import publish
from app.core.models import AuditEvent, IdempotencyRecord, Notification, User

# Work outside an HTTP request (worker, scripts) has no request code.
BACKGROUND = 'background'
correlation_id: ContextVar[str] = ContextVar('correlation_id', default=BACKGROUND)
T = TypeVar('T', bound=Entity)
# Stays well below the bind parameter limits of SQLite and PostgreSQL.
PREFETCH_CHUNK = 5000
_RETAINED = 'crm.retained_rows'


def plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, 'f')
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    return value


def serialize(obj: Any) -> dict[str, Any]:
    # Columns marked info={'internal': True} hold derived data for queries, not object state.
    return {a.key: plain(getattr(obj, a.key)) for a in inspect(type(obj)).column_attrs
            if not a.columns[0].info.get('internal')}


def advisory(db: Session, key: str) -> None:
    if db.bind is not None and db.bind.dialect.name == 'postgresql':
        number = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big', signed=True)
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': number})


def lock(db: Session, model: type[T], entity_id: str | None) -> T:
    row = db.scalar(select(model).where(model.id == entity_id).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise DomainError('NOT_FOUND', 'Объект не найден или недоступен', 404)
    return row


def retain(db: Session, rows: Iterable[Any]) -> None:
    """Keep loaded rows alive until the session ends.

    The identity map references objects weakly: an object nobody holds is dropped at once and the next
    ``db.get`` queries again. Holding them for the session (one request) makes batch loading effective.
    """
    db.info.setdefault(_RETAINED, []).extend(rows)


def prefetch(db: Session, model: type[T], ids: Iterable[str | None]) -> dict[str, T]:
    """Load rows by id with as few queries as possible.

    The rows stay in the session identity map, so later ``db.get(model, id)`` calls in per-row view
    functions are answered without SQL. This is how list endpoints avoid one query per row.
    """
    wanted = sorted({value for value in ids if value})
    found: dict[str, T] = {}
    for start in range(0, len(wanted), PREFETCH_CHUNK):
        chunk = wanted[start:start + PREFETCH_CHUNK]
        found.update((row.id, row) for row in db.scalars(select(model).where(model.id.in_(chunk))))
    retain(db, found.values())
    return found


def check_version(obj: Entity, version: int) -> None:
    if obj.version != version:
        raise DomainError('VERSION_CONFLICT', 'Запись изменена другим сотрудником. Обновите данные и повторите изменение.', 409, 'version')


def idem(db: Session, user: User, key: str | None, scope: str, payload: Any, operation: Callable[[], dict[str, Any]], reconstruct: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> dict[str, Any]:
    if not key or len(key) > 128:
        raise DomainError('IDEMPOTENCY_KEY_REQUIRED', 'Передайте ключ повторяемости операции', 422, 'Idempotency-Key')
    digest = hashlib.sha256(json.dumps(plain(payload), sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    advisory(db, f'idem:{user.id}:{scope}:{key}')
    previous = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.user_id == user.id, IdempotencyRecord.scope == scope, IdempotencyRecord.key == key))
    if previous:
        if previous.payload_hash != digest:
            raise DomainError('IDEMPOTENCY_CONFLICT', 'Этот ключ уже использован для другой операции', 409)
        if reconstruct:
            return reconstruct(previous.result)
        return previous.result
    result = plain(operation())
    db.add(IdempotencyRecord(user_id=user.id, scope=scope, key=key, payload_hash=digest, result=result))
    db.flush()
    if reconstruct:
        return reconstruct(result)
    return result


def audit(db: Session, user: User | None, entity_type: str, entity_id: str, action: str, before: Any = None, after: Any = None, reason: str | None = None) -> None:
    hidden = {'password', 'password_hash', 'token', 'token_hash', 'csrf_token', 'mfa_secret', 'secret', 'secret_key'}
    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k not in hidden}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return plain(value)
    db.add(AuditEvent(actor_id=user.id if user else None, entity_type=entity_type, entity_id=entity_id, action=action, before=clean(before), after=clean(after), reason=reason, correlation_id=correlation_id.get()))


def notify(db: Session, user_id: str, event_key: str, title: str, entity_type: str, entity_id: str) -> None:
    advisory(db, f'notification:{user_id}:{event_key}')
    if not db.scalar(select(Notification.id).where(Notification.user_id == user_id, Notification.event_key == event_key)):
        db.add(Notification(user_id=user_id, event_key=event_key, title=title, entity_type=entity_type, entity_id=entity_id))
        db.flush()
        publish(db, 'notification', [user_id])


def notify_many(db: Session, notices: Iterable[tuple[str, str, str, str, str]]) -> None:
    """notify() for (user_id, event_key, title, entity_type, entity_id) rows; one query finds delivered ones."""
    notices = list(notices)
    for start in range(0, len(notices), PREFETCH_CHUNK):
        chunk = notices[start:start + PREFETCH_CHUNK]
        delivered = set(db.execute(select(Notification.user_id, Notification.event_key).where(
            tuple_(Notification.user_id, Notification.event_key).in_({(row[0], row[1]) for row in chunk}))).tuples())
        for user_id, event_key, title, entity_type, entity_id in chunk:
            if (user_id, event_key) not in delivered:
                notify(db, user_id, event_key, title, entity_type, entity_id)


def page(db: Session, statement: Any, page: int = 1, page_size: int = 25) -> dict[str, Any]:
    if page < 1 or not 1 <= page_size <= 100:
        raise DomainError('PAGINATION_INVALID', 'Размер страницы от 1 до 100; номер от 1', 422)
    total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    items = db.scalars(statement.limit(page_size).offset((page - 1) * page_size)).all()
    retain(db, items)
    return {'items': [serialize(i) for i in items], 'total': total, 'page': page, 'page_size': page_size}
