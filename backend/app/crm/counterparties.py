"""Clients and suppliers, their documents and contacts, and the seller organizations."""

from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, Form, Header, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.communication.attachments import attach_file, file_download
from app.core.db import get_db
from app.core.errors import DomainError
from app.core.models import User
from app.core.security import (
    check_client,
    client_predicate,
    current_user,
    has_request_permission,
    require_permission,
)
from app.core.service import audit, check_version, idem, lock, serialize
from app.core.service import page as paginate
from app.crm.common import active_user, save
from app.crm.models import (
    Call,
    Contact,
    Counterparty,
    CounterpartyDocument,
    QuoteItem,
    QuoteSheet,
    Request,
    RequestItem,
    Seller,
    Task,
)
from app.crm.schemas import (
    ClientInput,
    ClientPatch,
    ContactCreateInput,
    ContactInput,
    ContactPatch,
    CounterpartyArchiveInput,
    CounterpartyDocumentPatch,
    CounterpartyMergeInput,
    PromoteClientInput,
    SellerInput,
    SellerUpdate,
)
from app.crm.text_search import search_parts

router = APIRouter(tags=['CRM'])


@router.get('/counterparties')
def clients(q: str = '', kind: str | None = None, client_base: str | None = None, contact_eligible: bool = False, page: int = 1, page_size: int = 25, sort: str = 'name', direction: str = 'asc', archived: bool = False, filter_name: str = '', filter_tax_id: str = '', filter_profile: str = '', filter_city: str = '', filter_phone: str = '', filter_email: str = '', user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    require_permission(db, user, 'clients.read')
    stmt = select(Counterparty).where(client_predicate(db, user), Counterparty.archived == archived)
    if contact_eligible:
        stmt = stmt.where(Counterparty.client_base == 'working', Counterparty.archived.is_(False))
    if kind:
        stmt = stmt.where(Counterparty.kind.in_([kind, 'both']))
    if client_base:
        if client_base not in ('cold', 'working'):
            raise DomainError('CLIENT_BASE_INVALID', 'Выберите рабочую базу или базу обзвона', 422)
        stmt = stmt.where(Counterparty.client_base == client_base)
    filters = {
        'name': filter_name, 'tax_id': filter_tax_id, 'profile': filter_profile,
        'city': filter_city, 'phone': filter_phone, 'email': filter_email,
    }
    col = {
        'name': Counterparty.name, 'tax_id': Counterparty.tax_id,
        'profile': Counterparty.details['profile'].as_string(),
        'city': Counterparty.details['city'].as_string(),
        'phone': Counterparty.phone, 'email': Counterparty.email,
        'created_at': Counterparty.created_at,
        'internal_code': Counterparty.internal_code,
    }.get(sort, Counterparty.name)
    stmt = stmt.order_by(col.desc() if direction == 'desc' else col, Counterparty.id)
    for part in search_parts(q):
        stmt = stmt.where(Counterparty.search_text.contains(part, autoescape=True))
    for key, value in filters.items():
        for part in search_parts(value):
            stmt = stmt.where(Counterparty.search_fields[key].as_string().contains(part, autoescape=True))
    return add_counterparty_owners(db, paginate(db, stmt, page, page_size))


def add_counterparty_owners(db: Session, result: dict) -> dict:
    names = {row.id: row.name for row in db.scalars(select(User).where(
        User.id.in_({row['owner_id'] for row in result['items']})))}
    merged_into = {row['merged_into_id'] for row in result['items'] if row.get('merged_into_id')}
    main_cards = dict(db.execute(select(Counterparty.id, Counterparty.name).where(
        Counterparty.id.in_(merged_into))).all()) if merged_into else {}
    for row in result['items']:
        row['owner_name'] = names.get(row['owner_id'])
        if row.get('merged_into_id'):
            row['merged_into_name'] = main_cards.get(row['merged_into_id'])
    return result


def counterparty_view(db: Session, row: Counterparty) -> dict:
    return add_counterparty_owners(db, {'items': [serialize(row)]})['items'][0]


@router.get('/counterparty-owners')
def counterparty_owners(user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    require_permission(db, user, 'clients.read')
    rows = db.scalars(select(User).where(User.active.is_(True)).order_by(User.name, User.id))
    return {'items': [{'id': row.id, 'name': row.name} for row in rows]}


@router.post('/counterparties', status_code=201)
def create_client(body: ClientInput, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    require_permission(db, user, 'clients.write')
    data = body.model_dump()
    data['owner_id'] = body.owner_id or user.id
    if data['owner_id'] != user.id:
        require_permission(db, user, 'requests.assign')
    active_user(db, data['owner_id'])
    result = save(db, user, Counterparty(**data), 'counterparty')
    return counterparty_view(db, db.get(Counterparty, result['id']))


@router.get('/counterparties/{entity_id}')
def client_detail(entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    return counterparty_view(db, check_client(db, user, entity_id))


@router.post('/counterparties/{entity_id}/promote')
def promote_client(entity_id: str, body: PromoteClientInput, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")):
    check_client(db, user, entity_id, 'clients.write')
    row = lock(db, Counterparty, entity_id)
    if row.archived:
        raise DomainError('CLIENT_ARCHIVED', 'Сначала восстановите клиента из архива', 409)
    if row.client_base == 'working':
        return counterparty_view(db, row)
    check_version(row, body.version)
    before = serialize(row)
    row.client_base = 'working'
    row.version += 1
    audit(db, user, 'counterparty', row.id, 'promoted', before, serialize(row), 'Перенос из базы обзвона в рабочую базу')
    return counterparty_view(db, row)


@router.patch('/counterparties/{entity_id}')
def edit_client(entity_id: str, body: ClientPatch, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    check_client(db, user, entity_id, 'clients.write')
    row = lock(db, Counterparty, entity_id)
    check_version(row, body.version)
    before = serialize(row)
    if 'owner_id' in body.model_fields_set and body.owner_id != row.owner_id:
        require_permission(db, user, 'requests.assign')
        active_user(db, body.owner_id)
        if not body.reason:
            raise DomainError('REASON_REQUIRED', 'Укажите причину переназначения', 422, 'reason')
    if row.client_base == 'cold' and body.kind in ('supplier', 'both'):
        raise DomainError('WORKING_CLIENT_REQUIRED', 'Сначала переведите контрагента в рабочую базу', 422, 'kind')

    for k, v in body.model_dump(exclude_unset=True, exclude={'version', 'reason'}).items():
        setattr(row, k, v)
    row.version += 1
    audit(db, user, 'counterparty', row.id, 'updated', before, serialize(row), body.reason)
    return counterparty_view(db, row)


def requests_in_work(db: Session, counterparty_id: str) -> list[Request]:
    """Requests of a counterparty that are neither archived nor finished.

    A request is finished when it is closed without a sale, or when its sale is confirmed and every
    position is delivered or cancelled.
    """
    from app.commerce.fulfillment import execution_view
    from app.commerce.models import Execution

    def finished(row: Request) -> bool:
        if row.commercial_stage == 'closed_lost':
            return True
        if row.commercial_stage not in ('sale_confirmed', 'won'):
            return False
        return all(execution_view(db, execution)['state'] in ('fulfilled', 'cancelled')
                   for execution in db.scalars(select(Execution).where(Execution.request_id == row.id)))

    rows = db.scalars(select(Request).where(Request.client_id == counterparty_id, Request.archived.is_(False))
                      .order_by(Request.created_at, Request.id)).all()
    return [row for row in rows if not finished(row)]


def supplier_work(db: Session, counterparty_id: str) -> list[str]:
    """Waves and orders of a supplier that are still under way."""
    from app.business.models import SupplierOrder, SupplierOrderLine
    from app.commerce.models import Wave

    waves = db.scalars(select(Wave.number).where(
        Wave.supplier_id == counterparty_id, Wave.archived.is_(False), Wave.status.not_in(('completed', 'cancelled')),
    ).order_by(Wave.number)).all()
    orders = db.scalars(select(SupplierOrder.number).where(
        SupplierOrder.supplier_id == counterparty_id,
        SupplierOrder.id.in_(select(SupplierOrderLine.order_id).where(SupplierOrderLine.status != 'delivered')),
    ).order_by(SupplierOrder.number)).all()
    return [f'волна «{number}»' for number in waves] + [f'заказ поставщику {number}' for number in orders]


def has_supplier_records(db: Session, counterparty_id: str) -> bool:
    """Quotations, requests and orders of a supplier are recorded facts and cannot move to another card."""
    from app.business.models import SupplierMail, SupplierOrder
    from app.commerce.models import Quote, SupplierRequest, Wave

    columns = (QuoteSheet.supplier_id, QuoteItem.supplier_id, Quote.supplier_id, SupplierRequest.supplier_id,
               SupplierOrder.supplier_id, SupplierMail.supplier_id, Wave.supplier_id, RequestItem.supplier_id)
    return any(db.scalar(select(column).where(column == counterparty_id).limit(1)) for column in columns)


@router.post('/counterparties/{entity_id}/archive')
def archive_counterparty(entity_id: str, body: CounterpartyArchiveInput, user: User = Depends(current_user),
                         db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    check_client(db, user, entity_id, 'clients.write')

    def operation():
        row = lock(db, Counterparty, entity_id)
        check_version(row, body.version)
        if row.merged_into_id:
            main = db.get(Counterparty, row.merged_into_id)
            raise DomainError('COUNTERPARTY_MERGED', f'Контрагент объединён с «{main.name}». Работайте с этой карточкой', 409)
        if row.archived == body.archived:
            raise DomainError('ARCHIVE_STATE', 'Контрагент уже в архиве' if row.archived else 'Контрагент не в архиве', 409)
        if body.archived:
            busy = [f'заявка №{req.number}' for req in requests_in_work(db, row.id)] + supplier_work(db, row.id)
            if busy:
                listed = ', '.join(busy[:5]) + (f' и ещё {len(busy) - 5}' if len(busy) > 5 else '')
                raise DomainError('COUNTERPARTY_IN_WORK', f'С контрагентом ещё идёт работа: {listed}. Завершите её, '
                                  'а дубль объедините с основной карточкой', 409)
            requests = db.scalars(select(Request).where(Request.client_id == row.id, Request.archived.is_(False))).all()
        else:
            requests = db.scalars(select(Request).where(Request.client_id == row.id,
                                                        Request.archived_with_client.is_(True))).all()
        # Finished requests leave the lists with their client and come back with it.
        for req in requests:
            req_before = serialize(req)
            req.archived = req.archived_with_client = body.archived
            req.version += 1
            audit(db, user, 'request', req.id, 'archived' if body.archived else 'restored', req_before,
                  serialize(req), f'Вместе с клиентом «{row.name}»: {body.reason}')
        before = serialize(row)
        row.archived = body.archived
        row.version += 1
        audit(db, user, 'counterparty', row.id, 'archived' if body.archived else 'restored', before,
              serialize(row), body.reason)
        return counterparty_view(db, row)

    return idem(db, user, body.idempotency_key, f'archive-counterparty:{entity_id}', body.model_dump(mode='json'),
                operation)


@router.post('/counterparties/{entity_id}/merge')
def merge_counterparty(entity_id: str, body: CounterpartyMergeInput, user: User = Depends(current_user),
                       db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    """Moves the working records of a duplicate to the main card and archives the duplicate.

    Issued documents, payments and supplier quotations are recorded facts: they keep the duplicate,
    which then stands for the main card (see app.crm.common.party_root).
    """
    require_permission(db, user, 'requests.assign')
    check_client(db, user, entity_id, 'clients.write')
    if body.target_id == entity_id:
        raise DomainError('MERGE_TARGET', 'Выберите другую карточку: контрагента нельзя объединить с самим собой',
                          422, 'target_id')
    check_client(db, user, body.target_id, 'clients.write')

    def operation():
        for key in sorted((entity_id, body.target_id)):
            lock(db, Counterparty, key)
        row, target = db.get(Counterparty, entity_id), db.get(Counterparty, body.target_id)
        check_version(row, body.version)
        if row.archived:
            raise DomainError('COUNTERPARTY_ARCHIVED', 'Дубль в архиве. Верните его из архива, чтобы объединить', 409)
        if target.archived:
            raise DomainError('MERGE_TARGET', 'Основная карточка в архиве. Выберите действующего контрагента',
                              422, 'target_id')
        for role, kinds in (('клиентом', ('client', 'both')), ('поставщиком', ('supplier', 'both'))):
            if row.kind in kinds and target.kind not in kinds:
                raise DomainError('MERGE_TARGET', f'Основная карточка должна быть {role}, как и дубль. Измените её тип',
                                  422, 'target_id')
        if has_supplier_records(db, row.id):
            raise DomainError('MERGE_SUPPLIER_HISTORY', 'У дубля есть квоты, запросы или заказы как у поставщика, их '
                              'нельзя перенести. Уберите дубль в архив', 409)
        requests = db.scalars(select(Request).where(Request.client_id == row.id).order_by(Request.created_at)).all()
        if requests and target.client_base != 'working':
            raise DomainError('MERGE_TARGET', 'Основная карточка в базе обзвона. Сначала переведите её в рабочую базу',
                              422, 'target_id')
        hidden = next((req for req in requests if not has_request_permission(db, user, req.id, 'requests.write')), None)
        if hidden:
            raise DomainError('MERGE_FORBIDDEN', f'Нет доступа к заявке №{hidden.number} дубля', 403)
        note = (f'Объединение дубля «{row.name}» (код {row.internal_code}) с «{target.name}» '
                f'(код {target.internal_code}): {body.reason}')
        for req in requests:
            req_before = serialize(req)
            req.client_id = target.id
            req.version += 1
            audit(db, user, 'request', req.id, 'client_merged', req_before, serialize(req), note)
        moved = {'requests': len(requests)}
        for name, model, column, values in (
            ('contacts', Contact, Contact.client_id, {Contact.client_id: target.id}),
            ('calls', Call, Call.client_id, {Call.client_id: target.id}),
            ('documents', CounterpartyDocument, CounterpartyDocument.counterparty_id,
             {CounterpartyDocument.counterparty_id: target.id}),
            ('tasks', Task, Task.entity_id, {Task.entity_id: target.id}),
        ):
            condition = column == row.id
            if model is Task:
                condition = condition & (Task.entity_type == 'counterparty')
            moved[name] = db.execute(sa.update(model).where(condition)
                                     .values({**values, model.version: model.version + 1})).rowcount
        from app.business.models import CalendarEntry
        from app.communication.models import FileRecord
        db.execute(sa.update(FileRecord).where(FileRecord.client_id == row.id)
                   .values({FileRecord.client_id: target.id, FileRecord.version: FileRecord.version + 1}))
        db.execute(sa.update(CalendarEntry).where(CalendarEntry.counterparty_id == row.id)
                   .values({CalendarEntry.counterparty_id: target.id, CalendarEntry.version: CalendarEntry.version + 1}))
        target_before, before = serialize(target), serialize(row)
        # The main card keeps its own data and takes from the duplicate only what it lacks.
        for field in ('tax_id', 'phone', 'email', 'country'):
            if not getattr(target, field) and getattr(row, field):
                setattr(target, field, getattr(row, field))
        target.details = {**(row.details or {}),
                          **{key: value for key, value in (target.details or {}).items() if value not in (None, '')}}
        target.version += 1
        row.archived, row.merged_into_id = True, target.id
        row.version += 1
        audit(db, user, 'counterparty', target.id, 'merged', target_before, serialize(target), note)
        audit(db, user, 'counterparty', row.id, 'merged_into', before, serialize(row), note)
        return {**counterparty_view(db, target), 'moved': moved}

    return idem(db, user, body.idempotency_key, f'merge-counterparty:{entity_id}', body.model_dump(mode='json'),
                operation)


def counterparty_document_view(db: Session, user: User, row: CounterpartyDocument) -> dict:
    from app.communication.access import check_file, file_view
    file = file_view(check_file(db, user, row.file_id))
    return {**serialize(row), **{key: file[key] for key in ('name', 'media_type', 'size', 'status')}}


@router.get('/counterparties/{entity_id}/documents')
def counterparty_documents(entity_id: str, archived: bool = False, page: int = 1, page_size: int = 100,
                           user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    check_client(db, user, entity_id)
    result = paginate(db, select(CounterpartyDocument).where(
        CounterpartyDocument.counterparty_id == entity_id, CounterpartyDocument.archived == archived
    ).order_by(CounterpartyDocument.created_at.desc(), CounterpartyDocument.id), page, page_size)
    result['items'] = [counterparty_document_view(db, user, db.get(CounterpartyDocument, row['id']))
                       for row in result['items']]
    return result


@router.post('/counterparties/{entity_id}/documents', status_code=201)
def upload_counterparty_document(entity_id: str, file: UploadFile = File(), category: str = Form('other'),
                                 idempotency_key: str | None = Header(default=None),
                                 user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    client = check_client(db, user, entity_id, 'clients.write')
    if client.archived:
        raise DomainError('CLIENT_ARCHIVED', 'Контрагент в архиве', 409)
    if category not in ('founding', 'contract', 'other'):
        raise DomainError('DOCUMENT_CATEGORY_INVALID', 'Выберите категорию документа', 422, 'category')
    saved = attach_file(db, user, file, 'client', entity_id, 'general', idempotency_key)
    row = db.scalar(select(CounterpartyDocument).where(CounterpartyDocument.file_id == saved['id']))
    if row:
        if row.category != category:
            raise DomainError('IDEMPOTENCY_CONFLICT', 'Этот ключ использован для другой категории', 409)
        return counterparty_document_view(db, user, row)
    row = CounterpartyDocument(counterparty_id=entity_id, file_id=saved['id'], category=category)
    save(db, user, row, 'counterparty_document')
    return counterparty_document_view(db, user, row)


@router.get('/counterparty-documents/{entity_id}/download')
def download_counterparty_document(entity_id: str, user: User = Depends(current_user),
                                   db: Session = Depends(get_db, scope="function")):
    row = db.get(CounterpartyDocument, entity_id)
    if not row:
        raise DomainError('NOT_FOUND', 'Документ не найден', 404)
    check_client(db, user, row.counterparty_id)
    return file_download(db, user, row.file_id)


@router.patch('/counterparty-documents/{entity_id}')
def archive_counterparty_document(entity_id: str, body: CounterpartyDocumentPatch,
                                  user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    row = lock(db, CounterpartyDocument, entity_id)
    check_client(db, user, row.counterparty_id, 'clients.write')
    check_version(row, body.version)
    before = serialize(row)
    row.archived = body.archived
    row.version += 1
    audit(db, user, 'counterparty_document', row.id, 'archived' if body.archived else 'restored',
          before, serialize(row), body.reason)
    return counterparty_document_view(db, user, row)


@router.get('/counterparties/{entity_id}/contacts')
def contacts(entity_id: str, q: str = '', user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    check_client(db, user, entity_id)
    stmt = select(Contact).where(Contact.client_id == entity_id, Contact.archived.is_(False))
    if q:
        stmt = stmt.where(sa.or_(Contact.name.ilike(f'%{q}%'), Contact.email.ilike(f'%{q}%')))
    return paginate(db, stmt.order_by(Contact.name), 1, 100)


@router.post('/counterparties/{entity_id}/contacts', status_code=201)
def create_contact(entity_id: str, body: ContactInput, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    client = check_client(db, user, entity_id, 'clients.write')
    if client.client_base != 'working' or client.archived:
        raise DomainError('WORKING_CLIENT_REQUIRED', 'Выберите действующего контрагента рабочей базы', 422)
    return save(db, user, Contact(client_id=entity_id, **body.model_dump()), 'contact')


@router.get('/contacts')
def contact_registry(q: str = '', page: int = 1, page_size: int = 25, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")):
    require_permission(db, user, 'clients.read')
    accessible = select(Counterparty.id).where(client_predicate(db, user), Counterparty.client_base == 'working', Counterparty.archived.is_(False))
    stmt = select(Contact).where(Contact.client_id.in_(accessible), Contact.archived.is_(False))
    if q:
        stmt = stmt.where(or_(Contact.name.ilike(f'%{q}%'), Contact.department.ilike(f'%{q}%'), Contact.purchase_area.ilike(f'%{q}%'), Contact.email.ilike(f'%{q}%')))
    result = paginate(db, stmt.order_by(Contact.name, Contact.id), page, page_size)
    names = {row.id: row.name for row in db.scalars(select(Counterparty).where(Counterparty.id.in_({r['client_id'] for r in result['items']})))}
    for row in result['items']:
        row['client_name'] = names.get(row['client_id'])
    return result


@router.post('/contacts', status_code=201)
def create_registry_contact(body: ContactCreateInput, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")):
    return create_contact(body.client_id, ContactInput(**body.model_dump(exclude={'client_id'})), user, db)


@router.get('/contacts/{entity_id}')
def contact_detail(entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")):
    row = db.get(Contact, entity_id)
    if not row:
        raise DomainError('NOT_FOUND', 'Контакт не найден', 404)
    client = check_client(db, user, row.client_id)
    return {**serialize(row), 'client_name': client.name}


@router.patch('/contacts/{entity_id}')
def edit_contact(entity_id: str, body: ContactPatch, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    row = lock(db, Contact, entity_id)
    check_client(db, user, row.client_id, 'clients.write')
    check_version(row, body.version)
    before = serialize(row)
    for k, v in body.model_dump(exclude_unset=True, exclude={'version', 'reason'}).items():
        setattr(row, k, v)
    row.version += 1
    audit(db, user, 'contact', row.id, 'updated', before, serialize(row), body.reason)
    return serialize(row)


@router.get('/sellers')
def sellers(user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    return paginate(db, select(Seller).where(Seller.archived.is_(False)).order_by(Seller.name), 1, 100)


@router.post('/sellers', status_code=201)
def create_seller(body: SellerInput, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    require_permission(db, user, 'admin.settings')
    return save(db, user, Seller(**body.model_dump()), 'seller')


@router.patch('/sellers/{entity_id}')
def update_seller(entity_id: str, body: SellerUpdate, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    require_permission(db, user, 'admin.settings')
    row = lock(db, Seller, entity_id)
    check_version(row, body.version)
    if row.archived:
        raise DomainError('SELLER_ARCHIVED', 'Организация находится в архиве', 422)
    before = serialize(row)
    for field, value in body.model_dump(exclude={'version'}).items():
        setattr(row, field, value)
    row.version += 1
    audit(db, user, 'seller', row.id, 'update', before=before, after=serialize(row))
    return serialize(row)
