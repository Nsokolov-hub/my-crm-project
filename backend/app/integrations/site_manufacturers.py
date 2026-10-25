"""The manufacturer registry for the site (flow F5, TZ of the site 9.3).

The site loads the registry every night: ``GET /api/v1/integrations/site/manufacturers?updated_since=…``.
Entries come with every known spelling, the umbrella brand and the entry a merged one went into, ordered by the
time they changed; the site uses the spellings to recognise manufacturers in imported price lists. Clients and
prices never leave the CRM. The call is signed like F1, over ``<timestamp>.GET <path>?<query>``.
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import and_, event, or_, select
from sqlalchemy.orm import Session

from app.commerce.models import Manufacturer, ManufacturerAlias
from app.core.db import DB, utcnow
from app.core.errors import DomainError
from app.integrations.site_auth import authenticate

router = APIRouter(tags=['Интеграция с сайтом'])
MAX_LIMIT = 1000


class SiteManufacturer(BaseModel):
    id: str
    name: str
    internal_code: int
    aliases: list[str]
    umbrella_id: str | None
    merged_into_id: str | None
    updated_at: datetime


class SiteManufacturers(BaseModel):
    items: list[SiteManufacturer]
    # Pass it as ``cursor`` for the next page; null — the registry has been read to the end.
    next_cursor: str | None


@event.listens_for(Session, 'before_flush')
def _touch_manufacturers(session: Session, _context: Any, _instances: Any) -> None:
    """A new, changed or removed spelling changes its entry for the site as well."""
    ids = {row.manufacturer_id for row in (*session.new, *session.dirty, *session.deleted)
           if isinstance(row, ManufacturerAlias)}
    for maker in (session.get(Manufacturer, i) for i in ids if i):
        if maker is not None:
            maker.updated_at = utcnow()


def _cursor(value: str | None) -> tuple[datetime, str] | None:
    if not value:
        return None
    stamp, _, last_id = value.partition('|')
    try:
        return datetime.fromisoformat(stamp), last_id
    except ValueError:
        raise DomainError('CURSOR_INVALID', 'Курсор не распознан: начните выгрузку заново', 422, 'cursor') from None


def manufacturers_page(db: Session, updated_since: datetime | None, cursor: str | None,
                       limit: int) -> dict[str, Any]:
    statement = select(Manufacturer)
    if updated_since is not None:
        statement = statement.where(Manufacturer.updated_at >= updated_since)
    after = _cursor(cursor)
    if after is not None:
        stamp, last_id = after
        statement = statement.where(or_(Manufacturer.updated_at > stamp,
                                        and_(Manufacturer.updated_at == stamp, Manufacturer.id > last_id)))
    rows = db.scalars(statement.order_by(Manufacturer.updated_at, Manufacturer.id).limit(limit + 1)).all()
    page, more = rows[:limit], len(rows) > limit
    spellings: dict[str, list[str]] = {row.id: [] for row in page}
    if page:
        for alias in db.scalars(select(ManufacturerAlias).where(ManufacturerAlias.manufacturer_id.in_(spellings))
                                .order_by(ManufacturerAlias.name)):
            spellings[alias.manufacturer_id].append(alias.name)
    items = [{'id': row.id, 'name': row.name, 'internal_code': row.internal_code, 'aliases': spellings[row.id],
              'umbrella_id': row.umbrella_id, 'merged_into_id': row.merged_into_id, 'updated_at': row.updated_at}
             for row in page]
    last = page[-1] if page else None
    return {'items': items, 'next_cursor': f'{last.updated_at.isoformat()}|{last.id}' if more and last else None}


@router.get('/integrations/site/manufacturers', response_model=SiteManufacturers, responses={
    401: {'description': 'Нет токена, подпись неверна или устарела'},
    403: {'description': 'Адрес сервера сайта не разрешён'},
})
async def site_manufacturers(request: Request, db: DB, updated_since: datetime | None = None,
                             cursor: str | None = None, limit: int = Query(500, ge=1, le=MAX_LIMIT)) -> dict[str, Any]:
    """Реестр производителей для сайта (поток F5): изменённые с `updated_since`, постранично по `cursor`.
    Подпись HMAC — от строки `GET <путь>?<параметры>` вместо тела (docs/site-integration.md)."""
    headers = {key.lower(): value for key, value in request.headers.items()}
    host = request.client.host if request.client else None
    target = request.url.path + (f'?{request.url.query}' if request.url.query else '')
    await run_in_threadpool(authenticate, db, headers, host, f'GET {target}'.encode())
    return await run_in_threadpool(manufacturers_page, db, updated_since, cursor, limit)
