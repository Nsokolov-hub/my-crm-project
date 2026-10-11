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
from app.core.security import check_client, client_predicate, current_user, require_permission
from app.core.service import audit, check_version, lock, serialize
from app.core.service import page as paginate
from app.crm.common import active_user, save
from app.crm.models import Contact, Counterparty, CounterpartyDocument, Request, Seller
from app.crm.schemas import (
    ClientInput,
    ClientPatch,
    ContactCreateInput,
    ContactInput,
    ContactPatch,
    CounterpartyDocumentPatch,
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
    for row in result['items']:
        row['owner_name'] = names.get(row['owner_id'])
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
    
    archive_cascade = body.archived is True and not row.archived
    if row.client_base == 'cold' and body.kind in ('supplier', 'both'):
        raise DomainError('WORKING_CLIENT_REQUIRED', 'Сначала переведите контрагента в рабочую базу', 422, 'kind')

    for k, v in body.model_dump(exclude_unset=True, exclude={'version', 'reason'}).items():
        setattr(row, k, v)
    row.version += 1
    
    if archive_cascade:
        # Archiving a counterparty also archives its requests; their items are left unchanged.
        requests = db.scalars(select(Request).where(Request.client_id == row.id, Request.archived.is_(False))).all()
        for req in requests:
            req_before = serialize(req)
            req.archived = True
            req.version += 1
            audit(db, user, 'request', req.id, 'updated', req_before, serialize(req), "Client archived cascade")

    audit(db, user, 'counterparty', row.id, 'updated', before, serialize(row), body.reason)
    return counterparty_view(db, row)


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
