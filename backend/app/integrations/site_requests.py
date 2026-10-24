"""The «Заявки с сайта» section: receiving requests from the site (flow F1) and their processing.

Arrival stores the request exactly as the customer sent it and notifies whoever assigns them. The chief
administrator assigns a responsible employee, who gets a task to contact the customer within two working
hours (the site's promise); a request is then turned into a CRM request or rejected with a reason.
"""

import hashlib
import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.communication.access import file_view
from app.communication.files import store_quarantine, validate_upload
from app.communication.models import FileRecord
from app.core.config import settings
from app.core.db import DB, as_utc, utcnow
from app.core.errors import DomainError
from app.core.models import AppSetting, AuditEvent, OutboxEvent, User
from app.core.security import Actor, require_permission, scope_for, users_with_permission
from app.core.service import advisory, audit, check_version, idem, lock, notify, plain, prefetch, serialize
from app.crm.common import active_user
from app.crm.models import NumberCounter, Task
from app.crm.models import Request as CRMRequest
from app.integrations.models import SiteRequest, SiteRequestAttribution, SiteRequestItem
from app.integrations.site_auth import authenticate, site_user
from app.integrations.site_contract import (
    ATTACHMENT_EXTENSIONS,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS,
    RECEIVE_REQUEST_BODY,
    AssignInput,
    RejectInput,
    SiteRequestIn,
    SiteRequestReceipt,
    SiteTouch,
)

router = APIRouter(tags=['Заявки с сайта'])

STATUS_LABELS = {'new': 'Новая', 'in_work': 'В работе', 'converted': 'Преобразована', 'rejected': 'Отклонена'}
KIND_LABELS = {'product_rfq': 'Корзина запроса', 'one_click': 'Заказ в 1 клик', 'service': 'Услуга',
               'general': 'Общая форма'}
OPEN_TASK_STATUSES = ('assigned', 'in_progress')
# The site promises the first contact within two working hours (TZ 1.3, 9.4).
CONTACT_WORKING_HOURS = 2
WORKDAY_START, WORKDAY_END = time(9), time(18)


# ── receiving (F1) ──

def next_number(db: Session) -> str:
    advisory(db, 'site_request.number')
    counter = db.scalar(select(NumberCounter).where(NumberCounter.key == 'site_requests').with_for_update()
                        .execution_options(populate_existing=True))
    if counter is None:
        counter = NumberCounter(key='site_requests', value=0)
        db.add(counter)
    counter.value += 1
    db.flush()
    return f'W-{counter.value}'


def receipt(row: SiteRequest) -> dict[str, Any]:
    return {'id': row.id, 'number': row.number, 'status': row.status, 'received_at': as_utc(row.created_at)}


def parse_payload(text: str) -> tuple[SiteRequestIn, str]:
    """The validated payload and the hash that identifies its content for idempotent replays."""
    try:
        raw = json.loads(text)
    except (TypeError, ValueError):
        raise DomainError('PAYLOAD_INVALID', 'Часть payload должна содержать JSON заявки', 422, 'payload') from None
    try:
        data = SiteRequestIn.model_validate(raw)
    except ValidationError as exc:
        raise RequestValidationError([{**error, 'loc': ('body', 'payload', *error['loc'])}
                                      for error in exc.errors(include_url=False, include_context=False)]) from None
    canonical = json.dumps(raw, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return data, hashlib.sha256(canonical.encode()).hexdigest()


def checked_uploads(data: SiteRequestIn, uploads: list[tuple[str, str | None, bytes]]) -> list[tuple[str, str, bytes]]:
    """Files exactly as the payload lists them: same order, names, sizes and SHA-256, in a safe format."""
    if len(uploads) != len(data.attachments):
        raise DomainError('ATTACHMENTS_MISMATCH', 'Число файлов не совпадает со списком attachments', 422, 'files')
    checked = []
    for declared, (name, media_type, content) in zip(data.attachments, uploads, strict=True):
        if Path(declared.file_name).suffix.lower() not in ATTACHMENT_EXTENSIONS:
            raise DomainError('FILE_TYPE_INVALID', 'Допустимы XLSX, CSV, PDF и DOCX', 422, 'files')
        if len(content) != declared.size or len(content) > MAX_ATTACHMENT_BYTES:
            raise DomainError('ATTACHMENTS_MISMATCH', f'Размер файла {declared.file_name} не совпадает', 422, 'files')
        if hashlib.sha256(content).hexdigest() != declared.sha256:
            raise DomainError('ATTACHMENTS_MISMATCH', f'Контрольная сумма {declared.file_name} не совпадает', 422,
                              'files')
        filename, safe_type = validate_upload(declared.file_name, media_type, content)
        checked.append((filename, safe_type, content))
    return checked


def touch_columns(prefix: str, touch: SiteTouch | None) -> dict[str, Any]:
    fields = ('landing_url', 'referrer', 'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term', 'at')
    return {f'{prefix}_{field}': getattr(touch, field) if touch else None for field in fields}


def store_request(db: Session, author: User, data: SiteRequestIn, payload_hash: str,
                  files: list[tuple[str, str, bytes]]) -> SiteRequest:
    customer = data.customer
    row = SiteRequest(
        number=next_number(db), site_request_id=data.site_request_id, site_number=data.site_number,
        payload_hash=payload_hash, kind=data.kind, lang=data.lang, form_type=data.form_type,
        submitted_at=data.created_at, page_url=data.page_url, customer_name=customer.name,
        company=customer.company, inn=customer.inn, phone=customer.phone, email=customer.email,
        city=customer.city, comment=customer.comment, desired_date=data.desired_date,
        marketing_consent=customer.marketing_consent,
        service=data.service.model_dump() if data.service else None,
    )
    db.add(row)
    db.flush()
    for position, item in enumerate(data.items, start=1):
        db.add(SiteRequestItem(site_request_id=row.id, position=position, **item.model_dump()))
    attribution = data.attribution
    if attribution:
        first, last = attribution.first_touch, attribution.last_touch
        click = last or first
        ids = attribution.client_ids
        db.add(SiteRequestAttribution(
            site_request_id=row.id, channel=attribution.channel,
            conversion_page_url=attribution.conversion_page_url,
            **touch_columns('first', first), **touch_columns('last', last),
            yclid=(click.yclid if click else None) or (first.yclid if first else None),
            gclid=(click.gclid if click else None) or (first.gclid if first else None),
            ym_uid=ids.ym_uid if ids else None, ga_client_id=ids.ga_client_id if ids else None,
        ))
    for filename, media_type, content in files:
        key, checksum = store_quarantine(content)
        record = FileRecord(name=filename, media_type=media_type, size=len(content), sha256=checksum,
                            storage_key=key, author_id=author.id, site_request_id=row.id)
        db.add(record)
        db.flush()
        # Customer files stay quarantined until ClamAV checks them, like every other upload.
        db.add(OutboxEvent(event_key=f'file-scan:{record.id}', kind='file.scan', payload={'file_id': record.id}))
    db.flush()
    audit(db, author, 'site_request', row.id, 'received', after={
        'number': row.number, 'site_number': row.site_number, 'kind': row.kind, 'items': len(data.items),
        'attachments': len(files)})
    for target in users_with_permission(db, 'site_requests.assign'):
        notify(db, target.id, f'site-request-new:{row.id}', f'Новая заявка с сайта {row.number}: {row.company}',
               'site_request', row.id)
    return row


def receive_site_request(db: Session, headers: dict[str, str], payload: str | None,
                         uploads: list[tuple[str, str | None, bytes]]) -> tuple[dict[str, Any], bool]:
    """Store a request once; a repeated delivery with the same key and content returns the same receipt."""
    if not payload:
        raise DomainError('PAYLOAD_REQUIRED', 'Передайте JSON заявки в части payload', 422, 'payload')
    data, payload_hash = parse_payload(payload)
    key = headers.get('idempotency-key', '')
    if key != data.site_request_id:
        raise DomainError('IDEMPOTENCY_KEY_MISMATCH', 'Idempotency-Key должен совпадать с site_request_id', 422,
                          'Idempotency-Key')
    advisory(db, f'site-request:{key}')
    previous = db.scalar(select(SiteRequest).where(SiteRequest.site_request_id == key))
    if previous is not None:
        if previous.payload_hash != payload_hash:
            raise DomainError('IDEMPOTENCY_CONFLICT', 'Заявка с этим ключом уже получена с другим содержимым', 409)
        return receipt(previous), False
    files = checked_uploads(data, uploads)
    row = store_request(db, site_user(db), data, payload_hash, files)
    return receipt(row), True


@router.post('/integrations/site/requests', status_code=201, response_model=SiteRequestReceipt,
             openapi_extra={'requestBody': RECEIVE_REQUEST_BODY}, responses={
    200: {'description': 'Заявка уже получена: повтор с тем же ключом и содержимым', 'model': SiteRequestReceipt},
    401: {'description': 'Нет токена, подпись неверна или устарела'},
    403: {'description': 'Адрес сервера сайта не разрешён'},
    409: {'description': 'Ключ уже использован для другого содержимого'},
    422: {'description': 'Заявка или вложения не прошли проверку'},
})
async def receive(request: Request, response: Response, db: DB) -> dict[str, Any]:
    """Заявка с сайта (поток F1). multipart/form-data: часть `payload` — JSON `SiteRequestIn`, части `files` —
    вложения в порядке `attachments`. Заголовки `Authorization`, `X-Site-Timestamp`, `X-Site-Signature`,
    `Idempotency-Key` описаны в docs/site-integration.md."""
    body = await request.body()
    headers = {key.lower(): value for key, value in request.headers.items()}
    host = request.client.host if request.client else None
    # The signature is checked before the form is parsed: unsigned bodies are never processed.
    await run_in_threadpool(authenticate, db, headers, host, body)
    form = await request.form(max_files=MAX_ATTACHMENTS, max_fields=20)
    part = form.get('payload')
    payload = part if isinstance(part, str) or part is None else (await part.read()).decode('utf-8', 'replace')
    uploads = []
    for upload in form.getlist('files'):
        if isinstance(upload, str):
            raise DomainError('ATTACHMENTS_MISMATCH', 'Части files должны быть файлами', 422, 'files')
        uploads.append((upload.filename or '', upload.content_type, await upload.read(MAX_ATTACHMENT_BYTES + 1)))
    result, created = await run_in_threadpool(receive_site_request, db, headers, payload, uploads)
    if not created:
        response.status_code = 200
    return result


# ── processing ──

def working_policy(db: Session) -> tuple[set[int], set[str]]:
    """Working weekdays (0 = Monday) and holidays of the company calendar used for task reminders."""
    setting = db.scalar(select(AppSetting).where(AppSetting.key == 'task_reminders', AppSetting.status == 'published'))
    value = setting.value if setting else {}
    days = value.get('working_days')
    holidays = value.get('holidays')
    return (set(days) if isinstance(days, list) and days else {0, 1, 2, 3, 4},
            {str(day) for day in holidays} if isinstance(holidays, list) else set())


def add_working_hours(db: Session, start: datetime, hours: int) -> datetime:
    """`start` plus working hours of the company day (9:00–18:00 local, working days only)."""
    days, holidays = working_policy(db)
    zone = ZoneInfo(settings.company_timezone)
    moment = as_utc(start).astimezone(zone)
    remaining = timedelta(hours=hours)
    for _ in range(400):
        day = moment.date()
        opens = datetime.combine(day, WORKDAY_START, zone)
        closes = datetime.combine(day, WORKDAY_END, zone)
        if day.weekday() not in days or day.isoformat() in holidays or moment >= closes:
            moment = datetime.combine(day + timedelta(days=1), WORKDAY_START, zone)
            continue
        moment = max(moment, opens)
        if moment + remaining <= closes:
            return (moment + remaining).astimezone(timezone.utc)
        remaining -= closes - moment
        moment = datetime.combine(day + timedelta(days=1), WORKDAY_START, zone)
    return (as_utc(start) + timedelta(hours=hours)).astimezone(timezone.utc)


def sees_all(db: Session, user: User) -> bool:
    """The «all» scope sees every request; others see those they are responsible for."""
    return 'all' in (scope_for(db, user, 'site_requests.read'), scope_for(db, user, 'site_requests.assign'))


def visible(db: Session, user: User) -> Any:
    return True if sees_all(db, user) else SiteRequest.assignee_id == user.id


def check_site_request(db: Session, user: User, entity_id: str, *, for_update: bool = False) -> SiteRequest:
    require_permission(db, user, 'site_requests.read')
    row = db.get(SiteRequest, entity_id)
    if row is None or not (sees_all(db, user) or row.assignee_id == user.id):
        raise DomainError('NOT_FOUND', 'Заявка с сайта не найдена или недоступна', 404)
    return lock(db, SiteRequest, entity_id) if for_update else row


def summary_view(row: SiteRequest, items: int, assignee: User | None, channel: str | None,
                 request_number: str | None) -> dict[str, Any]:
    return {
        'id': row.id, 'number': row.number, 'site_number': row.site_number, 'kind': row.kind,
        'kind_label': KIND_LABELS.get(row.kind, row.kind), 'lang': row.lang, 'status': row.status,
        'status_label': STATUS_LABELS.get(row.status, row.status), 'received_at': plain(row.created_at),
        'company': row.company, 'inn': row.inn, 'customer_name': row.customer_name, 'phone': row.phone,
        'email': row.email, 'city': row.city, 'items_count': items, 'channel': channel,
        'assignee_id': row.assignee_id, 'assignee_name': assignee.name if assignee else None,
        'request_id': row.request_id, 'request_number': request_number, 'version': row.version,
    }


@router.get('/site-requests')
def list_site_requests(
    user: Actor, db: DB,
    status: Literal['new', 'in_work', 'converted', 'rejected'] | None = None,
    q: str = Query(default='', max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    require_permission(db, user, 'site_requests.read')
    base = select(SiteRequest).where(visible(db, user))
    if q:
        pattern = f'%{q.strip()}%'
        base = base.where(or_(SiteRequest.company.ilike(pattern), SiteRequest.number.ilike(pattern),
                              SiteRequest.site_number.ilike(pattern), SiteRequest.inn.ilike(pattern),
                              SiteRequest.email.ilike(pattern), SiteRequest.phone.ilike(pattern),
                              SiteRequest.customer_name.ilike(pattern)))
    visible_rows = base.subquery()
    found = dict(db.execute(select(visible_rows.c.status, func.count()).group_by(visible_rows.c.status))
                 .tuples().all())
    counts = {key: found.get(key, 0) for key in STATUS_LABELS}
    statement = base.where(SiteRequest.status == status) if status else base
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = db.scalars(statement.order_by(SiteRequest.created_at.desc(), SiteRequest.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    ids = [row.id for row in rows]
    items = dict(db.execute(select(SiteRequestItem.site_request_id, func.count()).where(
        SiteRequestItem.site_request_id.in_(ids)).group_by(SiteRequestItem.site_request_id)).tuples().all())
    channels = dict(db.execute(select(SiteRequestAttribution.site_request_id, SiteRequestAttribution.channel)
                               .where(SiteRequestAttribution.site_request_id.in_(ids))).tuples().all())
    users = prefetch(db, User, (row.assignee_id for row in rows))
    requests = prefetch(db, CRMRequest, (row.request_id for row in rows))
    return {
        'items': [summary_view(row, items.get(row.id, 0), users.get(row.assignee_id or ''), channels.get(row.id),
                               requests[row.request_id].number if row.request_id in requests else None)
                  for row in rows],
        'total': total, 'page': page, 'page_size': page_size, 'counts': counts,
        'statuses': STATUS_LABELS,
    }


def detail_view(db: Session, user: User, row: SiteRequest) -> dict[str, Any]:
    items = db.scalars(select(SiteRequestItem).where(SiteRequestItem.site_request_id == row.id)
                       .order_by(SiteRequestItem.position)).all()
    attribution = db.scalar(select(SiteRequestAttribution).where(SiteRequestAttribution.site_request_id == row.id))
    files = db.scalars(select(FileRecord).where(FileRecord.site_request_id == row.id)
                       .order_by(FileRecord.created_at)).all()
    assignee = db.get(User, row.assignee_id) if row.assignee_id else None
    request = db.get(CRMRequest, row.request_id) if row.request_id else None
    history = db.scalars(select(AuditEvent).where(AuditEvent.entity_type == 'site_request',
                                                  AuditEvent.entity_id == row.id)
                         .order_by(AuditEvent.created_at, AuditEvent.id).limit(100)).all()
    actors = prefetch(db, User, (event.actor_id for event in history))
    value = summary_view(row, len(items), assignee, attribution.channel if attribution else None,
                         request.number if request else None)
    value.update({
        'form_type': row.form_type, 'submitted_at': plain(row.submitted_at), 'page_url': row.page_url,
        'comment': row.comment, 'desired_date': plain(row.desired_date),
        'marketing_consent': row.marketing_consent, 'service': row.service,
        'assigned_at': plain(row.assigned_at), 'converted_at': plain(row.converted_at),
        'reject_reason': row.reject_reason, 'rejected_at': plain(row.rejected_at),
        'items': [{key: value for key, value in serialize(item).items()
                   if key not in ('site_request_id', 'version', 'created_at')} for item in items],
        'attribution': ({key: value for key, value in serialize(attribution).items()
                         if key not in ('id', 'site_request_id', 'version', 'created_at')} if attribution else None),
        'files': [file_view(file) for file in files],
        'history': [{'action': event.action, 'at': plain(event.created_at), 'reason': event.reason,
                     'actor_name': actors[event.actor_id].name if event.actor_id in actors else None}
                    for event in history],
        'can_assign': scope_for(db, user, 'site_requests.assign') is not None,
        'can_process': scope_for(db, user, 'site_requests.write') is not None,
    })
    return value


@router.get('/site-requests/{entity_id}')
def site_request(entity_id: str, user: Actor, db: DB) -> dict[str, Any]:
    return detail_view(db, user, check_site_request(db, user, entity_id))


def close_contact_tasks(db: Session, row: SiteRequest, result: str) -> None:
    for task in db.scalars(select(Task).where(Task.entity_type == 'site_request', Task.entity_id == row.id,
                                              Task.status.in_(OPEN_TASK_STATUSES))):
        task.status = 'cancelled'
        task.result = result
        task.completed_at = utcnow()
        task.version += 1


@router.post('/site-requests/{entity_id}/assign')
def assign_site_request(entity_id: str, body: AssignInput, user: Actor, db: DB,
                        idempotency_key: str | None = Header(default=None)) -> dict[str, Any]:
    """Назначить ответственного. Переназначение — с причиной; новый ответственный получает задачу
    «Связаться с клиентом» со сроком 2 рабочих часа."""
    require_permission(db, user, 'site_requests.assign')

    def operation() -> dict[str, Any]:
        row = check_site_request(db, user, entity_id, for_update=True)
        check_version(row, body.version)
        if row.status in ('converted', 'rejected'):
            raise DomainError('SITE_REQUEST_CLOSED', 'Заявка с сайта уже обработана', 409)
        assignee = active_user(db, body.assignee_id)
        if row.assignee_id == assignee.id:
            raise DomainError('ASSIGNEE_UNCHANGED', 'Этот сотрудник уже ответственный', 422, 'assignee_id')
        if row.assignee_id and not body.reason:
            raise DomainError('REASON_REQUIRED', 'Укажите причину переназначения', 422, 'reason')
        before = {'assignee_id': row.assignee_id, 'status': row.status}
        if row.assignee_id:
            close_contact_tasks(db, row, f'Заявка передана сотруднику {assignee.name}')
        now = utcnow()
        row.assignee_id = assignee.id
        row.assigned_at = now
        row.status = 'in_work'
        row.version += 1
        db.add(Task(title=f'Связаться с клиентом по заявке с сайта {row.number}', entity_type='site_request',
                    entity_id=row.id, assignee_id=assignee.id, author_id=user.id, priority='high',
                    due_at=add_working_hours(db, now, CONTACT_WORKING_HOURS),
                    event_key=f'site-request-contact:{row.id}:{row.version}'))
        audit(db, user, 'site_request', row.id, 'reassigned' if before['assignee_id'] else 'assigned', before,
              {'assignee_id': assignee.id, 'status': row.status}, body.reason)
        notify(db, assignee.id, f'site-request-assigned:{row.id}:{row.version}',
               f'Вам назначена заявка с сайта {row.number}: {row.company}', 'site_request', row.id)
        db.flush()
        return detail_view(db, user, row)

    return idem(db, user, idempotency_key, f'site-request.assign:{entity_id}', body.model_dump(), operation)


@router.post('/site-requests/{entity_id}/reject')
def reject_site_request(entity_id: str, body: RejectInput, user: Actor, db: DB,
                        idempotency_key: str | None = Header(default=None)) -> dict[str, Any]:
    """Отклонить заявку (спам, дубль, не наш профиль) с причиной; учитывается в отчёте по каналам."""
    require_permission(db, user, 'site_requests.write')

    def operation() -> dict[str, Any]:
        row = check_site_request(db, user, entity_id, for_update=True)
        check_version(row, body.version)
        if row.status in ('converted', 'rejected'):
            raise DomainError('SITE_REQUEST_CLOSED', 'Заявка с сайта уже обработана', 409)
        before = {'status': row.status}
        row.status = 'rejected'
        row.reject_reason = body.reason
        row.rejected_at = utcnow()
        row.version += 1
        close_contact_tasks(db, row, f'Заявка с сайта отклонена: {body.reason}')
        audit(db, user, 'site_request', row.id, 'rejected', before, {'status': row.status}, body.reason)
        db.flush()
        return detail_view(db, user, row)

    return idem(db, user, idempotency_key, f'site-request.reject:{entity_id}', body.model_dump(), operation)
