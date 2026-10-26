"""Subscribers of the site's mailing as contacts for mailings (site TZ 11.2).

The site sends every subscription and unsubscription: ``POST /api/v1/integrations/site/subscribers``, signed like
flow F1 over ``<timestamp>.<raw body>``. The site's version of the subscriber decides: an older state arriving late
never overwrites a newer one. Staff see the list in «Заявки с сайта» → «Подписчики» and export it for a mailing.
"""

import csv
import io
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import DB
from app.core.errors import DomainError
from app.core.security import Actor, require_permission
from app.core.service import audit
from app.integrations.models import SiteSubscriber
from app.integrations.site_auth import authenticate

router = APIRouter(tags=['Интеграция с сайтом'])


class SubscriberIn(BaseModel):
    site_subscriber_id: str = Field(min_length=1, max_length=36)
    version: int = Field(default=1, ge=1)
    email: str = Field(min_length=3, max_length=254)
    lang: str = Field(default='ru', pattern='^(ru|en)$')
    subscribed: bool
    consent_at: datetime | None = None
    unsubscribed_at: datetime | None = None
    source: str | None = Field(default=None, max_length=40)
    page_url: str | None = Field(default=None, max_length=2000)


def store(db: Session, data: SubscriberIn) -> tuple[SiteSubscriber, bool]:
    row = db.scalar(select(SiteSubscriber).where(SiteSubscriber.site_subscriber_id == data.site_subscriber_id)
                    .with_for_update())
    if row is not None and row.site_version > data.version:
        return row, False
    if row is None:
        row = SiteSubscriber(site_subscriber_id=data.site_subscriber_id)
        db.add(row)
    row.site_version, row.email, row.lang = data.version, data.email.strip().lower(), data.lang
    row.subscribed, row.consent_at, row.unsubscribed_at = data.subscribed, data.consent_at, data.unsubscribed_at
    row.source, row.page_url = data.source, data.page_url
    db.flush()
    return row, True


@router.post('/integrations/site/subscribers', responses={
    401: {'description': 'Нет токена, подпись неверна или устарела'},
    403: {'description': 'Адрес сервера сайта не разрешён'},
    422: {'description': 'Подписчик не прошёл проверку'},
})
async def receive(request: Request, db: DB) -> dict[str, Any]:
    """Подписчик рассылки сайта: подписка или отписка. Подпись — как у потока F1."""
    body = await request.body()
    headers = {key.lower(): value for key, value in request.headers.items()}
    host = request.client.host if request.client else None
    await run_in_threadpool(authenticate, db, headers, host, body)
    try:
        data = SubscriberIn.model_validate_json(body)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise DomainError('SUBSCRIBER_INVALID', f'{".".join(map(str, first["loc"]))}: {first["msg"]}', 422) from None
    row, applied = await run_in_threadpool(store, db, data)
    return {'id': row.id, 'applied': applied}


def subscriber_view(row: SiteSubscriber) -> dict[str, Any]:
    return {'id': row.id, 'email': row.email, 'lang': row.lang, 'subscribed': row.subscribed,
            'consent_at': row.consent_at.isoformat() if row.consent_at else None,
            'unsubscribed_at': row.unsubscribed_at.isoformat() if row.unsubscribed_at else None,
            'source': row.source, 'updated_at': row.updated_at.isoformat()}


@router.get('/site-subscribers')
def subscribers(user: Actor, db: DB, q: str = '', subscribed: bool | None = None, page: int = 1) -> dict[str, Any]:
    """Подписчики рассылки сайта: контакты для рассылок с согласием, полученным на сайте."""
    require_permission(db, user, 'site_requests.read')
    statement = select(SiteSubscriber)
    if q.strip():
        statement = statement.where(SiteSubscriber.email.ilike(f'%{q.strip().lower()}%'))
    if subscribed is not None:
        statement = statement.where(SiteSubscriber.subscribed.is_(subscribed))
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = db.scalars(statement.order_by(SiteSubscriber.updated_at.desc()).offset((max(page, 1) - 1) * 50).limit(50))
    counts: dict[bool, int] = dict(db.execute(select(SiteSubscriber.subscribed, func.count())
                                              .group_by(SiteSubscriber.subscribed)).tuples().all())
    return {'items': [subscriber_view(r) for r in rows], 'total': total, 'page': max(page, 1), 'page_size': 50,
            'counts': {'subscribed': counts.get(True, 0), 'unsubscribed': counts.get(False, 0)}}


@router.get('/site-subscribers/export')
def export(user: Actor, db: DB) -> Response:
    """CSV подписанных адресов для сервиса рассылок: почта, язык, дата согласия, источник."""
    require_permission(db, user, 'site_requests.read')
    require_permission(db, user, 'exports.download')
    out = io.StringIO()
    writer = csv.writer(out, delimiter=';')
    writer.writerow(['email', 'lang', 'consent_at', 'source'])
    count = 0
    for row in db.scalars(select(SiteSubscriber).where(SiteSubscriber.subscribed.is_(True))
                          .order_by(SiteSubscriber.consent_at)):
        writer.writerow([row.email, row.lang, row.consent_at.isoformat() if row.consent_at else '', row.source or ''])
        count += 1
    audit(db, user, 'site_subscriber', 'export', 'exported', after={'count': count})
    return Response('﻿' + out.getvalue(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="site-subscribers.csv"'})
