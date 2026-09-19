from typing import Any

from fastapi import APIRouter, Depends, Header
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db, utcnow
from app.core.errors import DomainError
from app.core.models import AppSetting, AuditEvent, User
from app.core.security import (
    can,
    check_client,
    check_request,
    client_predicate,
    current_user,
    request_predicate,
    require_permission,
    scope_for,
)
from app.core.service import advisory, audit, check_version, idem, lock, notify, serialize
from app.core.service import page as paginate
from app.crm.models import (
    Call,
    Contact,
    Counterparty,
    Request,
    RequestItem,
    RequestItemRevision,
    RequestMember,
    Seller,
    Task,
)
from app.crm.schemas import (
    CallInput,
    CallPatch,
    ClientInput,
    ClientPatch,
    ContactInput,
    ItemInput,
    ItemPatch,
    RequestInput,
    RequestPatch,
    SellerInput,
    ShareInput,
    TaskInput,
    TaskPatch,
)

router = APIRouter(tags=['CRM'])


def active_user(db: Session, entity_id: str) -> User:
    row = db.get(User, entity_id)
    if not row or not row.active:
        raise DomainError('ASSIGNEE_INVALID', 'Выберите действующего сотрудника', 422, 'assignee_id')
    return row


def contact_matches(db: Session, contact_id: str | None, client_id: str) -> None:
    if contact_id:
        row = db.get(Contact, contact_id)
        if not row or row.client_id != client_id or row.archived:
            raise DomainError('CONTACT_INVALID', 'Контакт не относится к выбранному клиенту', 422, 'contact_id')


def save(db: Session, user: User, row: Any, kind: str) -> dict[str, Any]:
    db.add(row)
    db.flush()
    result = serialize(row)
    audit(db, user, kind, row.id, 'created', after=result)
    return result


def check_link(db: Session, user: User, kind: str | None, entity_id: str | None) -> None:
    if bool(kind) != bool(entity_id):
        raise DomainError('OBJECT_LINK_REQUIRED', 'Укажите тип и идентификатор связанного объекта')
    if kind == 'request':
        check_request(db, user, entity_id)
    elif kind == 'counterparty':
        check_client(db, user, entity_id)
    elif kind == 'wave':
        from app.commerce.models import Wave
        require_permission(db, user, 'waves.write')
        if not db.get(Wave, entity_id):
            raise DomainError('NOT_FOUND', 'Волна недоступна', 404)


@router.get('/counterparties')
def clients(q: str = '', kind: str | None = None, page: int = 1, page_size: int = 25, sort: str = 'name', direction: str = 'asc', archived: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'clients.read')
    stmt = select(Counterparty).where(client_predicate(db, user), Counterparty.archived == archived)
    if q:
        stmt = stmt.where(or_(Counterparty.name.ilike(f'%{q}%'), Counterparty.email.ilike(f'%{q}%'), Counterparty.phone.ilike(f'%{q}%'), Counterparty.tax_id.ilike(f'%{q}%')))
    if kind:
        stmt = stmt.where(Counterparty.kind.in_([kind, 'both']))
    col = {'name': Counterparty.name, 'created_at': Counterparty.created_at}.get(sort, Counterparty.name)
    return paginate(db, stmt.order_by(col.desc() if direction == 'desc' else col, Counterparty.id), page, page_size)


@router.post('/counterparties', status_code=201)
def create_client(body: ClientInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'clients.write')
    data = body.model_dump()
    data['owner_id'] = body.owner_id or user.id
    if data['owner_id'] != user.id:
        require_permission(db, user, 'requests.assign')
    active_user(db, data['owner_id'])
    return save(db, user, Counterparty(**data), 'counterparty')


@router.get('/counterparties/{entity_id}')
def client_detail(entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return serialize(check_client(db, user, entity_id))


@router.patch('/counterparties/{entity_id}')
def edit_client(entity_id: str, body: ClientPatch, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    check_client(db, user, entity_id, 'clients.write')
    row = lock(db, Counterparty, entity_id)
    check_version(row, body.version)
    before = serialize(row)
    if body.owner_id and body.owner_id != row.owner_id:
        require_permission(db, user, 'requests.assign')
        active_user(db, body.owner_id)
        if not body.reason:
            raise DomainError('REASON_REQUIRED', 'Укажите причину переназначения', 422, 'reason')
    for k, v in body.model_dump(exclude_unset=True, exclude={'version', 'reason'}).items():
        setattr(row, k, v)
    row.version += 1
    audit(db, user, 'counterparty', row.id, 'updated', before, serialize(row), body.reason)
    return serialize(row)


@router.get('/counterparties/{entity_id}/contacts')
def contacts(entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    check_client(db, user, entity_id)
    return paginate(db, select(Contact).where(Contact.client_id == entity_id, Contact.archived.is_(False)).order_by(Contact.name), 1, 100)


@router.post('/counterparties/{entity_id}/contacts', status_code=201)
def create_contact(entity_id: str, body: ContactInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    check_client(db, user, entity_id, 'clients.write')
    return save(db, user, Contact(client_id=entity_id, **body.model_dump()), 'contact')


@router.get('/tasks')
def tasks(status: str | None = None, q: str = '', entity_id: str | None = None, overdue: bool = False, page: int = 1, page_size: int = 25, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'tasks.read')
    stmt = select(Task)
    if scope_for(db, user, 'tasks.read') != 'all':
        stmt = stmt.where(or_(Task.assignee_id == user.id, Task.author_id == user.id))
    if status:
        stmt = stmt.where(Task.status == status)
    if q:
        stmt = stmt.where(Task.title.ilike(f'%{q}%'))
    if entity_id:
        stmt = stmt.where(Task.entity_id == entity_id)
    if overdue:
        stmt = stmt.where(Task.due_at < utcnow(), Task.status.in_(['assigned', 'in_progress']))
    return paginate(db, stmt.order_by(Task.due_at, Task.id), page, page_size)


@router.post('/tasks', status_code=201)
def create_task(body: TaskInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'tasks.write')
    check_link(db, user, body.entity_type, body.entity_id)
    assignee = active_user(db, body.assignee_id or user.id)
    check_link(db, assignee, body.entity_type, body.entity_id)
    row = Task(**body.model_dump(exclude={'assignee_id'}), assignee_id=assignee.id, author_id=user.id)
    result = save(db, user, row, 'task')
    notify(db, assignee.id, f'task:{row.id}', 'Вам назначена задача', 'task', row.id)
    return result


@router.patch('/tasks/{entity_id}')
def edit_task(entity_id: str, body: TaskPatch, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'tasks.write')
    row = lock(db, Task, entity_id)
    if scope_for(db, user, 'tasks.write') != 'all' and user.id not in (row.assignee_id, row.author_id):
        raise DomainError('NOT_FOUND', 'Задача недоступна', 404)
    check_link(db, user, row.entity_type, row.entity_id)
    check_version(row, body.version)
    if body.status in ('completed', 'cancelled') and not body.result:
        raise DomainError('RESULT_REQUIRED', 'Укажите результат выполнения или причину отмены', 422, 'result')
    if row.status in ('completed', 'cancelled') and body.status not in (None, row.status):
        raise DomainError('TASK_CLOSED', 'Закрытую задачу нельзя повторно открыть; создайте следующую задачу', 409)
    if body.assignee_id:
        assignee = active_user(db, body.assignee_id)
        check_link(db, assignee, row.entity_type, row.entity_id)
    before = serialize(row)
    for k, v in body.model_dump(exclude_unset=True, exclude={'version'}).items():
        setattr(row, k, v)
    if body.status in ('completed', 'cancelled'):
        row.completed_at = utcnow()
    row.version += 1
    audit(db, user, 'task', row.id, 'updated', before, serialize(row), body.result)
    return serialize(row)


CALL_RESULTS = ['no_answer', 'callback', 'interested', 'request_received', 'rejected', 'invalid_contact']


def valid_call(db: Session, result: str, next_at: Any, reason: str | None) -> None:
    setting = db.scalar(select(AppSetting).where(AppSetting.key == 'call_results'))
    allowed = setting.value.get('values', CALL_RESULTS) if setting else CALL_RESULTS
    if result not in allowed:
        raise DomainError('CALL_RESULT_INVALID', 'Выберите результат из справочника', 422, 'result')
    if result == 'callback' and not next_at:
        raise DomainError('NEXT_ACTION_REQUIRED', 'Для перезвона укажите дату следующего действия', 422, 'next_at')
    if result == 'rejected' and not reason:
        raise DomainError('REASON_REQUIRED', 'Для отказа укажите причину', 422, 'reason')
    if next_at and next_at.tzinfo is None:
        raise DomainError('TIMEZONE_REQUIRED', 'Укажите часовой пояс даты', 422, 'next_at')


@router.get('/calls')
def calls(client_id: str | None = None, page: int = 1, page_size: int = 25, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'clients.read')
    stmt = select(Call).where(Call.client_id.in_(select(Counterparty.id).where(client_predicate(db, user))))
    if client_id:
        check_client(db, user, client_id)
        stmt = stmt.where(Call.client_id == client_id)
    return paginate(db, stmt.order_by(Call.occurred_at.desc()), page, page_size)


@router.post('/calls', status_code=201)
def create_call(body: CallInput, idempotency_key: str | None = Header(default=None), user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'calls.write')
    check_client(db, user, body.client_id, 'clients.write')
    contact_matches(db, body.contact_id, body.client_id)
    valid_call(db, body.result, body.next_at, body.reason)
    def operation() -> dict[str, Any]:
        row = Call(**body.model_dump(exclude={'next_assignee_id', 'occurred_at'}), author_id=user.id, occurred_at=body.occurred_at or utcnow())
        if body.next_at:
            assignee = active_user(db, body.next_assignee_id or user.id)
            check_client(db, assignee, body.client_id)
            task = Task(title='Связаться с клиентом', entity_type='counterparty', entity_id=body.client_id, author_id=user.id, assignee_id=assignee.id, due_at=body.next_at)
            db.add(task)
            db.flush()
            row.task_id = task.id
            notify(db, assignee.id, f'task:{task.id}', 'Запланирован следующий контакт', 'task', task.id)
        return save(db, user, row, 'call')
    return idem(db, user, idempotency_key, 'calls.create', body.model_dump(mode='json'), operation)


@router.patch('/calls/{entity_id}')
def edit_call(entity_id: str, body: CallPatch, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'calls.write')
    row = lock(db, Call, entity_id)
    check_client(db, user, row.client_id, 'clients.write')
    check_version(row, body.version)
    valid_call(db, body.result or row.result, row.next_at, body.reason)
    before = serialize(row)
    for k, v in body.model_dump(exclude_unset=True, exclude={'version'}).items():
        setattr(row, k, v)
    row.version += 1
    audit(db, user, 'call', row.id, 'corrected', before, serialize(row), body.reason)
    return serialize(row)


@router.get('/requests')
def requests(q: str = '', stage: str | None = None, client_id: str | None = None, owner_id: str | None = None, page: int = 1, page_size: int = 25, sort: str = 'created_at', direction: str = 'desc', user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'requests.read')
    stmt = select(Request).where(request_predicate(db, user), Request.archived.is_(False))
    if q:
        stmt = stmt.where(or_(Request.title.ilike(f'%{q}%'), Request.number.ilike(f'%{q}%')))
    if client_id:
        stmt = stmt.where(Request.client_id == client_id)
    if stage:
        stmt = stmt.where(Request.commercial_stage == stage)
    if owner_id:
        stmt = stmt.where(Request.owner_id == owner_id)
    col = {'created_at': Request.created_at, 'title': Request.title, 'number': Request.number, 'due_at': Request.due_at}.get(sort, Request.created_at)
    result = paginate(db, stmt.order_by(col.desc() if direction == 'desc' else col, Request.id), page, page_size)
    for item in result['items']:
        item['client_name'] = db.get(Counterparty, item['client_id']).name
        item['owner_name'] = db.get(User, item['owner_id']).name
    return result


@router.post('/requests', status_code=201)
def create_request(body: RequestInput, idempotency_key: str | None = Header(default=None), user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'requests.write')
    check_client(db, user, body.client_id)
    contact_matches(db, body.contact_id, body.client_id)
    if body.seller_id and not db.get(Seller, body.seller_id):
        raise DomainError('SELLER_INVALID', 'Организация продавца не найдена', 422, 'seller_id')
    if body.source_call_id:
        call = db.get(Call, body.source_call_id)
        if not call or call.client_id != body.client_id or call.result != 'request_received' or call.cancelled:
            raise DomainError('CALL_INVALID', 'Выберите действующий звонок с результатом «Запрос получен»', 422, 'source_call_id')
    owner = active_user(db, body.owner_id or user.id)
    if owner.id != user.id:
        require_permission(db, user, 'requests.assign')
    def operation() -> dict[str, Any]:
        advisory(db, 'request.number')
        count = db.scalar(select(func.count()).select_from(Request)) or 0
        row = Request(**body.model_dump(exclude={'owner_id'}), owner_id=owner.id, number=f'З-{utcnow():%Y}-{count + 1:05d}')
        return save(db, user, row, 'request')
    return idem(db, user, idempotency_key, 'requests.create', body.model_dump(mode='json'), operation)


@router.get('/requests/{entity_id}')
def request_detail(entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = check_request(db, user, entity_id)
    result = serialize(row)
    result['client_name'] = db.get(Counterparty, row.client_id).name
    result['owner_name'] = db.get(User, row.owner_id).name
    result['members'] = list(db.scalars(select(RequestMember.user_id).where(RequestMember.request_id == row.id)))
    result['next_task'] = None
    task = db.scalar(select(Task).where(Task.entity_type == 'request', Task.entity_id == row.id, Task.status.in_(['assigned', 'in_progress'])).order_by(Task.due_at))
    if task:
        result['next_task'] = serialize(task)
    return result


@router.patch('/requests/{entity_id}')
def edit_request(entity_id: str, body: RequestPatch, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    check_request(db, user, entity_id, 'requests.write')
    row = lock(db, Request, entity_id)
    check_version(row, body.version)
    before = serialize(row)
    if body.owner_id and body.owner_id != row.owner_id:
        require_permission(db, user, 'requests.assign')
        active_user(db, body.owner_id)
        if not body.reason:
            raise DomainError('REASON_REQUIRED', 'Укажите причину переназначения', 422, 'reason')
    if body.seller_id and body.seller_id != row.seller_id:
        from app.commerce.models import CommercialDocument
        if not db.get(Seller, body.seller_id):
            raise DomainError('SELLER_INVALID', 'Организация продавца не найдена', 422, 'seller_id')
        if db.scalar(select(CommercialDocument.id).where(CommercialDocument.request_id == row.id).limit(1)):
            raise DomainError('SELLER_LOCKED', 'У заявки уже выпущены документы. Для другой организации создайте отдельную заявку.', 409, 'seller_id')
    if body.commercial_stage and body.commercial_stage != row.commercial_stage:
        allowed = ['new', 'clarification', 'collecting_quotes', 'calculation', 'closed_lost']
        if body.commercial_stage not in allowed:
            raise DomainError('STAGE_ACTION_REQUIRED', 'Этот этап меняется при выполнении связанной бизнес-операции', 422, 'commercial_stage')
        if body.commercial_stage == 'closed_lost':
            if not body.loss_reason:
                raise DomainError('LOSS_REASON_REQUIRED', 'Укажите причину закрытия без продажи', 422, 'loss_reason')
            from app.commerce.models import Execution
            if db.scalar(select(Execution.id).where(Execution.request_id == row.id).limit(1)):
                raise DomainError('ACCEPTED_COMPOSITION_EXISTS', 'Сначала оформите согласованную отмену принятого состава', 409)
            row.closed_at = utcnow()
        elif row.closed_at:
            if not body.reason:
                raise DomainError('REOPEN_REASON_REQUIRED', 'Укажите причину повторного открытия', 422, 'reason')
            row.closed_at = None
    for k, v in body.model_dump(exclude_unset=True, exclude={'version', 'reason'}).items():
        setattr(row, k, v)
    row.version += 1
    audit(db, user, 'request', row.id, 'updated', before, serialize(row), body.reason)
    return serialize(row)


@router.put('/requests/{entity_id}/members')
def share_request(entity_id: str, body: ShareInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    check_request(db, user, entity_id, 'requests.assign')
    row = lock(db, Request, entity_id)
    check_version(row, body.version)
    for member in set(body.member_ids):
        active_user(db, member)
    db.execute(delete(RequestMember).where(RequestMember.request_id == entity_id))
    db.add_all([RequestMember(request_id=entity_id, user_id=member) for member in set(body.member_ids)])
    row.version += 1
    audit(db, user, 'request', row.id, 'access_changed', after={'members': body.member_ids}, reason=body.reason)
    return serialize(row)


@router.get('/requests/{entity_id}/items')
def items(entity_id: str, page: int = 1, page_size: int = 100, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    check_request(db, user, entity_id)
    return paginate(db, select(RequestItem).where(RequestItem.request_id == entity_id).order_by(RequestItem.created_at), page, page_size)


@router.post('/requests/{entity_id}/items', status_code=201)
def create_item(entity_id: str, body: ItemInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    check_request(db, user, entity_id, 'requests.write')
    parent = lock(db, Request, entity_id)
    row = RequestItem(request_id=entity_id, **body.model_dump())
    result = save(db, user, row, 'request_item')
    db.add(RequestItemRevision(item_id=row.id, revision=1, snapshot=result, author_id=user.id))
    parent.version += 1
    return result


@router.patch('/request-items/{entity_id}')
def edit_item(entity_id: str, body: ItemPatch, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    initial = db.get(RequestItem, entity_id)
    if not initial:
        raise DomainError('NOT_FOUND', 'Позиция не найдена', 404)
    check_request(db, user, initial.request_id, 'requests.write')
    parent = lock(db, Request, initial.request_id)
    row = lock(db, RequestItem, entity_id)
    check_version(row, body.version)
    before = serialize(row)
    for k, v in body.model_dump(exclude_unset=True, exclude={'version', 'reason'}).items():
        setattr(row, k, v)
    row.version += 1
    row.revision += 1
    parent.version += 1
    db.add(RequestItemRevision(item_id=row.id, revision=row.revision, snapshot=serialize(row), author_id=user.id, reason=body.reason))
    audit(db, user, 'request_item', row.id, 'revised', before, serialize(row), body.reason)
    return serialize(row)


@router.get('/request-items/{entity_id}/revisions')
def item_revisions(entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(RequestItem, entity_id)
    if not row:
        raise DomainError('NOT_FOUND', 'Позиция не найдена', 404)
    check_request(db, user, row.request_id)
    return paginate(db, select(RequestItemRevision).where(RequestItemRevision.item_id == entity_id).order_by(RequestItemRevision.revision.desc()), 1, 100)


@router.get('/requests/{entity_id}/history')
def history(entity_id: str, page: int = 1, page_size: int = 50, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    check_request(db, user, entity_id)
    ids = [entity_id, *db.scalars(select(RequestItem.id).where(RequestItem.request_id == entity_id))]
    result = paginate(db, select(AuditEvent).where(AuditEvent.entity_id.in_(ids)).order_by(AuditEvent.created_at.desc()), page, page_size)
    for row in result['items']:
        if not all(can(db, user, p) for p in ('finance.purchase.read', 'finance.calculations.read', 'finance.reward.read')):
            row.pop('before', None)
            row.pop('after', None)
    return result


@router.get('/sellers')
def sellers(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return paginate(db, select(Seller).where(Seller.archived.is_(False)).order_by(Seller.name), 1, 100)


@router.post('/sellers', status_code=201)
def create_seller(body: SellerInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'admin.settings')
    return save(db, user, Seller(**body.model_dump()), 'seller')
