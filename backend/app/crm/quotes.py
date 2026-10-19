"""Supplier quotes: sheets, their items, price history and archiving."""

from collections import defaultdict
from datetime import timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.commerce.schemas import VersionCommand
from app.core.db import get_db, utcnow
from app.core.errors import DomainError
from app.core.models import User
from app.core.security import (
    check_request,
    current_user,
    has_request_permission,
    request_predicate,
    require_permission,
)
from app.core.service import advisory, audit, check_version, idem, lock, notify, prefetch, serialize
from app.core.service import page as paginate
from app.crm.catalogue import require_currency, require_nomenclature, require_packing
from app.crm.common import require_supplier
from app.crm.models import (
    Counterparty,
    Currency,
    Nomenclature,
    Packing,
    ProductGroup,
    QuoteItem,
    QuoteSheet,
    Request,
    RequestItem,
)
from app.crm.schemas import QuoteSheetInput

router = APIRouter(tags=['CRM'])


def aware_utc(value: Any) -> Any:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def quote_item_view(db: Session, row: QuoteItem, show_purchase: bool = True) -> dict[str, Any]:
    value = serialize(row)
    sheet = db.get(QuoteSheet, row.quote_id)
    request = db.get(Request, sheet.request_id)
    nomenclature = db.get(Nomenclature, row.nomenclature_id)
    packing = db.get(Packing, row.packing_id)
    group = db.get(ProductGroup, nomenclature.product_group_id) if nomenclature.product_group_id else None
    currency = db.get(Currency, row.currency_id)
    supplier = db.get(Counterparty, row.supplier_id)
    value.update({
        'quote_sheet_id': sheet.id,
        'quote_number': sheet.number,
        'quote_sheet_version': sheet.version,
        'request_id': sheet.request_id,
        'request_number': request.number,
        'supplier_request_id': sheet.supplier_request_id,
        'nomenclature_name': nomenclature.name,
        'article': nomenclature.article,
        'manufacturer': nomenclature.manufacturer,
        'request_quantity': db.get(RequestItem, row.source_request_item_id).quantity if row.source_request_item_id else None,
        'packing_name': packing.display_name,
        'product_group_id': nomenclature.product_group_id,
        'product_group_name': group.name if group else None,
        'product_group_slug': group.slug if group else None,
        'currency_code': currency.code,
        'supplier_name': supplier.name,
        'calculation_type': (supplier.details or {}).get('calculation_type', 'IMPORT'),
        'expired': aware_utc(row.valid_until) <= utcnow(),
    })
    if not show_purchase:
        value.pop('unit_price', None)
        value.pop('price_source_id', None)
    return value


def prefetch_quote_relations(db: Session, rows: list[QuoteItem]) -> None:
    sheets = prefetch(db, QuoteSheet, (row.quote_id for row in rows))
    prefetch(db, Request, (sheet.request_id for sheet in sheets.values()))
    nomenclatures = prefetch(db, Nomenclature, (row.nomenclature_id for row in rows))
    prefetch(db, ProductGroup, (nomenclature.product_group_id for nomenclature in nomenclatures.values()))
    prefetch(db, Packing, (row.packing_id for row in rows))
    prefetch(db, Currency, (row.currency_id for row in rows))
    prefetch(db, Counterparty, (row.supplier_id for row in rows))
    prefetch(db, RequestItem, (row.source_request_item_id for row in rows))


def quote_item_views(db: Session, rows: list[QuoteItem], show_purchase: bool = True) -> list[dict[str, Any]]:
    prefetch_quote_relations(db, rows)
    return [quote_item_view(db, row, show_purchase) for row in rows]


def quote_sheet_views(db: Session, rows: list[QuoteSheet], show_purchase: bool = True) -> list[dict[str, Any]]:
    """Sheets with their current items: one query for all items instead of one per sheet."""
    items: dict[str, list[QuoteItem]] = defaultdict(list)
    for item in db.scalars(select(QuoteItem).where(QuoteItem.quote_id.in_([row.id for row in rows]),
                                                   QuoteItem.archived.is_(False))
                           .order_by(QuoteItem.created_at, QuoteItem.id)):
        items[item.quote_id].append(item)
    prefetch_quote_relations(db, [item for group in items.values() for item in group])
    prefetch(db, Request, (row.request_id for row in rows))
    prefetch(db, Counterparty, (row.supplier_id for row in rows))
    views = []
    for row in rows:
        value = serialize(row)
        value.update({
            'request_number': db.get(Request, row.request_id).number,
            'supplier_name': db.get(Counterparty, row.supplier_id).name,
            'items': [quote_item_view(db, item, show_purchase) for item in items[row.id]],
        })
        views.append(value)
    return views


def quote_sheet_view(db: Session, row: QuoteSheet, show_purchase: bool = True) -> dict[str, Any]:
    return quote_sheet_views(db, [row], show_purchase)[0]


def latest_price(
    db: Session, user: User, supplier_id: str, nomenclature_id: str,
    packing_id: str, currency_id: str | None = None, at: Any = None,
) -> QuoteItem | None:
    at = at or utcnow()
    stmt = (
        select(QuoteItem)
        .join(QuoteSheet, QuoteSheet.id == QuoteItem.quote_id)
        .where(
            QuoteItem.supplier_id == supplier_id,
            QuoteItem.nomenclature_id == nomenclature_id,
            QuoteItem.packing_id == packing_id,
            QuoteItem.quoted_at <= at,
            QuoteItem.valid_until > at,
            QuoteItem.archived.is_(False),
            QuoteSheet.archived.is_(False),
            QuoteSheet.request_id.in_(select(Request.id).where(request_predicate(db, user, 'finance.purchase.read'))),
        )
    )
    if currency_id:
        stmt = stmt.where(QuoteItem.currency_id == currency_id)
    return db.scalar(stmt.order_by(QuoteItem.quoted_at.desc(), QuoteItem.created_at.desc(), QuoteItem.id.desc()).limit(1))


@router.get('/quote-items/latest')
def latest_quote_item(
    supplier_id: str, nomenclature_id: str, packing_id: str, currency_id: str | None = None,
    user: User = Depends(current_user), db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    require_permission(db, user, 'finance.purchase.read')
    require_supplier(db, supplier_id)
    require_nomenclature(db, nomenclature_id)
    require_packing(db, packing_id, nomenclature_id)
    if currency_id:
        require_currency(db, currency_id)
    row = latest_price(db, user, supplier_id, nomenclature_id, packing_id, currency_id)
    return {'item': quote_item_view(db, row) if row else None}


@router.get('/quote-items/history')
def quote_item_history(
    supplier_id: str | None = None, nomenclature_id: str | None = None,
    packing_id: str | None = None, currency_id: str | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
    user: User = Depends(current_user), db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    require_permission(db, user, 'finance.purchase.read')
    stmt = select(QuoteItem).join(QuoteSheet, QuoteSheet.id == QuoteItem.quote_id).where(
        QuoteItem.archived.is_(False), QuoteSheet.archived.is_(False),
        QuoteSheet.request_id.in_(select(Request.id).where(request_predicate(db, user, 'finance.purchase.read')))
    )
    for field, value in (
        ('supplier_id', supplier_id), ('nomenclature_id', nomenclature_id),
        ('packing_id', packing_id), ('currency_id', currency_id),
    ):
        if value:
            stmt = stmt.where(getattr(QuoteItem, field) == value)
    result = paginate(db, stmt.order_by(QuoteItem.quoted_at.desc(), QuoteItem.id.desc()), page, page_size)
    result['items'] = quote_item_views(db, [db.get(QuoteItem, item['id']) for item in result['items']])
    return result


@router.get('/requests/{entity_id}/quote-items')
def request_quote_items(
    entity_id: str, page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=100), ids: str = '', load_all: bool = Query(False, alias="all"),
    user: User = Depends(current_user), db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    check_request(db, user, entity_id)
    stmt = select(QuoteItem).join(QuoteSheet, QuoteSheet.id == QuoteItem.quote_id).where(
        QuoteSheet.request_id == entity_id, QuoteSheet.archived.is_(False), QuoteItem.archived.is_(False))
    if ids:
        selected_ids = [value.strip() for value in ids.split(',') if value.strip()]
        if len(selected_ids) > 10000:
            raise DomainError('QUOTE_SELECTION_LIMIT', 'Выберите не более 10 000 позиций квоты', 422, 'ids')
        stmt = stmt.where(QuoteItem.id.in_(selected_ids))
    if ids or load_all:
        rows = list(db.scalars(stmt.order_by(QuoteItem.created_at.desc(), QuoteItem.id.desc()).limit(10000)))
        result = {"total": len(rows), "page": 1, "page_size": len(rows)}
    else:
        result = paginate(db, stmt.order_by(QuoteItem.created_at.desc(), QuoteItem.id.desc()), page, page_size)
        rows = [db.get(QuoteItem, item['id']) for item in result['items']]
    show_purchase = has_request_permission(db, user, entity_id, 'finance.purchase.read')
    result['items'] = quote_item_views(db, rows, show_purchase)
    calculated = calculated_quote_items(db, entity_id)
    from app.commerce.rates import in_rubles, profile_rates

    # Prices of different suppliers compared in rubles, by the rates of the published profile.
    rates = profile_rates(db) if show_purchase else {"rates": {}, "profile": None}
    for item in result['items']:
        item['last_calculation'] = calculated.get(item['id'])
        if show_purchase:
            item['unit_price_rub'] = in_rubles(item['unit_price'], item['currency_code'], rates['rates'])
    result['rates_profile'] = rates['profile']
    return result


def calculated_quote_items(db, request_id: str) -> dict[str, dict]:
    """The latest saved calculation of each quote position of a request, for the mark in the calculation form."""
    from app.commerce.models import Calculation

    result: dict[str, dict] = {}
    for calculation in db.scalars(select(Calculation).where(Calculation.request_id == request_id)
                                  .order_by(Calculation.created_at, Calculation.id)):
        mark = {'id': calculation.id, 'number': calculation.snapshot.get('calculation_number'),
                'created_at': calculation.created_at.isoformat()}
        for line in calculation.snapshot.get('lines') or []:
            if line.get('quote_item_id'):
                result[line['quote_item_id']] = mark
    return result


@router.get('/requests/{entity_id}/quote-sheets')
def request_quote_sheets(
    entity_id: str, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
    user: User = Depends(current_user), db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    check_request(db, user, entity_id)
    result = paginate(db, select(QuoteSheet).where(QuoteSheet.request_id == entity_id, QuoteSheet.archived.is_(False)).order_by(QuoteSheet.created_at.desc()), page, page_size)
    show_purchase = has_request_permission(db, user, entity_id, 'finance.purchase.read')
    result['items'] = quote_sheet_views(db, [db.get(QuoteSheet, item['id']) for item in result['items']], show_purchase)
    return result


@router.get('/quote-sheets/{entity_id}')
def quote_sheet_detail(entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    row = db.get(QuoteSheet, entity_id)
    if not row:
        raise DomainError('NOT_FOUND', 'Квота не найдена', 404)
    check_request(db, user, row.request_id)
    return quote_sheet_view(db, row, has_request_permission(db, user, row.request_id, 'finance.purchase.read'))


def archive_quote(db, user, row):
    before = serialize(row)
    row.archived = True
    row.version += 1
    audit(db, user, 'quote_item' if isinstance(row, QuoteItem) else 'quote_sheet',
          row.id, 'deleted', before=before, after=serialize(row), reason='Ошибочная квота')


@router.post('/quote-items/{entity_id}/delete')
def delete_quote_item(entity_id: str, body: VersionCommand,
                      user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")):
    row = db.get(QuoteItem, entity_id)
    if not row:
        raise DomainError('NOT_FOUND', 'Позиция квоты не найдена', 404)
    sheet = db.get(QuoteSheet, row.quote_id)
    check_request(db, user, sheet.request_id, 'quotes.write')

    def operation():
        advisory(db, f'request-commerce:{sheet.request_id}')
        current_sheet = lock(db, QuoteSheet, sheet.id)
        current = lock(db, QuoteItem, entity_id)
        check_version(current, body.version)
        if current.archived or current_sheet.archived:
            raise DomainError('QUOTE_DELETED', 'Квота уже удалена', 409)
        archive_quote(db, user, current)
        if not db.scalar(select(QuoteItem.id).where(
                QuoteItem.quote_id == sheet.id, QuoteItem.archived.is_(False))):
            archive_quote(db, user, current_sheet)
        else:
            current_sheet.version += 1
        return {'deleted_ids': [current.id]}

    return idem(db, user, body.idempotency_key, f'quote-item.delete:{entity_id}',
                body.model_dump(mode='json'), operation)


@router.post('/quote-sheets/{entity_id}/delete')
def delete_quote_sheet(entity_id: str, body: VersionCommand,
                       user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")):
    row = db.get(QuoteSheet, entity_id)
    if not row:
        raise DomainError('NOT_FOUND', 'Квота не найдена', 404)
    check_request(db, user, row.request_id, 'quotes.write')

    def operation():
        advisory(db, f'request-commerce:{row.request_id}')
        current = lock(db, QuoteSheet, entity_id)
        check_version(current, body.version)
        if current.archived:
            raise DomainError('QUOTE_DELETED', 'Квота уже удалена', 409)
        items = db.scalars(select(QuoteItem).where(
            QuoteItem.quote_id == entity_id, QuoteItem.archived.is_(False)).with_for_update()).all()
        for item in items:
            archive_quote(db, user, item)
        archive_quote(db, user, current)
        return {'deleted_ids': [item.id for item in items]}

    return idem(db, user, body.idempotency_key, f'quote-sheet.delete:{entity_id}',
                body.model_dump(mode='json'), operation)


def record_quote_sheet(db: Session, user: User, request_id: str, body: QuoteSheetInput,
                       idempotency_key: str | None = None) -> dict[str, Any]:
    """Validate and save a supplier's quote sheet for the request; table imports use it too."""
    check_request(db, user, request_id, 'quotes.write')
    require_supplier(db, body.supplier_id)
    if body.supplier_request_id:
        from app.commerce.models import SupplierRequest
        rfq = db.get(SupplierRequest, body.supplier_request_id)
        if not rfq or rfq.request_id != request_id or rfq.supplier_id not in (None, body.supplier_id):
            raise DomainError('SUPPLIER_REQUEST_INVALID', 'Запрос поставщику не соответствует заявке и поставщику', 422, 'supplier_request_id')

    def operation() -> dict[str, Any]:
        advisory(db, 'quote_sheet.number')
        count = db.scalar(select(func.count()).select_from(QuoteSheet)) or 0
        sheet = QuoteSheet(
            number=f'Q-{count + 1:06d}', request_id=request_id, supplier_id=body.supplier_id,
            supplier_request_id=body.supplier_request_id, author_id=user.id,
        )
        db.add(sheet)
        db.flush()
        for item in body.items:
            nomenclature = require_nomenclature(db, item.nomenclature_id)
            require_packing(db, item.packing_id, nomenclature.id)
            source = db.get(RequestItem, item.source_request_item_id) if item.source_request_item_id else None
            if source is None and not item.source_request_item_id:
                source = RequestItem(request_id=request_id, description=nomenclature.name, nomenclature_id=nomenclature.id,
                                     packing_id=item.packing_id, product_group_id=nomenclature.product_group_id,
                                     quantity=item.quantity, unit='pcs', quote_only=True)
                db.add(source)
                db.flush()
            if not source or source.request_id != request_id or source.archived:
                raise DomainError('REQUEST_ITEM_INVALID', 'Позиция не относится к выбранной заявке', 422, 'source_request_item_id')
            if not source.nomenclature_id or not source.packing_id or source.quantity is None or source.unit != 'pcs':
                raise DomainError('REQUEST_ITEM_UNSTRUCTURED', 'Сначала укажите номенклатуру, фасовку и количество в позиции заявки', 422, 'source_request_item_id')
            quoted_at = item.quoted_at.astimezone(timezone.utc) if item.quoted_at else utcnow()
            if quoted_at > utcnow() + timedelta(minutes=1):
                raise DomainError('QUOTE_DATE_FUTURE', 'Дата квоты не может быть в будущем', 422, 'quoted_at')
            currency_id = item.currency_id
            if currency_id:
                require_currency(db, currency_id)
            candidate = latest_price(
                db, user, body.supplier_id, item.nomenclature_id, item.packing_id,
                currency_id, quoted_at,
            ) if item.unit_price is None or currency_id is None or item.delivery_days is None else None
            if item.unit_price is None and candidate is None:
                raise DomainError('QUOTE_PRICE_REQUIRED', 'Свежей цены нет. Укажите цену позиции', 422, 'unit_price')
            if currency_id is None:
                if candidate is None:
                    raise DomainError('CURRENCY_REQUIRED', 'Укажите валюту закупки', 422, 'currency_id')
                currency_id = candidate.currency_id
            unit_price = item.unit_price if item.unit_price is not None else candidate.unit_price
            delivery_days = item.delivery_days if item.delivery_days is not None else (candidate.delivery_days if candidate else None)
            if delivery_days is None:
                raise DomainError('DELIVERY_DAYS_REQUIRED', 'Укажите срок поставки в днях', 422, 'delivery_days')
            row = QuoteItem(
                quote_id=sheet.id, supplier_id=body.supplier_id, nomenclature_id=item.nomenclature_id,
                packing_id=item.packing_id, quantity=item.quantity, unit_price=unit_price,
                currency_id=currency_id, delivery_days=delivery_days, quoted_at=quoted_at,
                valid_until=quoted_at + timedelta(days=21),
                source_request_item_id=source.id,
                price_source_id=candidate.id if candidate and item.unit_price is None else None,
                author_id=user.id,
            )
            db.add(row)
            db.flush()
            audit(db, user, 'quote_item', row.id, 'created', after=serialize(row))
        parent = lock(db, Request, request_id)
        if parent.commercial_stage in ('new', 'clarification', 'collecting_quotes', 'quotes'):
            before_stage = parent.commercial_stage
            parent.commercial_stage = 'quote_given'
            parent.version += 1
            audit(db, user, 'request', parent.id, 'quote_given', before={'commercial_stage': before_stage}, after={'commercial_stage': parent.commercial_stage})
        batch_id = db.info.get("quote_import_batch")
        # The owner hears of quotes that someone else brought, not of the ones they loaded themselves.
        if parent.owner_id != user.id:
            notify(db, parent.owner_id, f"quote-import:{batch_id}" if batch_id else f"quote-sheet:{sheet.id}",
                   (f"Загружены квоты по заявке №{parent.number}" if batch_id else f"По заявке №{parent.number} добавлена квота {sheet.number}"),
                   "request", parent.id)
        result = quote_sheet_view(db, sheet)
        audit(db, user, 'quote_sheet', sheet.id, 'created', after=result)
        return result

    payload = body.model_dump(mode='json')
    if idempotency_key:
        return idem(db, user, idempotency_key, f'quote_sheets.create:{request_id}', payload, operation)
    return operation()


@router.post('/requests/{entity_id}/quote-sheets', status_code=201)
def create_quote_sheet(
    entity_id: str, body: QuoteSheetInput, idempotency_key: str | None = Header(default=None),
    user: User = Depends(current_user), db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    return record_quote_sheet(db, user, entity_id, body, idempotency_key)
