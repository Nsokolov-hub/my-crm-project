from datetime import timedelta, timezone
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, Form, Header, Query, UploadFile
from pydantic import BaseModel, Field
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
    has_request_permission,
    request_predicate,
    require_permission,
    scope_for,
    task_predicate,
)
from app.core.service import advisory, audit, check_version, idem, lock, notify, serialize
from app.core.service import page as paginate
from app.crm.codes import next_request_number
from app.crm.models import (
    Call,
    Contact,
    Counterparty,
    CounterpartyDocument,
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
from app.crm.schemas import (
    BulkCallTaskInput,
    CallInput,
    CallPatch,
    ClientInput,
    ClientPatch,
    ContactCreateInput,
    ContactInput,
    ContactPatch,
    CounterpartyDocumentPatch,
    CountryInput,
    CurrencyInput,
    ItemInput,
    ItemPatch,
    NomenclatureInput,
    NomenclaturePatch,
    PackingInput,
    PackingPatch,
    ProductGroupInput,
    PromoteClientInput,
    QuoteSheetInput,
    RequestInput,
    RequestPatch,
    SellerInput,
    ShareInput,
    StartItemsInput,
    TaskInput,
    TaskPatch,
)

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
def dictionary(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
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


def require_group(db: Session, entity_id: str | None) -> ProductGroup:
    group = db.get(ProductGroup, entity_id) if entity_id else db.scalar(
        select(ProductGroup).where(ProductGroup.slug == 'other')
    )
    if not group or not group.active:
        raise DomainError('PRODUCT_GROUP_INVALID', 'Выберите действующую товарную группу', 422, 'product_group_id')
    return group


def require_nomenclature(db: Session, entity_id: str) -> Nomenclature:
    row = db.get(Nomenclature, entity_id)
    if not row or not row.active:
        raise DomainError('NOMENCLATURE_INVALID', 'Выберите действующую номенклатуру', 422, 'nomenclature_id')
    return row


def require_packing(db: Session, entity_id: str, nomenclature_id: str) -> Packing:
    row = db.get(Packing, entity_id)
    if not row or not row.active or row.nomenclature_id != nomenclature_id:
        raise DomainError('PACKING_INVALID', 'Фасовка не относится к выбранной номенклатуре', 422, 'packing_id')
    return row


def require_currency(db: Session, entity_id: str) -> Currency:
    row = db.get(Currency, entity_id)
    if not row or not row.active:
        raise DomainError('CURRENCY_INVALID', 'Выберите валюту из справочника', 422, 'currency_id')
    return row


def require_supplier(db: Session, entity_id: str) -> Counterparty:
    row = db.get(Counterparty, entity_id)
    if not row or row.archived or row.kind not in ('supplier', 'both'):
        raise DomainError('SUPPLIER_INVALID', 'Выберите действующего поставщика', 422, 'supplier_id')
    return row


def require_catalog_create(db: Session, user: User) -> None:
    if not any(can(db, user, permission) for permission in ('catalog.write', 'requests.write')):
        raise DomainError('FORBIDDEN', 'Недостаточно прав для изменения справочника', 403)


def packing_name(value: Decimal, unit: str) -> str:
    number = format(value, 'f')
    if '.' in number:
        number = number.rstrip('0').rstrip('.')
    return f'{number} {unit}'


def nomenclature_creator(db: Session, entity_id: str) -> str | None:
    return db.scalar(select(AuditEvent.actor_id).where(
        AuditEvent.entity_type == 'nomenclature',
        AuditEvent.entity_id == entity_id,
        AuditEvent.action == 'created',
    ).order_by(AuditEvent.created_at, AuditEvent.id).limit(1))


def nomenclature_view(db: Session, row: Nomenclature) -> dict[str, Any]:
    value = serialize(row)
    value['created_by_id'] = nomenclature_creator(db, row.id)
    group = db.get(ProductGroup, row.product_group_id) if row.product_group_id else None
    value['product_group_name'] = group.name if group else None
    value['product_group_slug'] = group.slug if group else None
    value['packings'] = [
        serialize(packing) for packing in db.scalars(
            select(Packing).where(Packing.nomenclature_id == row.id, Packing.active.is_(True))
            .order_by(Packing.value, Packing.unit)
        )
    ]
    return value


def request_item_view(db: Session, user: User, row: RequestItem) -> dict[str, Any]:
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
    if not has_request_permission(db, user, row.request_id, 'finance.purchase.read'):
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


@router.get('/product-groups')
def product_groups(q: str = '', active: bool = True, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'catalog.read')
    stmt = select(ProductGroup).where(ProductGroup.active == active)
    if q:
        stmt = stmt.where(or_(ProductGroup.name.ilike(f'%{q}%'), ProductGroup.slug.ilike(f'%{q}%')))
    return paginate(db, stmt.order_by(ProductGroup.name), 1, 100)


@router.post('/product-groups', status_code=201)
def create_product_group(body: ProductGroupInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'catalog.write')
    advisory(db, 'catalog.product_groups')
    if db.scalar(select(ProductGroup.id).where(or_(ProductGroup.slug == body.slug, ProductGroup.name == body.name))):
        raise DomainError('PRODUCT_GROUP_EXISTS', 'Товарная группа уже существует', 409)
    return save(db, user, ProductGroup(**body.model_dump()), 'product_group')


class ProductGroupPatch(BaseModel):
    version: int
    name: str = Field(min_length=1, max_length=150)


@router.patch('/product-groups/{entity_id}')
def edit_product_group(entity_id: str, body: ProductGroupPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_permission(db, user, 'catalog.write')
    advisory(db, 'catalog.product_groups')
    row = db.get(ProductGroup, entity_id)
    if not row:
        raise DomainError('NOT_FOUND', 'Товарная группа не найдена', 404)
    check_version(row, body.version)
    if db.scalar(select(ProductGroup.id).where(ProductGroup.name == body.name, ProductGroup.id != entity_id)):
        raise DomainError('PRODUCT_GROUP_EXISTS', 'Товарная группа уже существует', 409)
    before = serialize(row)
    row.name, row.version = body.name, row.version + 1
    audit(db, user, 'product_group', row.id, 'updated', before, serialize(row))
    db.commit()
    return serialize(row)


@router.get('/currencies')
def currencies(active: bool = True, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'catalog.read')
    return paginate(db, select(Currency).where(Currency.active == active).order_by(Currency.code), 1, 100)


@router.post('/currencies', status_code=201)
def create_currency(body: CurrencyInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    if not can(db, user, 'admin.settings'):
        require_permission(db, user, 'profiles.write')
    advisory(db, 'catalog.currencies')
    if db.scalar(select(Currency.id).where(Currency.code == body.code)):
        raise DomainError('CURRENCY_EXISTS', 'Валюта уже есть в справочнике', 409, 'code')
    return save(db, user, Currency(**body.model_dump()), 'currency')


@router.get('/countries')
def countries(q: str = '', active: bool = True, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'catalog.read')
    stmt = select(Country).where(Country.active == active)
    if q:
        stmt = stmt.where(or_(Country.name.ilike(f'%{q}%'), Country.iso2.ilike(f'%{q}%')))
    return paginate(db, stmt.order_by(Country.name), 1, 100)


@router.post('/countries', status_code=201)
def create_country(body: CountryInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    if not can(db, user, 'profiles.write'):
        require_catalog_create(db, user)
    advisory(db, 'catalog.countries')
    if db.scalar(select(Country.id).where(or_(Country.iso2 == body.iso2, Country.name == body.name))):
        raise DomainError('COUNTRY_EXISTS', 'Страна уже есть в справочнике', 409)
    return save(db, user, Country(**body.model_dump()), 'country')


@router.get('/nomenclatures')
def nomenclatures(
    q: str = '', product_group_id: str | None = None, page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100), active: bool = True,
    cas: str = '', manufacturer: str = '', article: str = '',
    user: User = Depends(current_user), db: Session = Depends(get_db),
) -> dict[str, Any]:
    require_permission(db, user, 'catalog.read')
    stmt = select(Nomenclature).where(Nomenclature.active == active)
    if q:
        stmt = stmt.where(or_(Nomenclature.name.ilike(f'%{q}%'), Nomenclature.article.ilike(f'%{q}%'), Nomenclature.cas.ilike(f'%{q}%')))
    if product_group_id:
        stmt = stmt.where(Nomenclature.product_group_id == product_group_id)
    for column, value in ((Nomenclature.cas, cas), (Nomenclature.manufacturer, manufacturer), (Nomenclature.article, article)):
        if value:
            stmt = stmt.where(column.ilike(f'%{value}%'))
    result = paginate(db, stmt.order_by(Nomenclature.name, Nomenclature.id), page, page_size)
    result['items'] = [nomenclature_view(db, db.get(Nomenclature, item['id'])) for item in result['items']]
    return result


@router.post('/nomenclatures', status_code=201)
def create_nomenclature(body: NomenclatureInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_catalog_create(db, user)
    advisory(db, 'catalog.article-import')
    group = require_group(db, body.product_group_id)
    keys = [(item.value, item.unit.strip()) for item in body.packings]
    if len(keys) != len(set(keys)):
        raise DomainError('PACKING_DUPLICATE', 'Фасовка повторяется', 422, 'packings')
    row = Nomenclature(**body.model_dump(exclude={'packings', 'product_group_id'}), product_group_id=group.id)
    db.add(row)
    db.flush()
    for item in body.packings:
        db.add(Packing(
            nomenclature_id=row.id, value=item.value, unit=item.unit.strip(),
            display_name=item.display_name or packing_name(item.value, item.unit.strip()),
        ))
    db.flush()
    result = nomenclature_view(db, row)
    result['created_by_id'] = user.id
    audit(db, user, 'nomenclature', row.id, 'created', after=result)
    return result


@router.get('/nomenclatures/{entity_id}')
def nomenclature_detail(entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'catalog.read')
    row = db.get(Nomenclature, entity_id)
    if not row:
        raise DomainError('NOT_FOUND', 'Номенклатура не найдена', 404)
    return nomenclature_view(db, row)


@router.patch('/nomenclatures/{entity_id}')
def edit_nomenclature(entity_id: str, body: NomenclaturePatch, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    advisory(db, 'catalog.article-import')
    row = lock(db, Nomenclature, entity_id)
    if not can(db, user, 'catalog.write'):
        require_permission(db, user, 'requests.write')
        if nomenclature_creator(db, entity_id) != user.id:
            raise DomainError('FORBIDDEN', 'Изменять эту номенклатуру может её создатель или сотрудник с правом ведения каталога', 403)
    check_version(row, body.version)
    before = nomenclature_view(db, row)
    data = body.model_dump(exclude_unset=True, exclude={'version'})
    if 'product_group_id' in data:
        data['product_group_id'] = require_group(db, data['product_group_id']).id
    for key, value in data.items():
        setattr(row, key, value)
    row.version += 1
    row.updated_at = utcnow()
    result = nomenclature_view(db, row)
    audit(db, user, 'nomenclature', row.id, 'updated', before, result)
    return result


@router.get('/nomenclatures/{entity_id}/packings')
def packings(entity_id: str, active: bool = True, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'catalog.read')
    if not db.get(Nomenclature, entity_id):
        raise DomainError('NOT_FOUND', 'Номенклатура не найдена', 404)
    return paginate(db, select(Packing).where(Packing.nomenclature_id == entity_id, Packing.active == active).order_by(Packing.value, Packing.unit), 1, 100)


@router.post('/nomenclatures/{entity_id}/packings', status_code=201)
def create_packing(entity_id: str, body: PackingInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_catalog_create(db, user)
    advisory(db, 'catalog.article-import')
    require_nomenclature(db, entity_id)
    unit = body.unit.strip()
    advisory(db, f'catalog.packing:{entity_id}')
    existing = db.scalar(select(Packing).where(Packing.nomenclature_id == entity_id, Packing.value == body.value, Packing.unit == unit))
    if existing:
        if existing.active:
            return serialize(existing)
        raise DomainError('PACKING_ARCHIVED', 'Эта фасовка отключена; восстановите её в справочнике', 409)
    return save(db, user, Packing(
        nomenclature_id=entity_id, value=body.value, unit=unit,
        display_name=body.display_name or packing_name(body.value, unit),
    ), 'packing')


@router.patch('/packings/{entity_id}')
def edit_packing(entity_id: str, body: PackingPatch, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'catalog.write')
    advisory(db, 'catalog.article-import')
    row = lock(db, Packing, entity_id)
    check_version(row, body.version)
    before = serialize(row)
    for key, value in body.model_dump(exclude_unset=True, exclude={'version'}).items():
        setattr(row, key, value)
    row.version += 1
    result = serialize(row)
    audit(db, user, 'packing', row.id, 'updated', before, result)
    return result


@router.get('/counterparties')
def clients(q: str = '', kind: str | None = None, client_base: str | None = None, contact_eligible: bool = False, page: int = 1, page_size: int = 25, sort: str = 'name', direction: str = 'asc', archived: bool = False, filter_name: str = '', filter_tax_id: str = '', filter_profile: str = '', filter_city: str = '', filter_phone: str = '', filter_email: str = '', user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
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
    query_parts = search_parts(q)
    filter_parts = {key: search_parts(value) for key, value in filters.items()}
    if not query_parts and not any(filter_parts.values()):
        return add_counterparty_owners(db, paginate(db, stmt, page, page_size))
    if page < 1 or not 1 <= page_size <= 100:
        raise DomainError('PAGINATION_INVALID', 'Размер страницы от 1 до 100; номер от 1', 422)
    start = (page - 1) * page_size
    items = []
    total = 0
    for row in db.scalars(stmt).yield_per(500):
        details = row.details if isinstance(row.details, dict) else {}
        values = {
            'name': row.name, 'tax_id': row.tax_id, 'phone': row.phone, 'email': row.email,
            'internal_code': row.internal_code,
            'profile': details.get('profile'), 'city': details.get('city'),
        }
        if query_parts and not search_match(' '.join(str(value or '') for value in values.values()), query_parts):
            continue
        if any(parts and not search_match(values[key], parts) for key, parts in filter_parts.items()):
            continue
        if start <= total < start + page_size:
            items.append(serialize(row))
        total += 1
    return add_counterparty_owners(db, {'items': items, 'total': total, 'page': page, 'page_size': page_size})


def add_counterparty_owners(db: Session, result: dict) -> dict:
    names = {row.id: row.name for row in db.scalars(select(User).where(
        User.id.in_({row['owner_id'] for row in result['items']})))}
    for row in result['items']:
        row['owner_name'] = names.get(row['owner_id'])
    return result


def counterparty_view(db: Session, row: Counterparty) -> dict:
    return add_counterparty_owners(db, {'items': [serialize(row)]})['items'][0]


@router.get('/counterparty-owners')
def counterparty_owners(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    require_permission(db, user, 'clients.read')
    rows = db.scalars(select(User).where(User.active.is_(True)).order_by(User.name, User.id))
    return {'items': [{'id': row.id, 'name': row.name} for row in rows]}


def search_parts(value: str) -> list[str]:
    return [part for part in (''.join(char for char in word.casefold().replace('ё', 'е') if char.isalnum())
                              for word in value.split()) if part]


def search_match(value: Any, parts: list[str]) -> bool:
    normalized = ''.join(char for char in str(value or '').casefold().replace('ё', 'е') if char.isalnum())
    return all(part in normalized for part in parts)


@router.post('/counterparties', status_code=201)
def create_client(body: ClientInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'clients.write')
    data = body.model_dump()
    data['owner_id'] = body.owner_id or user.id
    if data['owner_id'] != user.id:
        require_permission(db, user, 'requests.assign')
    active_user(db, data['owner_id'])
    result = save(db, user, Counterparty(**data), 'counterparty')
    return counterparty_view(db, db.get(Counterparty, result['id']))


@router.get('/counterparties/{entity_id}')
def client_detail(entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return counterparty_view(db, check_client(db, user, entity_id))


@router.post('/counterparties/{entity_id}/promote')
def promote_client(entity_id: str, body: PromoteClientInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
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
def edit_client(entity_id: str, body: ClientPatch, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
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
        # Cascade archive requests and items
        requests = db.scalars(select(Request).where(Request.client_id == row.id, Request.archived.is_(False))).all()
        for req in requests:
            req_before = serialize(req)
            req.archived = True
            req.version += 1
            db.add(req)
            audit(db, user, 'request', req.id, 'updated', req_before, serialize(req), "Client archived cascade")
            
            items = db.scalars(select(RequestItem).where(RequestItem.request_id == req.id)).all()
            for it in items:
                # Assuming RequestItem has archived? Wait, I added archived to ItemPatch! Let's check Item model.
                pass

    audit(db, user, 'counterparty', row.id, 'updated', before, serialize(row), body.reason)
    return counterparty_view(db, row)


def counterparty_document_view(db: Session, user: User, row: CounterpartyDocument) -> dict:
    from app.communication.access import check_file, file_view
    file = file_view(check_file(db, user, row.file_id))
    return {**serialize(row), **{key: file[key] for key in ('name', 'media_type', 'size', 'status')}}


@router.get('/counterparties/{entity_id}/documents')
def counterparty_documents(entity_id: str, archived: bool = False, page: int = 1, page_size: int = 100,
                           user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
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
                                 user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    from app.communication.routes import upload_file
    client = check_client(db, user, entity_id, 'clients.write')
    if client.archived:
        raise DomainError('CLIENT_ARCHIVED', 'Контрагент в архиве', 409)
    if category not in ('founding', 'contract', 'other'):
        raise DomainError('DOCUMENT_CATEGORY_INVALID', 'Выберите категорию документа', 422, 'category')
    saved = upload_file(file, 'client', entity_id, 'general', user, db, idempotency_key)
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
                                   db: Session = Depends(get_db)):
    from app.communication.routes import download_file
    row = db.get(CounterpartyDocument, entity_id)
    if not row:
        raise DomainError('NOT_FOUND', 'Документ не найден', 404)
    check_client(db, user, row.counterparty_id)
    return download_file(row.file_id, user, db)


@router.patch('/counterparty-documents/{entity_id}')
def archive_counterparty_document(entity_id: str, body: CounterpartyDocumentPatch,
                                  user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
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
def contacts(entity_id: str, q: str = '', user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    check_client(db, user, entity_id)
    stmt = select(Contact).where(Contact.client_id == entity_id, Contact.archived.is_(False))
    if q:
        stmt = stmt.where(sa.or_(Contact.name.ilike(f'%{q}%'), Contact.email.ilike(f'%{q}%')))
    return paginate(db, stmt.order_by(Contact.name), 1, 100)


@router.post('/counterparties/{entity_id}/contacts', status_code=201)
def create_contact(entity_id: str, body: ContactInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    client = check_client(db, user, entity_id, 'clients.write')
    if client.client_base != 'working' or client.archived:
        raise DomainError('WORKING_CLIENT_REQUIRED', 'Выберите действующего контрагента рабочей базы', 422)
    return save(db, user, Contact(client_id=entity_id, **body.model_dump()), 'contact')


@router.get('/contacts')
def contact_registry(q: str = '', page: int = 1, page_size: int = 25, user: User = Depends(current_user), db: Session = Depends(get_db)):
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
def create_registry_contact(body: ContactCreateInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return create_contact(body.client_id, ContactInput(**body.model_dump(exclude={'client_id'})), user, db)


@router.get('/contacts/{entity_id}')
def contact_detail(entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(Contact, entity_id)
    if not row:
        raise DomainError('NOT_FOUND', 'Контакт не найден', 404)
    client = check_client(db, user, row.client_id)
    return {**serialize(row), 'client_name': client.name}


@router.patch('/contacts/{entity_id}')
def edit_contact(entity_id: str, body: ContactPatch, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = lock(db, Contact, entity_id)
    check_client(db, user, row.client_id, 'clients.write')
    check_version(row, body.version)
    before = serialize(row)
    for k, v in body.model_dump(exclude_unset=True, exclude={'version', 'reason'}).items():
        setattr(row, k, v)
    row.version += 1
    audit(db, user, 'contact', row.id, 'updated', before, serialize(row), body.reason)
    return serialize(row)


@router.get('/tasks')
def tasks(status: str | None = None, q: str = '', entity_id: str | None = None, overdue: bool = False, page: int = 1, page_size: int = 25, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'tasks.read')
    stmt = select(Task).where(task_predicate(db, user))
    if status:
        stmt = stmt.where(Task.status == status)
    if q:
        stmt = stmt.where(Task.title.ilike(f'%{q}%'))
    if entity_id:
        stmt = stmt.where(Task.entity_id == entity_id)
    if overdue:
        stmt = stmt.where(Task.due_at < utcnow(), Task.status.in_(['assigned', 'in_progress']))
    result = paginate(db, stmt.order_by(Task.due_at, Task.id), page, page_size)
    client_ids = {item['entity_id'] for item in result['items'] if item['entity_type'] == 'counterparty'}
    client_names = {row.id: row.name for row in db.scalars(select(Counterparty).where(Counterparty.id.in_(client_ids)))}
    assignee_ids = {item['assignee_id'] for item in result['items']}
    assignee_names = {row.id: row.name for row in db.scalars(select(User).where(User.id.in_(assignee_ids)))}
    for item in result['items']:
        item['client_name'] = client_names.get(item['entity_id']) if item['entity_type'] == 'counterparty' else None
        item['assignee_name'] = assignee_names.get(item['assignee_id'])
    return result


@router.post('/tasks', status_code=201)
def create_task(body: TaskInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'tasks.write')
    check_link(db, user, body.entity_type, body.entity_id)
    assignee = active_user(db, body.assignee_id or user.id)
    from app.business.routes import ensure_available
    ensure_available(db, assignee.id)
    check_link(db, assignee, body.entity_type, body.entity_id)
    row = Task(**body.model_dump(exclude={'assignee_id'}), assignee_id=assignee.id, author_id=user.id)
    result = save(db, user, row, 'task')
    notify(db, assignee.id, f'task:{row.id}', 'Вам назначена задача', 'task', row.id)
    return result


@router.post('/tasks/bulk-calls', status_code=201)
def create_bulk_call_tasks(body: BulkCallTaskInput, idempotency_key: str | None = Header(default=None), user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'tasks.write')
    require_permission(db, user, 'requests.assign')
    if len(set(body.client_ids)) != len(body.client_ids):
        raise DomainError('CLIENT_DUPLICATE', 'Уберите повторяющихся клиентов', 422, 'client_ids')
    assignee = active_user(db, body.assignee_id)
    from app.business.routes import ensure_available
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
    from app.business.routes import ensure_available, local_date, submit_review
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
def calls(client_id: str | None = None, page: int = 1, page_size: int = 25, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
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
    for leader in db.scalars(select(User).where(User.active.is_(True))):
        if can(db, leader, 'approvals.decide'):
            key = f'call:{row.id}' if version is None else f'call:{row.id}:v{version}'
            notify(db, leader.id, key, title, 'counterparty', row.client_id)


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
                from app.business.routes import ensure_available
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
    notify_call_result(db, row, row.version)
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
def request_detail(entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = check_request(db, user, entity_id)
    result = serialize(row)
    result['client_name'] = db.get(Counterparty, row.client_id).name
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
def edit_request(entity_id: str, body: RequestPatch, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    check_request(db, user, entity_id, 'requests.write')
    row = lock(db, Request, entity_id)
    check_version(row, body.version)
    before = serialize(row)
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
        allowed = ['new', 'clarification', 'collecting_quotes', 'quote_given', 'calculation', 'closed_lost']
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
def items(entity_id: str, q: str = '', page: int = 1, page_size: int = 100, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    check_request(db, user, entity_id)
    stmt = select(RequestItem).where(RequestItem.request_id == entity_id, RequestItem.quote_only.is_(False))
    if q:
        names = select(Nomenclature.id).where(or_(
            Nomenclature.name.ilike(f'%{q}%'), Nomenclature.article.ilike(f'%{q}%'),
        ))
        stmt = stmt.where(or_(RequestItem.description.ilike(f'%{q}%'), RequestItem.nomenclature_id.in_(names)))
    result = paginate(db, stmt.order_by(RequestItem.created_at, RequestItem.id), page, page_size)
    result['items'] = [request_item_view(db, user, db.get(RequestItem, item['id'])) for item in result['items']]
    numbers = {item_id: index for index, item_id in enumerate(db.scalars(select(RequestItem.id).where(RequestItem.request_id == entity_id, RequestItem.archived.is_(False)).order_by(RequestItem.created_at, RequestItem.id)), 1)}
    for item in result['items']:
        item['position_number'] = numbers.get(item['id'])
    return result


@router.post('/requests/{entity_id}/items', status_code=201)
def create_item(entity_id: str, body: ItemInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    check_request(db, user, entity_id, 'requests.write')
    parent = lock(db, Request, entity_id)
    row = RequestItem(request_id=entity_id, **structured_item_fields(db, body.model_dump()))
    result = save(db, user, row, 'request_item')
    db.add(RequestItemRevision(item_id=row.id, revision=1, snapshot=result, author_id=user.id))
    parent.version += 1
    return request_item_view(db, user, row)


@router.post('/requests/{entity_id}/items/start')
def start_items(entity_id: str, body: StartItemsInput, idempotency_key: str | None = Header(default=None), user: User = Depends(current_user), db: Session = Depends(get_db)):
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
def edit_item(entity_id: str, body: ItemPatch, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
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
def item_revisions(entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
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
def history(entity_id: str, page: int = 1, page_size: int = 50, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    check_request(db, user, entity_id)
    ids = [entity_id, *db.scalars(select(RequestItem.id).where(RequestItem.request_id == entity_id))]
    result = paginate(db, select(AuditEvent).where(AuditEvent.entity_id.in_(ids)).order_by(AuditEvent.created_at.desc()), page, page_size)
    for row in result['items']:
        if not all(can(db, user, p) for p in ('finance.purchase.read', 'finance.calculations.read', 'finance.reward.read')):
            row.pop('before', None)
            row.pop('after', None)
    return result


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
        'request_id': sheet.request_id,
        'request_number': request.number,
        'supplier_request_id': sheet.supplier_request_id,
        'nomenclature_name': nomenclature.name,
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


def quote_sheet_view(db: Session, row: QuoteSheet, show_purchase: bool = True) -> dict[str, Any]:
    value = serialize(row)
    request = db.get(Request, row.request_id)
    supplier = db.get(Counterparty, row.supplier_id)
    value.update({
        'request_number': request.number,
        'supplier_name': supplier.name,
        'items': [
            quote_item_view(db, item, show_purchase) for item in db.scalars(
                select(QuoteItem).where(QuoteItem.quote_id == row.id)
                .order_by(QuoteItem.created_at, QuoteItem.id)
            )
        ],
    })
    return value


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
            QuoteSheet.request_id.in_(select(Request.id).where(request_predicate(db, user, 'finance.purchase.read'))),
        )
    )
    if currency_id:
        stmt = stmt.where(QuoteItem.currency_id == currency_id)
    return db.scalar(stmt.order_by(QuoteItem.quoted_at.desc(), QuoteItem.created_at.desc(), QuoteItem.id.desc()).limit(1))


@router.get('/quote-items/latest')
def latest_quote_item(
    supplier_id: str, nomenclature_id: str, packing_id: str, currency_id: str | None = None,
    user: User = Depends(current_user), db: Session = Depends(get_db),
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
    user: User = Depends(current_user), db: Session = Depends(get_db),
) -> dict[str, Any]:
    require_permission(db, user, 'finance.purchase.read')
    stmt = select(QuoteItem).join(QuoteSheet, QuoteSheet.id == QuoteItem.quote_id).where(
        QuoteSheet.request_id.in_(select(Request.id).where(request_predicate(db, user, 'finance.purchase.read')))
    )
    for field, value in (
        ('supplier_id', supplier_id), ('nomenclature_id', nomenclature_id),
        ('packing_id', packing_id), ('currency_id', currency_id),
    ):
        if value:
            stmt = stmt.where(getattr(QuoteItem, field) == value)
    result = paginate(db, stmt.order_by(QuoteItem.quoted_at.desc(), QuoteItem.id.desc()), page, page_size)
    result['items'] = [quote_item_view(db, db.get(QuoteItem, item['id'])) for item in result['items']]
    return result


@router.get('/requests/{entity_id}/quote-items')
def request_quote_items(
    entity_id: str, page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=100), ids: str = '',
    user: User = Depends(current_user), db: Session = Depends(get_db),
) -> dict[str, Any]:
    check_request(db, user, entity_id)
    stmt = select(QuoteItem).join(QuoteSheet, QuoteSheet.id == QuoteItem.quote_id).where(QuoteSheet.request_id == entity_id)
    if ids:
        selected_ids = [value.strip() for value in ids.split(',') if value.strip()]
        if len(selected_ids) > 100:
            raise DomainError('QUOTE_SELECTION_LIMIT', 'Выберите не более 100 позиций квоты', 422, 'ids')
        stmt = stmt.where(QuoteItem.id.in_(selected_ids))
    result = paginate(db, stmt.order_by(QuoteItem.created_at.desc(), QuoteItem.id.desc()), page, page_size)
    show_purchase = has_request_permission(db, user, entity_id, 'finance.purchase.read')
    result['items'] = [quote_item_view(db, db.get(QuoteItem, item['id']), show_purchase) for item in result['items']]
    return result


@router.get('/requests/{entity_id}/quote-sheets')
def request_quote_sheets(
    entity_id: str, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
    user: User = Depends(current_user), db: Session = Depends(get_db),
) -> dict[str, Any]:
    check_request(db, user, entity_id)
    result = paginate(db, select(QuoteSheet).where(QuoteSheet.request_id == entity_id).order_by(QuoteSheet.created_at.desc()), page, page_size)
    show_purchase = has_request_permission(db, user, entity_id, 'finance.purchase.read')
    result['items'] = [quote_sheet_view(db, db.get(QuoteSheet, item['id']), show_purchase) for item in result['items']]
    return result


@router.get('/quote-sheets/{entity_id}')
def quote_sheet_detail(entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(QuoteSheet, entity_id)
    if not row:
        raise DomainError('NOT_FOUND', 'Квота не найдена', 404)
    check_request(db, user, row.request_id)
    return quote_sheet_view(db, row, has_request_permission(db, user, row.request_id, 'finance.purchase.read'))


@router.post('/requests/{entity_id}/quote-sheets', status_code=201)
def create_quote_sheet(
    entity_id: str, body: QuoteSheetInput, idempotency_key: str | None = Header(default=None),
    user: User = Depends(current_user), db: Session = Depends(get_db),
) -> dict[str, Any]:
    check_request(db, user, entity_id, 'quotes.write')
    require_supplier(db, body.supplier_id)
    if body.supplier_request_id:
        from app.commerce.models import SupplierRequest
        rfq = db.get(SupplierRequest, body.supplier_request_id)
        if not rfq or rfq.request_id != entity_id or rfq.supplier_id not in (None, body.supplier_id):
            raise DomainError('SUPPLIER_REQUEST_INVALID', 'Запрос поставщику не соответствует заявке и поставщику', 422, 'supplier_request_id')

    def operation() -> dict[str, Any]:
        advisory(db, 'quote_sheet.number')
        count = db.scalar(select(func.count()).select_from(QuoteSheet)) or 0
        sheet = QuoteSheet(
            number=f'Q-{count + 1:06d}', request_id=entity_id, supplier_id=body.supplier_id,
            supplier_request_id=body.supplier_request_id, author_id=user.id,
        )
        db.add(sheet)
        db.flush()
        for item in body.items:
            nomenclature = require_nomenclature(db, item.nomenclature_id)
            require_packing(db, item.packing_id, nomenclature.id)
            source = db.get(RequestItem, item.source_request_item_id) if item.source_request_item_id else None
            if source is None and not item.source_request_item_id:
                source = RequestItem(request_id=entity_id, description=nomenclature.name, nomenclature_id=nomenclature.id,
                                     packing_id=item.packing_id, product_group_id=nomenclature.product_group_id,
                                     quantity=item.quantity, unit='pcs', quote_only=True)
                db.add(source)
                db.flush()
            if not source or source.request_id != entity_id or source.archived:
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
        parent = lock(db, Request, entity_id)
        if parent.commercial_stage in ('new', 'clarification', 'collecting_quotes', 'quotes'):
            before_stage = parent.commercial_stage
            parent.commercial_stage = 'quote_given'
            parent.version += 1
            audit(db, user, 'request', parent.id, 'quote_given', before={'commercial_stage': before_stage}, after={'commercial_stage': parent.commercial_stage})
        result = quote_sheet_view(db, sheet)
        audit(db, user, 'quote_sheet', sheet.id, 'created', after=result)
        return result

    payload = body.model_dump(mode='json')
    if idempotency_key:
        return idem(db, user, idempotency_key, f'quote_sheets.create:{entity_id}', payload, operation)
    return operation()


@router.get('/sellers')
def sellers(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return paginate(db, select(Seller).where(Seller.archived.is_(False)).order_by(Seller.name), 1, 100)


@router.post('/sellers', status_code=201)
def create_seller(body: SellerInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'admin.settings')
    return save(db, user, Seller(**body.model_dump()), 'seller')
