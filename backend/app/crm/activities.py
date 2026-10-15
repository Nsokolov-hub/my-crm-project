"""Tasks, calls and the call result dictionary."""

from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db, utcnow
from app.core.errors import DomainError
from app.core.models import AppSetting, User
from app.core.security import (
    can,
    check_client,
    check_request,
    client_predicate,
    current_user,
    require_permission,
    scope_for,
    task_predicate,
    users_with_permission,
)
from app.core.service import audit, check_version, idem, lock, notify, serialize
from app.core.service import page as paginate
from app.crm.common import active_user, contact_matches, save
from app.crm.models import Call, Counterparty, Request, Task
from app.crm.schemas import BulkCallTaskInput, CallInput, CallPatch, TaskInput, TaskPatch

router = APIRouter(tags=['CRM'])


CALL_RESULTS = (
    ('not_interested', 'Не интересны'),
    ('presentation_sent', 'Отправлена презентация'),
    ('awaiting_request', 'Ждём запрос'),
    ('request_received', 'Получен запрос'),
    ('invalid_contact', 'Неактуальный контакт'),
)


CALL_RESULT_LABELS = dict(CALL_RESULTS)


@router.get('/dictionaries/{key}')
def dictionary(key: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    dictionaries = {
        'call_results': ('results', 'calls.write'),
        'loss_reasons': ('reasons', 'requests.write'),
    }
    if key not in dictionaries:
        raise DomainError('NOT_FOUND', 'Справочник не найден', 404)
    value_key, permission = dictionaries[key]
    require_permission(db, user, permission)
    if key == 'call_results':
        items = [{'id': value, 'name': label} for value, label in CALL_RESULTS]
        return {'items': items, 'total': len(items)}
    setting = db.scalar(select(AppSetting).where(AppSetting.key == key, AppSetting.status == 'published'))
    if not setting:
        raise DomainError('SETUP_REQUIRED', 'Справочник не настроен. Обратитесь к администратору.', 409)
    labels = {
        'no_answer': 'Не дозвонились', 'callback': 'Перезвонить', 'interested': 'Есть интерес',
        'request_received': 'Получен запрос', 'rejected': 'Отказ', 'invalid_contact': 'Неверный контакт',
        'not_interested': 'Нет интереса', 'wrong_number': 'Неверный номер',
        'meeting_scheduled': 'Назначена встреча', 'price_too_high': 'Высокая цена',
        'went_to_competitor': 'Выбран конкурент', 'no_budget': 'Нет бюджета',
        'timing': 'Не подходят сроки', 'other': 'Другая причина',
    }
    values = setting.value.get(value_key, [])
    items = [{'id': value, 'name': labels.get(value, value)} for value in values if isinstance(value, str)]
    return {'items': items, 'total': len(items)}


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


OPEN_TASK_STATUSES = ('assigned', 'in_progress')
TASK_STATUS_LABELS = {'assigned': 'Назначена', 'in_progress': 'В работе', 'completed': 'Завершена', 'cancelled': 'Отменена'}
TASK_PRIORITY_LABELS = {'high': 'Высокий', 'normal': 'Обычный', 'low': 'Низкий'}
# Due groups in the order a person works through them; closed tasks come last.
DUE_GROUPS = {'overdue': 'Просрочено', 'today': 'Сегодня', 'tomorrow': 'Завтра', 'week': 'На этой неделе',
              'later': 'Позже', 'closed': 'Завершены и отменены'}
TASK_GROUPS = ('due', 'assignee', 'client', 'status', 'priority')


def day_starts(now: datetime) -> dict[str, datetime]:
    """Starts of today, tomorrow, the day after and a week from today in the company time zone, in UTC."""
    zone = ZoneInfo(settings.company_timezone)
    today = datetime.combine(now.astimezone(zone).date(), time.min, zone)
    return {key: (today + timedelta(days=days)).astimezone(timezone.utc)
            for key, days in (('today', 0), ('tomorrow', 1), ('after_tomorrow', 2), ('week', 7))}


def due_group(task: Task, now: datetime, starts: dict[str, datetime]) -> str:
    if task.status not in OPEN_TASK_STATUSES:
        return 'closed'
    due = task.due_at if task.due_at.tzinfo else task.due_at.replace(tzinfo=timezone.utc)
    if due < now:
        return 'overdue'
    if due < starts['tomorrow']:
        return 'today'
    if due < starts['after_tomorrow']:
        return 'tomorrow'
    return 'week' if due < starts['week'] else 'later'


@router.get('/tasks')
def tasks(status: str | None = None, q: str = '', entity_id: str | None = None, overdue: bool = False,
          assignee_id: str | None = None, priority: str | None = None, due: str | None = None,
          client_id: str | None = None, group: str | None = None, page: int = 1, page_size: int = 25,
          user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    require_permission(db, user, 'tasks.read')
    if group and group not in TASK_GROUPS:
        raise DomainError('TASK_GROUP', 'Неизвестная группировка задач', 422, 'group')
    if due and due not in DUE_GROUPS:
        raise DomainError('TASK_DUE', 'Неизвестный период срока', 422, 'due')
    now = utcnow()
    starts = day_starts(now)
    stmt = select(Task).where(task_predicate(db, user))
    if status == 'active':
        stmt = stmt.where(Task.status.in_(OPEN_TASK_STATUSES))
    elif status:
        stmt = stmt.where(Task.status == status)
    if q:
        stmt = stmt.where(Task.title.ilike(f'%{q}%'))
    if entity_id:
        stmt = stmt.where(Task.entity_id == entity_id)
    if overdue:
        stmt = stmt.where(Task.due_at < now, Task.status.in_(OPEN_TASK_STATUSES))
    if assignee_id:
        stmt = stmt.where(Task.assignee_id == assignee_id)
    if priority:
        stmt = stmt.where(Task.priority == priority)
    if client_id:
        stmt = stmt.where(or_(
            (Task.entity_type == 'counterparty') & (Task.entity_id == client_id),
            (Task.entity_type == 'request') & Task.entity_id.in_(select(Request.id).where(Request.client_id == client_id)),
        ))
    if due == 'closed':
        stmt = stmt.where(Task.status.not_in(OPEN_TASK_STATUSES))
    elif due:
        bounds = {'overdue': (None, now), 'today': (now, starts['tomorrow']),
                  'tomorrow': (starts['tomorrow'], starts['after_tomorrow']),
                  'week': (starts['after_tomorrow'], starts['week']), 'later': (starts['week'], None)}[due]
        stmt = stmt.where(Task.status.in_(OPEN_TASK_STATUSES))
        if bounds[0] is not None:
            stmt = stmt.where(Task.due_at >= bounds[0])
        if bounds[1] is not None:
            stmt = stmt.where(Task.due_at < bounds[1])
    if not group:
        result = paginate(db, stmt.order_by(Task.due_at, Task.id), page, page_size)
        rows = [db.get(Task, item['id']) for item in result['items']]
    else:
        if page < 1 or not 1 <= page_size <= 100:
            raise DomainError('PAGINATION_INVALID', 'Размер страницы от 1 до 100; номер от 1', 422)
        everything = db.scalars(stmt.order_by(Task.due_at, Task.id)).all()
        names = task_names(db, everything)
        keys = {task.id: task_group(task, group, names, now, starts) for task in everything}
        order = group_order(group, keys.values(), names)
        everything = sorted(everything, key=lambda task: order.index(keys[task.id]))
        counts = {key: 0 for key in order}
        for key in keys.values():
            counts[key] += 1
        rows = everything[(page - 1) * page_size:page * page_size]
        result = {'items': [serialize(task) for task in rows], 'total': len(everything), 'page': page,
                  'page_size': page_size,
                  'groups': [{'key': key, 'label': group_label(group, key, names), 'count': counts[key]}
                             for key in order]}
    names = task_names(db, rows)
    for item, task in zip(result['items'], rows, strict=True):
        item['client_id'] = names['client_of'].get(task.id)
        item['client_name'] = names['clients'].get(item['client_id'])
        item['request_number'] = names['request_numbers'].get(task.entity_id) if task.entity_type == 'request' else None
        item['assignee_name'] = names['users'].get(task.assignee_id)
        item['due_group'] = due_group(task, now, starts)
        if group:
            item['group'] = task_group(task, group, names, now, starts)
    return result


def task_names(db: Session, rows) -> dict[str, dict]:
    """Assignee and client names of tasks; a request task belongs to the request's client."""
    request_ids = {task.entity_id for task in rows if task.entity_type == 'request'}
    requests = {row.id: row for row in db.scalars(select(Request).where(Request.id.in_(request_ids)))}
    client_of = {}
    for task in rows:
        if task.entity_type == 'counterparty':
            client_of[task.id] = task.entity_id
        elif task.entity_type == 'request' and task.entity_id in requests:
            client_of[task.id] = requests[task.entity_id].client_id
    clients = {row.id: row.name for row in db.scalars(select(Counterparty).where(Counterparty.id.in_(set(client_of.values()))))}
    users = {row.id: row.name for row in db.scalars(select(User).where(User.id.in_({task.assignee_id for task in rows})))}
    return {'client_of': client_of, 'clients': clients, 'users': users,
            'request_numbers': {key: row.number for key, row in requests.items()}}


def task_group(task: Task, group: str, names: dict, now: datetime, starts: dict) -> str:
    if group == 'due':
        return due_group(task, now, starts)
    if group == 'assignee':
        return task.assignee_id
    if group == 'client':
        return names['client_of'].get(task.id) or ''
    return task.status if group == 'status' else task.priority


def group_order(group: str, keys, names: dict) -> list[str]:
    present = set(keys)
    if group == 'due':
        return [key for key in DUE_GROUPS if key in present]
    if group == 'status':
        return [key for key in TASK_STATUS_LABELS if key in present] + sorted(present - TASK_STATUS_LABELS.keys())
    if group == 'priority':
        return [key for key in TASK_PRIORITY_LABELS if key in present] + sorted(present - TASK_PRIORITY_LABELS.keys())
    # Named groups go alphabetically; tasks without an assignee or a client come last.
    return sorted(present, key=lambda key: (key == '', group_label(group, key, names).casefold()))


def group_label(group: str, key: str, names: dict) -> str:
    if group == 'due':
        return DUE_GROUPS[key]
    if group == 'assignee':
        return names['users'].get(key) or 'Без исполнителя'
    if group == 'client':
        return names['clients'].get(key) or 'Без клиента'
    labels = TASK_STATUS_LABELS if group == 'status' else TASK_PRIORITY_LABELS
    return labels.get(key, key)


@router.post('/tasks', status_code=201)
def create_task(body: TaskInput, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    require_permission(db, user, 'tasks.write')
    check_link(db, user, body.entity_type, body.entity_id)
    assignee = active_user(db, body.assignee_id or user.id)
    from app.business.absences import ensure_available
    ensure_available(db, assignee.id)
    check_link(db, assignee, body.entity_type, body.entity_id)
    row = Task(**body.model_dump(exclude={'assignee_id'}), assignee_id=assignee.id, author_id=user.id)
    result = save(db, user, row, 'task')
    notify(db, assignee.id, f'task:{row.id}', 'Вам назначена задача', 'task', row.id)
    return result


@router.post('/tasks/bulk-calls', status_code=201)
def create_bulk_call_tasks(body: BulkCallTaskInput, idempotency_key: str | None = Header(default=None), user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    require_permission(db, user, 'tasks.write')
    require_permission(db, user, 'requests.assign')
    if len(set(body.client_ids)) != len(body.client_ids):
        raise DomainError('CLIENT_DUPLICATE', 'Уберите повторяющихся клиентов', 422, 'client_ids')
    assignee = active_user(db, body.assignee_id)
    from app.business.absences import ensure_available
    ensure_available(db, assignee.id)
    if not can(db, assignee, 'clients.read') or not can(db, assignee, 'tasks.read'):
        raise DomainError('ASSIGNEE_ACCESS_REQUIRED', 'У сотрудника должны быть права просмотра клиентов и задач', 422, 'assignee_id')
    clients_to_assign = [check_client(db, user, client_id, 'clients.write') for client_id in body.client_ids]
    if any(client.archived or client.kind == 'supplier' for client in clients_to_assign):
        raise DomainError('CLIENT_INVALID', 'Выберите действующих клиентов для обзвона', 422, 'client_ids')

    def operation() -> dict[str, Any]:
        task_ids = []
        for client in clients_to_assign:
            if client.owner_id != assignee.id:
                before = serialize(client)
                client.owner_id = assignee.id
                client.version += 1
                audit(db, user, 'counterparty', client.id, 'assigned', before, serialize(client))
            task = Task(title=body.title, entity_type='counterparty', entity_id=client.id,
                        assignee_id=assignee.id, author_id=user.id, due_at=body.due_at,
                        priority=body.priority)
            save(db, user, task, 'task')
            notify(db, assignee.id, f'task:{task.id}', f'Вам назначен обзвон: {client.name}'[:250], 'task', task.id)
            task_ids.append(task.id)
        return {'count': len(task_ids), 'task_ids': task_ids}

    return idem(db, user, idempotency_key, 'tasks.bulk_calls', body.model_dump(mode='json'), operation)


@router.patch('/tasks/{entity_id}')
def edit_task(entity_id: str, body: TaskPatch, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
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
    from app.business.absences import ensure_available
    from app.business.common import local_date
    from app.business.reviews import submit_review
    if body.assignee_id:
        ensure_available(db, body.assignee_id)
    late = local_date(utcnow()) > local_date(row.due_at)
    if late and body.due_at and not can(db, user, "approvals.decide"):
        raise DomainError("TASK_DEADLINE_APPROVAL", "Перенос просроченной задачи выполняет руководитель", 422)
    before = serialize(row)
    for k, v in body.model_dump(exclude_unset=True, exclude={'version'}).items():
        setattr(row, k, v)
    if body.status in ('completed', 'cancelled'):
        row.completed_at = utcnow()
    row.version += 1
    if late and body.status in ("completed", "cancelled") and not can(db, user, "approvals.decide"):
        row.status = "completion_pending"
        row.completed_at = None
        submit_review(db, user, "task", row, "Закрытие просроченной задачи: " + row.title,
                      {**serialize(row), "requested_status": body.status}, row.entity_id if row.entity_type == "request" else None)
    audit(db, user, 'task', row.id, 'updated', before, serialize(row), body.result)
    return serialize(row)


def valid_call(db: Session, result: str, next_at: Any, reason: str | None) -> None:
    if result not in CALL_RESULT_LABELS:
        raise DomainError('CALL_RESULT_INVALID', 'Выберите результат из справочника', 422, 'result')
    if next_at and next_at.tzinfo is None:
        raise DomainError('TIMEZONE_REQUIRED', 'Укажите часовой пояс даты', 422, 'next_at')


@router.get('/calls')
def calls(client_id: str | None = None, page: int = 1, page_size: int = 25, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    require_permission(db, user, 'clients.read')
    stmt = select(Call).where(Call.client_id.in_(select(Counterparty.id).where(client_predicate(db, user))))
    if client_id:
        check_client(db, user, client_id)
        stmt = stmt.where(Call.client_id == client_id)
    result = paginate(db, stmt.order_by(Call.occurred_at.desc(), Call.id.desc()), page, page_size)
    author_ids = {item['author_id'] for item in result['items']}
    client_ids = {item['client_id'] for item in result['items']}
    authors = {row.id: row.name for row in db.scalars(select(User).where(User.id.in_(author_ids)))}
    clients = {row.id: row.name for row in db.scalars(select(Counterparty).where(Counterparty.id.in_(client_ids)))}
    for item in result['items']:
        item['author_name'] = authors.get(item['author_id'], 'Сотрудник удалён')
        item['client_name'] = clients.get(item['client_id'], 'Клиент удалён')
    return result


def notify_call_result(db: Session, row: Call, version: int | None = None) -> None:
    client = db.get(Counterparty, row.client_id)
    author = db.get(User, row.author_id)
    title = f'{author.name if author else "Сотрудник"}: {client.name if client else "Клиент"} — {CALL_RESULT_LABELS.get(row.result, row.result)}'[:250]
    key = f'call:{row.id}' if version is None else f'call:{row.id}:v{version}'
    for leader in users_with_permission(db, 'approvals.decide'):
        notify(db, leader.id, key, title, 'counterparty', row.client_id)


@router.post('/calls', status_code=201)
def create_call(body: CallInput, idempotency_key: str | None = Header(default=None), user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    require_permission(db, user, 'calls.write')
    check_client(db, user, body.client_id, 'clients.write')
    contact_matches(db, body.contact_id, body.client_id)
    valid_call(db, body.result, body.next_at, body.reason)
    def operation() -> dict[str, Any]:
        row = Call(**body.model_dump(exclude={'next_assignee_id', 'occurred_at'}), author_id=user.id, occurred_at=body.occurred_at or utcnow())
        if body.next_at:
            assignee = active_user(db, body.next_assignee_id or user.id)
            check_client(db, assignee, body.client_id)
            existing_task = db.scalar(select(Task).where(
                Task.entity_type == 'counterparty',
                Task.entity_id == body.client_id,
                Task.status.in_(['assigned', 'in_progress'])
            ).order_by(Task.due_at.desc()).limit(1))
            
            if existing_task:
                existing_task.due_at = body.next_at
                existing_task.assignee_id = assignee.id
                existing_task.version += 1
                db.add(existing_task)
                row.task_id = existing_task.id
                notify(db, assignee.id, f'task:{existing_task.id}', 'Время звонка перенесено', 'task', existing_task.id)
            else:
                from app.business.absences import ensure_available
                ensure_available(db, assignee.id)
                task = Task(title='Связаться с клиентом', entity_type='counterparty', entity_id=body.client_id, author_id=user.id, assignee_id=assignee.id, due_at=body.next_at)
                db.add(task)
                db.flush()
                row.task_id = task.id
                notify(db, assignee.id, f'task:{task.id}', 'Запланирован следующий контакт', 'task', task.id)
        result = save(db, user, row, 'call')
        notify_call_result(db, row)
        return result
    return idem(db, user, idempotency_key, 'calls.create', body.model_dump(mode='json'), operation)


@router.patch('/calls/{entity_id}')
def edit_call(entity_id: str, body: CallPatch, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
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
    notify_call_result(db, row, row.version)
    return serialize(row)
