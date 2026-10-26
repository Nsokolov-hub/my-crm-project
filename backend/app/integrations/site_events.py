"""Events of the CRM to the site (flow F2, TZ of the site 9.3).

The site never reads the CRM: what it must know comes as signed events through the outbox and its worker, with
retries. A change is noticed where it is written to the database (``before_flush``), so every path that moves a
request between stages or edits a manufacturer emits its event without being taught to:

- ``site_request.assigned`` / ``converted`` / ``rejected`` — the fate of a site request;
- ``request.stage_changed`` / ``request.closed`` — stages of a CRM request made from a site request; the site
  turns them into offline conversions of Yandex Metrika and GA4 (F3); ``sale_confirmed`` carries the revenue;
- ``product.upserted`` — a nomenclature marked «Публиковать на сайте» (F4): the site makes a draft card;
- ``manufacturer.upserted`` / ``manufacturer.merged`` — the registry changed (F5 without waiting for the night).

Clients, contacts and prices never leave the CRM: events carry ids, stages, the catalogue fields and the revenue
of a confirmed sale only. Each event is POSTed to SITE_EVENTS_URL with ``X-CRM-Event-Id``, ``X-CRM-Timestamp`` and
``X-CRM-Signature: sha256=HMAC(SITE_EVENTS_SECRET, "<timestamp>.<body>")``.
"""

import hashlib
import hmac
import json
import time
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import utcnow
from app.core.errors import DomainError
from app.core.models import OutboxEvent

KIND = 'site.event'
# Fields of a nomenclature the site's card is made of; the site's own id coming back changes nothing to send.
PRODUCT_FIELDS = ('name', 'article', 'manufacturer_id', 'manufacturer', 'purity', 'cas', 'active', 'publish_to_site')
SITE_REQUEST_EVENTS = {'in_work': 'site_request.assigned', 'converted': 'site_request.converted',
                       'rejected': 'site_request.rejected'}


def enabled() -> bool:
    return bool(settings.site_events_url and settings.site_events_secret)


def emit(session: Session, kind: str, key: str, data: dict[str, Any]) -> None:
    """Queue one event; the key makes a repeat of the same change one event."""
    if not enabled():
        return
    event_id = str(uuid4())
    session.add(OutboxEvent(event_key=f'site-event:{kind}:{key}'[:200], kind=KIND, payload={
        'id': event_id, 'type': kind, 'occurred_at': utcnow().isoformat(), 'data': data}))


def changed(row: Any, field: str) -> bool:
    return bool(inspect(row).attrs[field].history.has_changes())


def revenue(session: Session, request_id: str) -> dict[str, Any] | None:
    """The sum of the request's current invoice (or, without one, of its latest proposal)."""
    from app.commerce.models import CommercialDocument

    for kind in ('invoice', 'proposal'):
        doc = session.scalar(select(CommercialDocument).where(
            CommercialDocument.request_id == request_id, CommercialDocument.kind == kind,
            CommercialDocument.status.not_in(('cancelled', 'superseded', 'replaced')))
            .order_by(CommercialDocument.created_at.desc()).limit(1))
        if doc is not None:
            return {'amount': str(Decimal(doc.total).quantize(Decimal('0.01'))), 'currency': doc.currency}
    return None


@event.listens_for(Session, 'before_flush')
def _collect(session: Session, _context: Any, _instances: Any) -> None:
    if not enabled():
        return
    from app.commerce.models import Manufacturer
    from app.crm.models import Nomenclature, Packing
    from app.crm.models import Request as CRMRequest
    from app.integrations.models import SiteRequest

    touched_nomenclatures: set[str] = set()
    for row in [*session.new, *session.dirty]:
        if isinstance(row, SiteRequest) and (row in session.new or changed(row, 'status')):
            kind = SITE_REQUEST_EVENTS.get(row.status)
            if kind:
                emit(session, kind, f'{row.id}:{row.status}', {
                    'site_request_id': row.site_request_id, 'crm_site_request_id': row.id, 'status': row.status,
                    'crm_request_id': row.request_id})
        elif isinstance(row, CRMRequest) and row not in session.new:
            if changed(row, 'commercial_stage') or changed(row, 'sale_confirmed_at') or changed(row, 'closed_at'):
                _request_events(session, row)
        elif isinstance(row, Nomenclature):
            if row in session.new or any(changed(row, field) for field in PRODUCT_FIELDS):
                touched_nomenclatures.add(row.id)
        elif isinstance(row, Packing):
            touched_nomenclatures.add(row.nomenclature_id)
        elif isinstance(row, Manufacturer) and row not in session.new:
            if changed(row, 'merged_into_id') and row.merged_into_id:
                emit(session, 'manufacturer.merged', f'{row.id}:{row.merged_into_id}',
                     {'id': row.id, 'merged_into_id': row.merged_into_id})
            elif session.is_modified(row, include_collections=False):
                emit(session, 'manufacturer.upserted', f'{row.id}:{utcnow().timestamp()}', {'id': row.id})
    for nomenclature_id in touched_nomenclatures:
        row = session.get(Nomenclature, nomenclature_id) if nomenclature_id else None
        if row is not None and (row.publish_to_site or (row not in session.new and changed(row, 'publish_to_site'))):
            emit(session, 'product.upserted', f'{row.id}:{utcnow().timestamp()}', product_payload(session, row))


def _request_events(session: Session, row: Any) -> None:
    from app.integrations.models import SiteRequest

    origin = session.scalar(select(SiteRequest).where(SiteRequest.request_id == row.id).limit(1))
    if origin is None:
        return
    base = {'site_request_id': origin.site_request_id, 'crm_request_id': row.id, 'crm_request_number': row.number}
    if row.closed_at is not None and changed(row, 'closed_at'):
        emit(session, 'request.closed', f'{row.id}:closed:{row.closed_at.isoformat()}',
             {**base, 'stage': row.commercial_stage, 'lost': bool(row.loss_reason)})
        return
    stage = 'sale_confirmed' if row.sale_confirmed_at is not None and changed(row, 'sale_confirmed_at') \
        else row.commercial_stage
    data: dict[str, Any] = {**base, 'stage': stage}
    if stage == 'sale_confirmed':
        data['revenue'] = revenue(session, row.id)
    emit(session, 'request.stage_changed', f'{row.id}:{stage}', data)


def product_payload(session: Session, row: Any) -> dict[str, Any]:
    """The catalogue fields of a position, without prices, suppliers or clients."""
    from app.commerce.models import Manufacturer
    from app.crm.models import Packing

    maker = session.get(Manufacturer, row.manufacturer_id) if row.manufacturer_id else None
    packings = [p.display_name for p in session.scalars(
        select(Packing).where(Packing.nomenclature_id == row.id, Packing.active.is_(True))
        .order_by(Packing.value))]
    return {'crm_product_id': row.id, 'site_product_id': row.site_product_id, 'publish': bool(row.publish_to_site),
            'active': bool(row.active), 'brand': maker.name if maker else row.manufacturer,
            'crm_manufacturer_id': row.manufacturer_id, 'catalog_number': row.article, 'name': row.name,
            'cas': row.cas, 'purity': row.purity, 'packagings': packings}


def sign(timestamp: str, body: bytes) -> str:
    return 'sha256=' + hmac.new(settings.site_events_secret.encode(), timestamp.encode() + b'.' + body,
                                hashlib.sha256).hexdigest()


def http_client() -> httpx.Client:
    """Replaced in tests with a client on a mock transport."""
    return httpx.Client(timeout=15)


def deliver(session: Session, outbox: OutboxEvent) -> None:
    """POST the event to the site; 2xx — done, 4xx other than 408/409/429 — the event is wrong (no retry)."""
    if not enabled():
        raise DomainError('SITE_EVENTS_DISABLED', 'Отправка событий на сайт выключена', 503)
    body = json.dumps(outbox.payload, ensure_ascii=False, separators=(',', ':')).encode()
    stamp = str(int(time.time()))
    headers = {'Content-Type': 'application/json', 'X-CRM-Event-Id': str(outbox.payload.get('id')),
               'X-CRM-Timestamp': stamp, 'X-CRM-Signature': sign(stamp, body)}
    try:
        with http_client() as client:
            response = client.post(settings.site_events_url, content=body, headers=headers)
    except httpx.HTTPError:
        raise DomainError('SITE_UNREACHABLE', 'Сайт не отвечает', 503) from None
    if response.status_code < 300:
        answer = response.json() if response.content else {}
        site_product_id = answer.get('site_product_id') if isinstance(answer, dict) else None
        if outbox.payload.get('type') == 'product.upserted' and site_product_id:
            remember_site_product(session, str(outbox.payload['data'].get('crm_product_id')), str(site_product_id))
        return
    if response.status_code in (400, 401, 403, 404, 410, 422):
        raise DomainError('SITE_REFUSED', f'Сайт отклонил событие: {response.status_code}', 422)
    raise DomainError('SITE_UNAVAILABLE', f'Сайт ответил {response.status_code}', 503)


def remember_site_product(session: Session, nomenclature_id: str, site_product_id: str) -> None:
    """The site's card of the position, so the next event and the request items point to it."""
    from app.crm.models import Nomenclature

    row = session.get(Nomenclature, nomenclature_id)
    if row is not None and row.site_product_id != site_product_id:
        row.site_product_id = site_product_id
