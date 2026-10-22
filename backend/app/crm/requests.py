"""Requests, their lines, sharing and history."""

from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db, utcnow
from app.core.errors import DomainError
from app.core.models import AppSetting, AuditEvent, User
from app.core.security import (
    check_client,
    check_request,
    current_user,
    has_request_permission,
    request_predicate,
    require_permission,
    task_predicate,
)
from app.core.service import audit, check_version, idem, lock, prefetch, serialize
from app.core.service import page as paginate
from app.crm.catalogue import require_currency, require_group, require_nomenclature, require_packing
from app.crm.codes import next_request_number
from app.crm.common import active_user, contact_matches, require_supplier, save
from app.crm.models import (
    Call,
    Contact,
    Counterparty,
    Country,
    Currency,
    Nomenclature,
    Packing,
    ProductGroup,
    QuoteItem,
    QuoteSheet,
    Request,
    RequestItem,
    RequestItemRevision,
    RequestMember,
    Seller,
    Task,
)
from app.crm.schemas import ItemInput, ItemPatch, RequestInput, RequestPatch, ShareInput, StartItemsInput

router = APIRouter(tags=['CRM'])


def prefetch_item_relations(db: Session, rows: list[RequestItem]) -> None:
    prefetch(db, Nomenclature, (row.nomenclature_id for row in rows))
    prefetch(db, Packing, (row.packing_id for row in rows))
    prefetch(db, ProductGroup, (row.product_group_id for row in rows))
    prefetch(db, Counterparty, (row.supplier_id for row in rows))
    prefetch(db, Currency, (row.purchase_currency_id for row in rows))


def request_item_view(db: Session, user: User, row: RequestItem, show_purchase: bool | None = None) -> dict[str, Any]:
    value = serialize(row)
    nomenclature = db.get(Nomenclature, row.nomenclature_id) if row.nomenclature_id else None
    packing = db.get(Packing, row.packing_id) if row.packing_id else None
    group = db.get(ProductGroup, row.product_group_id) if row.product_group_id else None
    supplier = db.get(Counterparty, row.supplier_id) if row.supplier_id else None
    currency = db.get(Currency, row.purchase_currency_id) if row.purchase_currency_id else None
    value.update({
        'nomenclature_name': nomenclature.name if nomenclature else None,
        'packing_name': packing.display_name if packing else None,
        'product_group_name': group.name if group else None,
        'product_group_slug': group.slug if group else None,
        'supplier_name': supplier.name if supplier else None,
        'purchase_currency_code': currency.code if currency else None,
    })
    if show_purchase is None:
        show_purchase = has_request_permission(db, user, row.request_id, 'finance.purchase.read')
    if not show_purchase:
        value.pop('purchase_price', None)
    return value


def structured_item_fields(db: Session, data: dict[str, Any], previous: RequestItem | None = None) -> dict[str, Any]:
    nomenclature_id = data.get('nomenclature_id', previous.nomenclature_id if previous else None)
    packing_id = data.get('packing_id', previous.packing_id if previous else None)
    if bool(nomenclature_id) != bool(packing_id):
        raise DomainError('PACKING_REQUIRED', 'Для номенклатуры выберите фасовку', 422, 'packing_id')
    if nomenclature_id:
        nomenclature = require_nomenclature(db, nomenclature_id)
        packing = require_packing(db, packing_id, nomenclature.id)
        quantity = data.get('quantity', previous.quantity if previous else None)
        if quantity is None or quantity != quantity.to_integral_value():
            raise DomainError('QUANTITY_UNITS_REQUIRED', 'Укажите целое число единиц выбранной фасовки', 422, 'quantity')
        if data.get('unit') not in (None, 'pcs'):
            raise DomainError('UNIT_INVALID', 'Количество указывается в единицах выбранной фасовки', 422, 'unit')
        data['unit'] = 'pcs'
        if 'product_group_id' in data and data['product_group_id'] not in (None, nomenclature.product_group_id):
            raise DomainError('PRODUCT_GROUP_MISMATCH', 'Группа не совпадает с номенклатурой', 422, 'product_group_id')
        data['product_group_id'] = nomenclature.product_group_id
        if not data.get('description') and previous is None:
            data['description'] = nomenclature.name
        if previous and previous.nomenclature_id != nomenclature.id and 'description' not in data:
            old = db.get(Nomenclature, previous.nomenclature_id) if previous.nomenclature_id else None
            if old and previous.description == old.name:
                data['description'] = nomenclature.name
        if previous is None:
            data['article'] = data.get('article') or nomenclature.article
            data['cas'] = data.get('cas') or nomenclature.cas
            data['packaging'] = data.get('packaging') or packing.display_name
    elif not (data.get('description') or (previous and previous.description)):
        raise DomainError('ITEM_DESCRIPTION_REQUIRED', 'Укажите номенклатуру или описание позиции', 422, 'description')
    if data.get('product_group_id'):
        require_group(db, data['product_group_id'])
    supplier_id = data.get('supplier_id', previous.supplier_id if previous else None)
    if supplier_id:
        require_supplier(db, supplier_id)
    country_id = data.get('supplier_country_id', previous.supplier_country_id if previous else None)
    if country_id:
        country = db.get(Country, country_id)
        if not country or not country.active:
            raise DomainError('COUNTRY_INVALID', 'Выберите страну из справочника', 422, 'supplier_country_id')
    currency_id = data.get('purchase_currency_id', previous.purchase_currency_id if previous else None)
    if currency_id:
        require_currency(db, currency_id)
    price = data.get('purchase_price', previous.purchase_price if previous else None)
    if price is not None and currency_id is None:
        raise DomainError('CURRENCY_REQUIRED', 'Для закупочной цены укажите валюту', 422, 'purchase_currency_id')
    return data


@router.get('/requests')
def requests(q: str = '', stage: str | None = None, client_id: str | None = None, owner_id: str | None = None, page: int = 1, page_size: int = 25, sort: str = 'created_at', direction: str = 'desc', user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
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
    clients = prefetch(db, Counterparty, (item['client_id'] for item in result['items']))
    owners = prefetch(db, User, (item['owner_id'] for item in result['items']))
    counts = supplier_counts(db, [item['id'] for item in result['items']])
    for item in result['items']:
        item['client_name'] = clients[item['client_id']].name
        item['owner_name'] = owners[item['owner_id']].name
        item.update(counts.get(item['id'], {'quoted_suppliers': 0, 'answered_suppliers': 0, 'asked_suppliers': 0}))
    return result


def supplier_counts(db: Session, request_ids: list[str]) -> dict[str, dict[str, int]]:
    """Per request: suppliers asked for prices, those that answered and those that gave a price.

    Business remarks of 21 and 22 October 2026: a supplier answers with a quote or by a reply without prices
    («Ответил без цен»), so two requests with one quote and one refusal are «2 из 2». A supplier that sent a
    quote without being asked from the CRM counts as asked too.
    """
    from app.business.models import SupplierMail

    if not request_ids:
        return {}
    quoted = set(db.execute(
        select(QuoteSheet.request_id, QuoteSheet.supplier_id).join(QuoteItem, QuoteItem.quote_id == QuoteSheet.id)
        .where(QuoteSheet.request_id.in_(request_ids), QuoteSheet.archived.is_(False), QuoteItem.archived.is_(False))
        .distinct()).tuples())
    mails = db.execute(select(SupplierMail.request_id, SupplierMail.supplier_id, SupplierMail.replied_at).where(
        SupplierMail.request_id.in_(request_ids), SupplierMail.sent_at.is_not(None))).tuples().all()
    asked = {(request_id, supplier_id) for request_id, supplier_id, _ in mails}
    answered = quoted | {(request_id, supplier_id) for request_id, supplier_id, replied in mails if replied}
    result: dict[str, dict[str, int]] = {}
    for request_id in request_ids:
        def count(pairs: set[tuple[str, str]]) -> int:
            return sum(1 for pair in pairs if pair[0] == request_id)
        result[request_id] = {'quoted_suppliers': count(quoted), 'answered_suppliers': count(answered),
                              'asked_suppliers': count(asked | answered)}
    return result


@router.post('/requests', status_code=201)
def create_request(body: RequestInput, idempotency_key: str | None = Header(default=None), user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    require_permission(db, user, 'requests.write')
    client = check_client(db, user, body.client_id)
    if client.client_base != 'working' or client.kind not in ('client', 'both') or client.archived:
        raise DomainError('WORKING_CLIENT_REQUIRED', 'Выберите клиента из рабочей базы', 422, 'client_id')
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
        row = Request(**body.model_dump(exclude={'owner_id'}), owner_id=owner.id, number=next_request_number(db))
        return save(db, user, row, 'request')
    return idem(db, user, idempotency_key, 'requests.create', body.model_dump(mode='json'), operation)


@router.get('/requests/{entity_id}')
def request_detail(entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    row = check_request(db, user, entity_id)
    result = serialize(row)
    client = db.get(Counterparty, row.client_id)
    result['client_name'] = client.name
    # Business remark of 21 October 2026: the calculation form sells to such a client with VAT 0 %.
    result['client_in_belarus'] = bool(client.in_belarus)
    result['owner_name'] = db.get(User, row.owner_id).name
    result['source_columns'] = list(dict.fromkeys(col for columns in db.scalars(select(RequestItem.source_columns).where(RequestItem.request_id == row.id, RequestItem.archived.is_(False))) for col in (columns or [])))
    contact = db.get(Contact, row.contact_id) if row.contact_id else None
    result['contact_name'] = contact.name if contact else None
    result['members'] = list(db.scalars(select(RequestMember.user_id).where(RequestMember.request_id == row.id)))
    result['next_task'] = None
    task = db.scalar(
        select(Task).where(
            Task.entity_type == 'request', 
            Task.entity_id == row.id, 
            Task.status.in_(['assigned', 'in_progress']),
            task_predicate(db, user)
        ).order_by(Task.due_at)
    )
    if task:
        result['next_task'] = serialize(task)
    return result


@router.patch('/requests/{entity_id}')
def edit_request(entity_id: str, body: RequestPatch, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    check_request(db, user, entity_id, 'requests.write')
    row = lock(db, Request, entity_id)
    check_version(row, body.version)
    before = serialize(row)
    # A selected refusal reason is the customer's decision to close this
    # request. An explicit reopening clears the previous reason instead.
    if body.loss_reason and body.loss_reason.strip():
        if row.commercial_stage == 'closed_lost' and 'commercial_stage' in body.model_fields_set and body.commercial_stage != 'closed_lost':
            body.loss_reason = None
        else:
            body.commercial_stage = 'closed_lost'
    if 'owner_id' in body.model_fields_set and body.owner_id != row.owner_id:
        require_permission(db, user, 'requests.assign')
        active_user(db, body.owner_id)
        if not body.reason:
            raise DomainError('REASON_REQUIRED', 'Укажите причину переназначения', 422, 'reason')
    if 'seller_id' in body.model_fields_set and body.seller_id != row.seller_id:
        from app.commerce.models import CommercialDocument
        if body.seller_id and not db.get(Seller, body.seller_id):
            raise DomainError('SELLER_INVALID', 'Организация продавца не найдена', 422, 'seller_id')
        if db.scalar(select(CommercialDocument.id).where(CommercialDocument.request_id == row.id).limit(1)):
            raise DomainError('SELLER_LOCKED', 'У заявки уже выпущены документы. Для другой организации создайте отдельную заявку.', 409, 'seller_id')
    if 'commercial_stage' in body.model_fields_set and body.commercial_stage != row.commercial_stage:
        allowed = ['new', 'clarification', 'collecting_quotes', 'quote_given', 'calculation',
                   'proposal_sent', 'composition_agreed', 'awaiting_payment', 'closed_lost']
        if body.commercial_stage not in allowed:
            raise DomainError('STAGE_ACTION_REQUIRED', 'Этот этап меняется при выполнении связанной бизнес-операции', 422, 'commercial_stage')
        if body.commercial_stage == 'closed_lost':
            if not body.loss_reason:
                raise DomainError('LOSS_REASON_REQUIRED', 'Укажите причину закрытия без продажи', 422, 'loss_reason')
            
            setting = db.scalar(select(AppSetting).where(AppSetting.key == 'loss_reasons', AppSetting.status == 'published'))
            if not setting:
                raise DomainError('SETUP_REQUIRED', 'Справочник причин отказа не настроен. Заполните его в настройках организации.', 409)
            if body.loss_reason not in setting.value.get('reasons', []):
                raise DomainError('LOSS_REASON_INVALID', 'Выберите причину из справочника', 422, 'loss_reason')

            from app.commerce.models import Execution
            if db.scalar(select(Execution.id).where(Execution.request_id == row.id,
                                                  Execution.quantity > Execution.cancelled_quantity).limit(1)):
                raise DomainError('ACCEPTED_COMPOSITION_EXISTS', 'Сначала оформите согласованную отмену принятого состава', 409)
            row.closed_at = utcnow()
        elif row.closed_at:
            if not body.reason:
                raise DomainError('REOPEN_REASON_REQUIRED', 'Укажите причину повторного открытия', 422, 'reason')
            row.closed_at = None
            row.loss_reason = None
    for k, v in body.model_dump(exclude_unset=True, exclude={'version', 'reason'}).items():
        setattr(row, k, v)
    row.version += 1
    audit(db, user, 'request', row.id, 'updated', before, serialize(row), body.reason)
    return serialize(row)


@router.put('/requests/{entity_id}/members')
def share_request(entity_id: str, body: ShareInput, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
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
def items(entity_id: str, q: str = '', page: int = 1, page_size: int = 100, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    check_request(db, user, entity_id)
    stmt = select(RequestItem).where(RequestItem.request_id == entity_id, RequestItem.quote_only.is_(False))
    if q:
        names = select(Nomenclature.id).where(or_(
            Nomenclature.name.ilike(f'%{q}%'), Nomenclature.article.ilike(f'%{q}%'),
        ))
        stmt = stmt.where(or_(RequestItem.description.ilike(f'%{q}%'), RequestItem.nomenclature_id.in_(names)))
    result = paginate(db, stmt.order_by(RequestItem.created_at, RequestItem.id), page, page_size)
    rows = [db.get(RequestItem, item['id']) for item in result['items']]
    prefetch_item_relations(db, rows)
    show_purchase = has_request_permission(db, user, entity_id, 'finance.purchase.read')
    result['items'] = [request_item_view(db, user, row, show_purchase) for row in rows]
    numbers = {item_id: index for index, item_id in enumerate(db.scalars(select(RequestItem.id).where(RequestItem.request_id == entity_id, RequestItem.archived.is_(False)).order_by(RequestItem.created_at, RequestItem.id)), 1)}
    for item in result['items']:
        item['position_number'] = numbers.get(item['id'])
    return result


@router.post('/requests/{entity_id}/items', status_code=201)
def create_item(entity_id: str, body: ItemInput, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    check_request(db, user, entity_id, 'requests.write')
    parent = lock(db, Request, entity_id)
    row = RequestItem(request_id=entity_id, **structured_item_fields(db, body.model_dump()))
    result = save(db, user, row, 'request_item')
    db.add(RequestItemRevision(item_id=row.id, revision=1, snapshot=result, author_id=user.id))
    parent.version += 1
    return request_item_view(db, user, row)


@router.post('/requests/{entity_id}/items/start')
def start_items(entity_id: str, body: StartItemsInput, idempotency_key: str | None = Header(default=None), user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")):
    check_request(db, user, entity_id, 'requests.write')

    def operation():
        parent = lock(db, Request, entity_id)
        ids = sorted(set(body.item_ids))
        rows = db.scalars(select(RequestItem).where(RequestItem.request_id == entity_id, RequestItem.id.in_(ids)).order_by(RequestItem.id).with_for_update()).all()
        if parent.archived or len(rows) != len(ids) or any(row.archived for row in rows):
            raise DomainError('REQUEST_ITEM_INVALID', 'Выберите действующие позиции этой заявки', 422)
        for row in rows:
            if row.work_status != 'in_progress':
                before = serialize(row)
                row.work_status = 'in_progress'
                row.version += 1
                audit(db, user, 'request_item', row.id, 'started', before, serialize(row), body.reason)
        parent.version += 1
        return {'count': len(rows), 'item_ids': ids}

    return idem(db, user, idempotency_key, f'items.start:{entity_id}', body.model_dump(), operation)


@router.patch('/request-items/{entity_id}')
def edit_item(entity_id: str, body: ItemPatch, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    initial = db.get(RequestItem, entity_id)
    if not initial:
        raise DomainError('NOT_FOUND', 'Позиция не найдена', 404)
    check_request(db, user, initial.request_id, 'requests.write')
    parent = lock(db, Request, initial.request_id)
    row = lock(db, RequestItem, entity_id)
    check_version(row, body.version)
    data = structured_item_fields(db, body.model_dump(exclude_unset=True, exclude={'version', 'reason'}), row)
    if 'quantity' in data or 'unit' in data:
        from app.commerce.models import Execution
        accepted = db.scalar(select(sa.func.sum(Execution.quantity - Execution.cancelled_quantity)).where(Execution.item_id == row.id)) or Decimal('0')
        if accepted > 0:
            if 'unit' in data and data['unit'] != row.unit:
                raise DomainError('UNIT_ACCEPTED', 'Единица измерения уже используется в принятых предложениях', 409, 'unit')
            new_qty = data['quantity'] if 'quantity' in data else row.quantity
            if new_qty is None:
                raise DomainError('QUANTITY_REQUIRED', 'Количество нельзя очистить после принятия', 422, 'quantity')
            if new_qty < accepted:
                raise DomainError('QUANTITY_EXCEEDED', f'Количество не может быть меньше уже принятого ({accepted})', 409, 'quantity')
    before = serialize(row)
    for k, v in data.items():
        setattr(row, k, v)
    row.version += 1
    row.revision += 1
    parent.version += 1
    db.add(RequestItemRevision(item_id=row.id, revision=row.revision, snapshot=serialize(row), author_id=user.id, reason=body.reason))
    audit(db, user, 'request_item', row.id, 'revised', before, serialize(row), body.reason)
    return request_item_view(db, user, row)


@router.get('/request-items/{entity_id}/revisions')
def item_revisions(entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    row = db.get(RequestItem, entity_id)
    if not row:
        raise DomainError('NOT_FOUND', 'Позиция не найдена', 404)
    check_request(db, user, row.request_id)
    result = paginate(db, select(RequestItemRevision).where(RequestItemRevision.item_id == entity_id).order_by(RequestItemRevision.revision.desc()), 1, 100)
    if not has_request_permission(db, user, row.request_id, 'finance.purchase.read'):
        for item in result['items']:
            item['snapshot'].pop('purchase_price', None)
    return result


@router.get('/requests/{entity_id}/history')
def history(entity_id: str, page: int = 1, page_size: int = 50, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    check_request(db, user, entity_id)
    ids = [entity_id, *db.scalars(select(RequestItem.id).where(RequestItem.request_id == entity_id))]
    result = paginate(db, select(AuditEvent).where(AuditEvent.entity_id.in_(ids)).order_by(AuditEvent.created_at.desc()), page, page_size)
    # Snapshots can contain purchase prices and rewards; the rights must cover this very request.
    if not all(has_request_permission(db, user, entity_id, permission) for permission in
               ('finance.purchase.read', 'finance.calculations.read', 'finance.reward.read')):
        for row in result['items']:
            row.pop('before', None)
            row.pop('after', None)
    return result
