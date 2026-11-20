"""Read-only stock catalogue over the site's existing signed CRM channel."""

import json
import time
from datetime import datetime
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings
from app.core.db import DB, as_utc
from app.core.errors import DomainError
from app.core.security import Actor, can, require_permission
from app.core.service import audit
from app.integrations import site_events

from .stock_catalog_exports import ClientCatalogRow, client_workbook, internal_workbook

router = APIRouter(tags=['Каталог наличия'])
MAX_EXPORT_ROWS = 20_000
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


class StockOffer(BaseModel):
    source: str
    source_name: str
    offer_id: str
    external_id: str | None = None
    catalog_number: str | None = None
    pack: str | None = None
    site_packaging_id: str | None = None
    match: str = ''
    seen_at: datetime | None = None
    valid_until: datetime | None = None
    quantity: int | str | None = None
    url: str | None = None


class StockItem(BaseModel):
    id: str
    product_id: str | None = None
    packaging_id: str | None = None
    name: str
    article: str | None = None
    cas: str | None = None
    manufacturer: str | None = None
    purity: str | None = None
    packaging: str = ''
    availability: Literal['in_stock', 'unknown', 'on_order']
    checked_at: datetime | None = None
    product_url: str | None = None
    origin: str | None = None
    offers: list[StockOffer] = Field(default_factory=list)
    fresh: bool | None = None
    reason: str | None = None


class StockSource(BaseModel):
    id: str
    name: str
    status: str
    reason: str = ''
    last_check: dict[str, Any] | None = None


class SiteStockPage(BaseModel):
    as_of: datetime
    items: list[StockItem] = Field(max_length=200)
    next_cursor: str | None = None
    has_more: bool
    sources: list[StockSource] = Field(default_factory=list)


class StockCatalogPage(SiteStockPage):
    can_review: bool


def url() -> str:
    base = settings.site_events_url.rstrip('/')
    return (base[:-len('/events')] if base.endswith('/events') else base) + '/stock'


def http_client() -> httpx.Client:
    return httpx.Client(timeout=30)


def ask(**fields: Any) -> SiteStockPage:
    if not site_events.enabled():
        raise DomainError('SITE_LINK_DISABLED', 'Связь с сайтом не настроена (SITE_EVENTS_URL и SITE_EVENTS_SECRET)',
                          503)
    body = json.dumps(fields, ensure_ascii=False, separators=(',', ':')).encode()
    stamp = str(int(time.time()))
    headers = {'Content-Type': 'application/json', 'X-CRM-Timestamp': stamp,
               'X-CRM-Signature': site_events.sign(stamp, body)}
    try:
        with http_client() as client:
            response = client.post(url(), content=body, headers=headers)
    except httpx.HTTPError:
        raise DomainError('SITE_UNREACHABLE', 'Сайт не отвечает. Повторите загрузку каталога', 503) from None
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code in (401, 403):
        raise DomainError('SITE_LINK_REFUSED', 'Сайт не принял запрос CRM: проверьте ключ связи и разрешённые адреса',
                          503)
    if response.status_code >= 300:
        detail = data if isinstance(data, dict) else {}
        raise DomainError(str(detail.get('code') or 'SITE_UNAVAILABLE'),
                          str(detail.get('message') or 'Каталог сайта временно недоступен'),
                          response.status_code if response.status_code in (409, 422) else 503,
                          detail.get('field'))
    try:
        return SiteStockPage.model_validate(data)
    except ValidationError:
        raise DomainError('SITE_STOCK_INVALID', 'Сайт вернул неполный каталог. Повторите загрузку', 503) from None


def query_fields(mode: str, q: str, source: str, brand: str, as_of: datetime | None) -> dict[str, Any]:
    return {'mode': mode, 'q': q.strip(), 'source': source.strip(), 'brand': brand.strip(),
            **({'as_of': as_utc(as_of).isoformat()} if as_of else {})}


def authorize(db: DB, user: Actor, audience: str, mode: str, source: str) -> None:
    require_permission(db, user, 'catalog.read')
    if audience == 'internal' or mode == 'review' or source:
        require_permission(db, user, 'clients.read')
    if audience == 'client' and mode == 'review':
        raise DomainError('STOCK_REVIEW_INTERNAL', 'Позиции на проверке доступны только во внутреннем файле', 422)


def public_item(row: StockItem) -> StockItem:
    # Build a new object from canonical public product fields. Supplier evidence is never copied.
    return StockItem(id=row.id, product_id=row.product_id, packaging_id=row.packaging_id,
                     name=row.name, article=row.article, cas=row.cas, manufacturer=row.manufacturer,
                     purity=row.purity, packaging=row.packaging, availability=row.availability,
                     checked_at=row.checked_at, product_url=row.product_url)


def all_rows(fields: dict[str, Any]) -> tuple[datetime, list[StockItem]]:
    """Export every matching page at one as-of time. Never silently truncate an export."""
    rows: list[StockItem] = []
    seen_ids: set[str] = set()
    seen_cursors: set[str] = set()
    cursor = None
    at: datetime | None = None
    while True:
        page = ask(**fields, cursor=cursor, limit=200)
        if at is None:
            at = as_utc(page.as_of)
            fields = {**fields, 'as_of': at.isoformat()}
        elif as_utc(page.as_of) != at:
            raise DomainError('STOCK_VIEW_CHANGED', 'Каталог изменился при выгрузке. Повторите экспорт', 409)
        for row in page.items:
            key = f'{row.product_id}:{row.packaging_id}' if fields['mode'] == 'catalog' else row.id
            if key in seen_ids:
                raise DomainError('STOCK_PAGE_REPEATED', 'Сайт повторил позиции каталога. Повторите экспорт', 503)
            seen_ids.add(key)
            rows.append(row)
        if len(rows) > MAX_EXPORT_ROWS or len(rows) == MAX_EXPORT_ROWS and page.has_more:
            raise DomainError('STOCK_EXPORT_TOO_LARGE',
                              'Больше 20 000 позиций. Уточните поиск или производителя и повторите выгрузку', 422)
        if not page.has_more:
            return at, rows
        if not page.items or not page.next_cursor or page.next_cursor in seen_cursors:
            raise DomainError('STOCK_PAGE_INVALID', 'Сайт не вернул следующую страницу каталога', 503)
        cursor = page.next_cursor
        seen_cursors.add(cursor)


@router.get('/stock-catalog', response_model=StockCatalogPage, response_model_exclude_none=True)
def listing(
    user: Actor, db: DB, mode: Literal['catalog', 'review'] = 'catalog',
    audience: Literal['client', 'internal'] = 'client', q: str = Query('', max_length=200),
    source: str = Query('', max_length=30), brand: str = Query('', max_length=200),
    cursor: str | None = Query(None, max_length=4096), limit: int = Query(50, ge=1, le=200),
    as_of: datetime | None = None,
) -> StockCatalogPage:
    authorize(db, user, audience, mode, source)
    page = ask(**query_fields(mode, q, source, brand, as_of), cursor=cursor, limit=limit)
    if audience == 'client' and mode == 'catalog':
        page = page.model_copy(update={'items': [public_item(row) for row in page.items], 'sources': []})
    return StockCatalogPage(**page.model_dump(), can_review=can(db, user, 'clients.read'))


@router.get('/stock-catalog/export.xlsx')
def export(
    user: Actor, db: DB, audience: Literal['client', 'internal'] = 'client',
    mode: Literal['catalog', 'review'] = 'catalog', q: str = Query('', max_length=200),
    source: str = Query('', max_length=30), brand: str = Query('', max_length=200),
    as_of: datetime | None = None,
) -> Response:
    authorize(db, user, audience, mode, source)
    require_permission(db, user, 'exports.download')
    at, rows = all_rows(query_fields(mode, q, source, brand, as_of))
    if audience == 'client':
        # The generator cannot see source names, URLs, private notes, IDs or procurement data.
        public = [ClientCatalogRow(name=row.name, article=row.article or '', cas=row.cas or '',
                                   manufacturer=row.manufacturer or '', purity=row.purity or '',
                                   packaging=row.packaging, checked_at=row.checked_at)
                  for row in rows if row.availability == 'in_stock']
        content = client_workbook(public, at, q=q.strip(), brand=brand.strip())
    else:
        if sum(max(1, len(row.offers)) for row in rows) > MAX_EXPORT_ROWS:
            raise DomainError('STOCK_EXPORT_TOO_LARGE',
                              'Больше 20 000 строк предложений. Уточните фильтры и повторите выгрузку', 422)
        content = internal_workbook([row.model_dump() for row in rows], at, mode=mode,
                                    q=q.strip(), brand=brand.strip(), source=source.strip())
    audit(db, user, 'stock_catalog', 'export', 'exported',
          after={'audience': audience, 'mode': mode, 'count': len(rows), 'as_of': at.isoformat(),
                 'q': q.strip(), 'brand': brand.strip(), 'source': source.strip()})
    name = f'ogk-stock-{audience}-{at.date().isoformat()}.xlsx'
    return Response(content, media_type=XLSX,
                    headers={'Content-Disposition': f'attachment; filename="{name}"', 'Cache-Control': 'no-store'})
