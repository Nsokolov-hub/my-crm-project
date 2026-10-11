"""Product groups, currencies, countries, nomenclature and packings."""

from collections import defaultdict
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db, utcnow
from app.core.errors import DomainError
from app.core.models import AuditEvent, User
from app.core.security import can, current_user, require_permission
from app.core.service import advisory, audit, check_version, lock, prefetch, serialize
from app.core.service import page as paginate
from app.crm.common import save
from app.crm.models import Country, Currency, Nomenclature, Packing, ProductGroup
from app.crm.schemas import (
    CountryInput,
    CurrencyInput,
    NomenclatureInput,
    NomenclaturePatch,
    PackingInput,
    PackingPatch,
    ProductGroupInput,
)

router = APIRouter(tags=['CRM'])


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


def nomenclature_views(db: Session, rows: list[Nomenclature]) -> list[dict[str, Any]]:
    """Views for a page of nomenclatures with a fixed number of queries."""
    ids = [row.id for row in rows]
    creators: dict[str, str | None] = {}
    for entity_id, actor_id in db.execute(select(AuditEvent.entity_id, AuditEvent.actor_id).where(
        AuditEvent.entity_type == 'nomenclature', AuditEvent.action == 'created', AuditEvent.entity_id.in_(ids),
    ).order_by(AuditEvent.created_at, AuditEvent.id)):
        creators.setdefault(entity_id, actor_id)
    groups = prefetch(db, ProductGroup, (row.product_group_id for row in rows))
    packings: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for packing in db.scalars(select(Packing).where(Packing.nomenclature_id.in_(ids), Packing.active.is_(True))
                              .order_by(Packing.value, Packing.unit)):
        packings[packing.nomenclature_id].append(serialize(packing))
    views = []
    for row in rows:
        value = serialize(row)
        value['created_by_id'] = creators.get(row.id)
        group = groups.get(row.product_group_id) if row.product_group_id else None
        value['product_group_name'] = group.name if group else None
        value['product_group_slug'] = group.slug if group else None
        value['packings'] = packings[row.id]
        views.append(value)
    return views


def nomenclature_view(db: Session, row: Nomenclature) -> dict[str, Any]:
    return nomenclature_views(db, [row])[0]


@router.get('/product-groups')
def product_groups(q: str = '', active: bool = True, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    require_permission(db, user, 'catalog.read')
    stmt = select(ProductGroup).where(ProductGroup.active == active)
    if q:
        stmt = stmt.where(or_(ProductGroup.name.ilike(f'%{q}%'), ProductGroup.slug.ilike(f'%{q}%')))
    return paginate(db, stmt.order_by(ProductGroup.name), 1, 100)


@router.post('/product-groups', status_code=201)
def create_product_group(body: ProductGroupInput, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    require_permission(db, user, 'catalog.write')
    advisory(db, 'catalog.product_groups')
    if db.scalar(select(ProductGroup.id).where(or_(ProductGroup.slug == body.slug, ProductGroup.name == body.name))):
        raise DomainError('PRODUCT_GROUP_EXISTS', 'Товарная группа уже существует', 409)
    return save(db, user, ProductGroup(**body.model_dump()), 'product_group')


class ProductGroupPatch(BaseModel):
    version: int
    name: str = Field(min_length=1, max_length=150)


@router.patch('/product-groups/{entity_id}')
def edit_product_group(entity_id: str, body: ProductGroupPatch, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")):
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
def currencies(active: bool = True, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    require_permission(db, user, 'catalog.read')
    return paginate(db, select(Currency).where(Currency.active == active).order_by(Currency.code), 1, 100)


@router.post('/currencies', status_code=201)
def create_currency(body: CurrencyInput, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    if not can(db, user, 'admin.settings'):
        require_permission(db, user, 'profiles.write')
    advisory(db, 'catalog.currencies')
    if db.scalar(select(Currency.id).where(Currency.code == body.code)):
        raise DomainError('CURRENCY_EXISTS', 'Валюта уже есть в справочнике', 409, 'code')
    return save(db, user, Currency(**body.model_dump()), 'currency')


@router.get('/countries')
def countries(q: str = '', active: bool = True, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    require_permission(db, user, 'catalog.read')
    stmt = select(Country).where(Country.active == active)
    if q:
        stmt = stmt.where(or_(Country.name.ilike(f'%{q}%'), Country.iso2.ilike(f'%{q}%')))
    return paginate(db, stmt.order_by(Country.name), 1, 100)


@router.post('/countries', status_code=201)
def create_country(body: CountryInput, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
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
    user: User = Depends(current_user), db: Session = Depends(get_db, scope="function"),
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
    result['items'] = nomenclature_views(db, [db.get(Nomenclature, item['id']) for item in result['items']])
    return result


@router.post('/nomenclatures', status_code=201)
def create_nomenclature(body: NomenclatureInput, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
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
def nomenclature_detail(entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    require_permission(db, user, 'catalog.read')
    row = db.get(Nomenclature, entity_id)
    if not row:
        raise DomainError('NOT_FOUND', 'Номенклатура не найдена', 404)
    return nomenclature_view(db, row)


@router.patch('/nomenclatures/{entity_id}')
def edit_nomenclature(entity_id: str, body: NomenclaturePatch, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
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
def packings(entity_id: str, active: bool = True, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    require_permission(db, user, 'catalog.read')
    if not db.get(Nomenclature, entity_id):
        raise DomainError('NOT_FOUND', 'Номенклатура не найдена', 404)
    return paginate(db, select(Packing).where(Packing.nomenclature_id == entity_id, Packing.active == active).order_by(Packing.value, Packing.unit), 1, 100)


@router.post('/nomenclatures/{entity_id}/packings', status_code=201)
def create_packing(entity_id: str, body: PackingInput, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
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
def edit_packing(entity_id: str, body: PackingPatch, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
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
