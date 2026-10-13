"""Registry of manufacturers: numbers for Excel forms, every known spelling, umbrella brands and merging.

A spelling names one entry. Nomenclature keeps the entry's name as text for documents and requests to
suppliers, so renaming and merging rewrite that text. A merged entry stays as history and points to the
entry that took its spellings.
"""

import re
from collections import defaultdict
from typing import Any, cast

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.commerce.models import Manufacturer, ManufacturerAlias, Product
from app.core.db import get_db
from app.core.errors import DomainError
from app.core.models import User
from app.core.security import can, current_user, require_permission
from app.core.service import advisory, audit, check_version, idem, lock, prefetch, serialize
from app.core.service import page as paginate
from app.crm.models import Nomenclature
from app.crm.schemas import ManufacturerInput, ManufacturerMergeInput, ManufacturerPatch

router = APIRouter(tags=['CRM'])
REGISTRY_LOCK = 'manufacturer.registry'
# Words that do not tell manufacturers apart when looking for duplicates.
LEGAL_WORDS = {
    'pvt', 'ltd', 'limited', 'private', 'inc', 'llc', 'gmbh', 'co', 'corp', 'corporation', 'company', 'sa', 'ag',
    'srl', 'spa', 'bv', 'oy', 'kg', 'ooo', 'ооо', 'ао', 'зао', 'пао', 'оао',
}


def normalized(value: str) -> str:
    return ' '.join(value.casefold().split())


def simplified(value: str) -> str:
    return ''.join(word for word in re.findall(r'[0-9a-zа-яё]+', value.casefold()) if word not in LEGAL_WORDS)


def current(db: Session, maker: Manufacturer) -> Manufacturer:
    """The entry in use: a merged entry leads to the one it was merged into."""
    seen = set()
    while maker.merged_into_id and maker.id not in seen:
        seen.add(maker.id)
        maker = db.get(Manufacturer, maker.merged_into_id) or maker
    return maker


def find_manufacturer(db: Session, reference: str | None) -> Manufacturer | None:
    """The entry for a registry number or any known spelling; None when the registry does not know it."""
    value = ' '.join((reference or '').split())
    if not value:
        return None
    if value.isdigit():
        maker = db.scalar(select(Manufacturer).where(Manufacturer.internal_code == int(value)))
    else:
        alias = db.scalar(select(ManufacturerAlias).where(ManufacturerAlias.normalized_name == normalized(value)))
        maker = db.get(Manufacturer, alias.manufacturer_id) if alias else None
    return current(db, maker) if maker else None


def ensure_manufacturer(db: Session, user: User | None, reference: str) -> Manufacturer:
    """The entry for a number or a spelling; a new spelling becomes a new entry."""
    value = ' '.join(reference.split())
    found = find_manufacturer(db, value)
    if found:
        return found
    if value.isdigit():
        raise DomainError('MANUFACTURER_CODE', f'Производителя с номером {value} нет в реестре', 422, 'manufacturer')
    advisory(db, REGISTRY_LOCK)
    found = find_manufacturer(db, value)
    if found:
        return found
    maker = Manufacturer(name=value, normalized_name=normalized(value))
    db.add(maker)
    db.flush()
    db.add(ManufacturerAlias(manufacturer_id=maker.id, name=value, normalized_name=normalized(value)))
    db.flush()
    audit(db, user, 'manufacturer', maker.id, 'created', after=serialize(maker))
    return maker


def manufacturer_fields(db: Session, user: User, data: dict[str, Any]) -> dict[str, Any]:
    """Nomenclature fields with the registry entry: chosen by id, or found or added by its spelling."""
    if data.get('manufacturer_id'):
        chosen = db.get(Manufacturer, data['manufacturer_id'])
        if not chosen:
            raise DomainError('MANUFACTURER_INVALID', 'Выберите производителя из реестра', 422, 'manufacturer_id')
        maker = current(db, chosen)
    elif (data.get('manufacturer') or '').strip():
        maker = ensure_manufacturer(db, user, data['manufacturer'])
    else:
        return {**data, 'manufacturer_id': None, 'manufacturer': None}
    return {**data, 'manufacturer_id': maker.id, 'manufacturer': maker.name}


def manufacturer_views(db: Session, rows: list[Manufacturer]) -> list[dict[str, Any]]:
    ids = [row.id for row in rows]
    spellings: dict[str, list[str]] = defaultdict(list)
    for alias in db.scalars(select(ManufacturerAlias).where(ManufacturerAlias.manufacturer_id.in_(ids))
                            .order_by(ManufacturerAlias.name)):
        spellings[alias.manufacturer_id].append(alias.name)
    counts: dict[str | None, int] = {key: count for key, count in db.execute(
        select(Nomenclature.manufacturer_id, func.count()).where(
            Nomenclature.manufacturer_id.in_(ids), Nomenclature.active.is_(True)).group_by(Nomenclature.manufacturer_id))}
    members: dict[str | None, int] = {key: count for key, count in db.execute(
        select(Manufacturer.umbrella_id, func.count()).where(
            Manufacturer.umbrella_id.in_(ids), Manufacturer.merged_into_id.is_(None)).group_by(Manufacturer.umbrella_id))}
    related = prefetch(db, Manufacturer, [*(row.umbrella_id for row in rows), *(row.merged_into_id for row in rows)])
    views = []
    for row in rows:
        value = serialize(row)
        value.pop('normalized_name', None)
        umbrella = related.get(row.umbrella_id) if row.umbrella_id else None
        merged = related.get(row.merged_into_id) if row.merged_into_id else None
        value.update(
            label=f'{row.internal_code} · {row.name}',
            aliases=[name for name in spellings[row.id] if normalized(name) != normalized(row.name)],
            nomenclatures=counts.get(row.id, 0), brand_members=members.get(row.id, 0),
            umbrella_name=umbrella.name if umbrella else None,
            merged_into_name=merged.name if merged else None,
        )
        views.append(value)
    return views


def changed(db: Session, statement: Any) -> int:
    """Rows touched by a bulk UPDATE."""
    return cast(CursorResult, db.execute(statement)).rowcount


def manufacturer_view(db: Session, row: Manufacturer) -> dict[str, Any]:
    return manufacturer_views(db, [row])[0]


def live(db: Session, entity_id: str, field: str | None = None) -> Manufacturer:
    row = db.get(Manufacturer, entity_id)
    if not row:
        raise DomainError('NOT_FOUND', 'Производитель не найден', 404, field)
    if row.merged_into_id:
        target = current(db, row)
        raise DomainError('MANUFACTURER_MERGED', f'Производитель объединён с «{target.name}» (№ {target.internal_code})',
                          409, field)
    return row


def check_umbrella(db: Session, maker: Manufacturer | None, umbrella_id: str | None) -> str | None:
    """One level of brands: an umbrella has no umbrella of its own, and an umbrella's members keep theirs."""
    if not umbrella_id:
        return None
    umbrella = live(db, umbrella_id, 'umbrella_id')
    if maker and umbrella.id == maker.id:
        raise DomainError('UMBRELLA_INVALID', 'Производитель не может быть зонтичным брендом самому себе', 422,
                          'umbrella_id')
    if umbrella.umbrella_id:
        raise DomainError('UMBRELLA_INVALID', f'«{umbrella.name}» сам входит в зонтичный бренд: выберите верхний бренд',
                          422, 'umbrella_id')
    if maker and db.scalar(select(Manufacturer.id).where(Manufacturer.umbrella_id == maker.id,
                                                         Manufacturer.merged_into_id.is_(None)).limit(1)):
        raise DomainError('UMBRELLA_INVALID', f'«{maker.name}» сам зонтичный бренд для других производителей', 422,
                          'umbrella_id')
    return umbrella.id


def claim_spellings(db: Session, maker_id: str | None, spellings: list[str]) -> dict[str, str]:
    """Normalized spelling → spelling, refused when another entry already has one of them."""
    wanted: dict[str, str] = {}
    for spelling in spellings:
        value = ' '.join(spelling.split())
        if value.isdigit():
            raise DomainError('MANUFACTURER_NAME', f'«{value}» похоже на номер: название не может состоять из цифр',
                              422, 'name')
        if value:
            wanted.setdefault(normalized(value), value)
    taken = db.scalars(select(ManufacturerAlias).where(ManufacturerAlias.normalized_name.in_(wanted),
                                                       ManufacturerAlias.manufacturer_id != (maker_id or ''))).first()
    if taken:
        owner = current(db, cast(Manufacturer, db.get(Manufacturer, taken.manufacturer_id)))
        raise DomainError('MANUFACTURER_EXISTS', f'Написание «{taken.name}» уже есть у производителя '
                          f'«{owner.name}» (№ {owner.internal_code})', 409, 'name')
    return wanted


@router.get('/manufacturers')
def manufacturers(
    q: str = '', merged: bool = False, umbrellas: bool = False, page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    user: User = Depends(current_user), db: Session = Depends(get_db, scope='function'),
) -> dict[str, Any]:
    """The registry; `umbrellas` leaves only entries that can be an umbrella brand."""
    require_permission(db, user, 'catalog.read')
    stmt = select(Manufacturer).where(
        Manufacturer.merged_into_id.is_not(None) if merged else Manufacturer.merged_into_id.is_(None))
    if umbrellas:
        stmt = stmt.where(Manufacturer.umbrella_id.is_(None))
    if text := ' '.join(q.split()):
        spelled = select(ManufacturerAlias.manufacturer_id).where(ManufacturerAlias.name.ilike(f'%{text}%'))
        stmt = stmt.where(or_(Manufacturer.name.ilike(f'%{text}%'), Manufacturer.id.in_(spelled),
                              *([Manufacturer.internal_code == int(text)] if text.isdigit() else [])))
    result = paginate(db, stmt.order_by(func.lower(Manufacturer.name), Manufacturer.id), page, page_size)
    result['items'] = manufacturer_views(db, [maker for row in result['items']
                                              if (maker := db.get(Manufacturer, row['id']))])
    return result


@router.get('/manufacturers/duplicates')
def duplicates(user: User = Depends(current_user), db: Session = Depends(get_db, scope='function')) -> dict[str, Any]:
    """Entries whose names or spellings look alike: the same letters, or one name starting the other."""
    require_permission(db, user, 'catalog.read')
    rows = db.scalars(select(Manufacturer).where(Manufacturer.merged_into_id.is_(None))).all()
    keys: dict[str, set[str]] = defaultdict(set)
    for alias in db.scalars(select(ManufacturerAlias).where(ManufacturerAlias.manufacturer_id.in_([r.id for r in rows]))):
        if key := simplified(alias.name):
            keys[alias.manufacturer_id].add(key)
    parent = {row.id: row.id for row in rows}

    def root(entity_id: str) -> str:
        while parent[entity_id] != entity_id:
            parent[entity_id] = parent[parent[entity_id]]
            entity_id = parent[entity_id]
        return entity_id

    entries = sorted((key, maker_id) for maker_id, found in keys.items() for key in found)
    for index, (key, maker_id) in enumerate(entries):
        # Sorted keys put a name right before the longer names that start with it.
        for other_key, other_id in entries[index + 1:]:
            if not (other_key == key or (len(key) >= 4 and other_key.startswith(key))):
                break
            parent[root(other_id)] = root(maker_id)
    groups: dict[str, list[Manufacturer]] = defaultdict(list)
    for row in rows:
        groups[root(row.id)].append(row)
    found = [manufacturer_views(db, sorted(group, key=lambda row: row.internal_code))
             for group in groups.values() if len(group) > 1]
    found.sort(key=lambda group: (-sum(row['nomenclatures'] for row in group), group[0]['name'].casefold()))
    return {'items': [{'id': group[0]['id'], 'manufacturers': group} for group in found], 'total': len(found)}


@router.get('/manufacturers/{entity_id}')
def manufacturer_detail(entity_id: str, user: User = Depends(current_user),
                        db: Session = Depends(get_db, scope='function')) -> dict[str, Any]:
    require_permission(db, user, 'catalog.read')
    row = db.get(Manufacturer, entity_id)
    if not row:
        raise DomainError('NOT_FOUND', 'Производитель не найден', 404)
    return manufacturer_view(db, row)


@router.post('/manufacturers', status_code=201)
def create_manufacturer(body: ManufacturerInput, user: User = Depends(current_user),
                        db: Session = Depends(get_db, scope='function')) -> dict[str, Any]:
    if not any(can(db, user, code) for code in ('catalog.write', 'requests.write')):
        raise DomainError('FORBIDDEN', 'Недостаточно прав для изменения справочника', 403)
    advisory(db, REGISTRY_LOCK)
    name = ' '.join(body.name.split())
    wanted = claim_spellings(db, None, [name, *body.aliases])
    maker = Manufacturer(name=name, normalized_name=normalized(name), umbrella_id=check_umbrella(db, None, body.umbrella_id))
    db.add(maker)
    db.flush()
    db.add_all(ManufacturerAlias(manufacturer_id=maker.id, name=value, normalized_name=key)
               for key, value in wanted.items())
    db.flush()
    result = manufacturer_view(db, maker)
    audit(db, user, 'manufacturer', maker.id, 'created', after=result)
    return result


@router.patch('/manufacturers/{entity_id}')
def edit_manufacturer(entity_id: str, body: ManufacturerPatch, user: User = Depends(current_user),
                      db: Session = Depends(get_db, scope='function')) -> dict[str, Any]:
    require_permission(db, user, 'catalog.write')
    advisory(db, REGISTRY_LOCK)
    live(db, entity_id)
    maker = lock(db, Manufacturer, entity_id)
    check_version(maker, body.version)
    before = manufacturer_view(db, maker)
    data = body.model_dump(exclude_unset=True, exclude={'version'})
    name = ' '.join(data.get('name', maker.name).split())
    existing = {row.normalized_name: row for row in db.scalars(
        select(ManufacturerAlias).where(ManufacturerAlias.manufacturer_id == maker.id))}
    others = data['aliases'] if 'aliases' in data else [row.name for row in existing.values()]
    # The previous name stays a known spelling, so files and requests written with it still find the entry.
    wanted = claim_spellings(db, maker.id, [name, maker.name, *others])
    for key, row in existing.items():
        if key not in wanted:
            db.delete(row)
    db.add_all(ManufacturerAlias(manufacturer_id=maker.id, name=value, normalized_name=key)
               for key, value in wanted.items() if key not in existing)
    if 'umbrella_id' in data:
        maker.umbrella_id = check_umbrella(db, maker, data['umbrella_id'])
    if name != maker.name:
        maker.name, maker.normalized_name = name, normalized(name)
        db.execute(update(Nomenclature).where(Nomenclature.manufacturer_id == maker.id).values(manufacturer=name))
    maker.version += 1
    db.flush()
    result = manufacturer_view(db, maker)
    audit(db, user, 'manufacturer', maker.id, 'updated', before, result)
    return result


@router.post('/manufacturers/{entity_id}/merge')
def merge_manufacturer(entity_id: str, body: ManufacturerMergeInput, user: User = Depends(current_user),
                       db: Session = Depends(get_db, scope='function')) -> dict[str, Any]:
    """Move spellings, nomenclature and brand members of a duplicate to the main entry."""
    require_permission(db, user, 'catalog.write')

    def operation() -> dict[str, Any]:
        advisory(db, REGISTRY_LOCK)
        live(db, entity_id)
        target = live(db, body.target_id, 'target_id')
        if target.id == entity_id:
            raise DomainError('MERGE_TARGET', 'Выберите другого производителя', 422, 'target_id')
        source = lock(db, Manufacturer, entity_id)
        check_version(source, body.version)
        target = lock(db, Manufacturer, target.id)
        if target.umbrella_id == source.id:
            target.umbrella_id = None
        moved = {
            'aliases': changed(db, update(ManufacturerAlias).where(ManufacturerAlias.manufacturer_id == source.id)
                               .values(manufacturer_id=target.id)),
            'nomenclatures': changed(db, update(Nomenclature).where(Nomenclature.manufacturer_id == source.id)
                                     .values(manufacturer_id=target.id, manufacturer=target.name)),
            'members': changed(db, update(Manufacturer).where(
                Manufacturer.umbrella_id == source.id, Manufacturer.id != target.id)
                .values(umbrella_id=target.umbrella_id or target.id)),
        }
        db.execute(update(Product).where(Product.manufacturer_id == source.id).values(manufacturer_id=target.id))
        source.merged_into_id, source.umbrella_id = target.id, None
        source.version += 1
        target.version += 1
        db.flush()
        audit(db, user, 'manufacturer', source.id, 'merged', after={'target_id': target.id, 'moved': moved},
              reason=body.reason)
        audit(db, user, 'manufacturer', target.id, 'merged_from', after={'source_id': source.id, 'moved': moved},
              reason=body.reason)
        return {**manufacturer_view(db, target), 'moved': moved}

    return idem(db, user, body.idempotency_key, f'merge-manufacturer:{entity_id}', body.model_dump(mode='json'),
                operation)
