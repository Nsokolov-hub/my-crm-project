"""The site's Yandex Metrica and Webmaster in «Аналитика» → «Сайт: Метрика и Вебмастер».

The site keeps the Yandex token and fetches the reports once an hour (ogk-site app/analytics/yandex.py); the CRM asks
for them on the same channel as its events: a POST to the site's /api/v1/crm/analytics signed with
SITE_EVENTS_SECRET (``X-CRM-Timestamp``, ``X-CRM-Signature``). The action is in the body: ``report`` and
``refresh`` — the snapshot of 7, 30 or 90 days; ``connect`` (the token pasted here goes to the site and is never
shown again), ``disconnect``, ``goals`` (goals of Metrica for the site's events and the stages of requests),
``sitemap`` (the site map in Webmaster).

Reports — for everyone with «Все отчёты аналитики»; the token, goals and the site map — «Настройки системы».
"""

import json
import time
from typing import Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.db import DB
from app.core.errors import DomainError
from app.core.security import Actor, require_permission
from app.core.service import audit
from app.integrations import site_events

router = APIRouter(tags=['Аналитика сайта'])
PERIODS = (7, 30, 90)


def url() -> str:
    """The site's address of the reports: next to the address of the events (…/api/v1/crm/events)."""
    base = settings.site_events_url.rstrip('/')
    return (base[: -len('/events')] if base.endswith('/events') else base) + '/analytics'


def http_client() -> httpx.Client:
    """Replaced in tests with a client on a mock transport. Connecting creates goals in Metrica: up to a minute."""
    return httpx.Client(timeout=60)


def ask(action: str, **fields: Any) -> dict[str, Any]:
    if not site_events.enabled():
        raise DomainError('SITE_LINK_DISABLED', 'Связь с сайтом не настроена (SITE_EVENTS_URL и SITE_EVENTS_SECRET)',
                          503)
    body = json.dumps({'action': action, **fields}, ensure_ascii=False, separators=(',', ':')).encode()
    stamp = str(int(time.time()))
    headers = {'Content-Type': 'application/json', 'X-CRM-Timestamp': stamp,
               'X-CRM-Signature': site_events.sign(stamp, body)}
    try:
        with http_client() as client:
            response = client.post(url(), content=body, headers=headers)
    except httpx.HTTPError:
        raise DomainError('SITE_UNREACHABLE', 'Сайт не отвечает', 503) from None
    try:
        data = response.json() if response.content else {}
    except ValueError:
        data = {}
    if response.status_code < 300 and isinstance(data, dict):
        return data
    detail = data if isinstance(data, dict) else {}
    if response.status_code in (401, 403):
        # The key or the list of addresses differs between the CRM and the site.
        raise DomainError('SITE_LINK_REFUSED', 'Сайт не принял запрос CRM: сверьте SITE_EVENTS_SECRET с '
                                               'CRM_EVENTS_SECRET сайта', 503)
    status = response.status_code if response.status_code in (409, 422) else 503
    raise DomainError(str(detail.get('code') or f'SITE_HTTP_{response.status_code}'),
                      str(detail.get('message') or f'Сайт ответил {response.status_code}'), status,
                      detail.get('field'))


def check_period(period: int) -> None:
    if period not in PERIODS:
        raise DomainError('PERIOD_INVALID', 'Период — 7, 30 или 90 дней', 422, 'period')


@router.get('/site-analytics')
def report(user: Actor, db: DB, period: int = 30) -> dict[str, Any]:
    require_permission(db, user, 'analytics.read')
    require_permission(db, user, 'analytics.reports')
    check_period(period)
    return ask('report', period=period)


@router.post('/site-analytics/refresh')
def refresh(user: Actor, db: DB, period: int = 30) -> dict[str, Any]:
    require_permission(db, user, 'analytics.reports')
    check_period(period)
    return ask('refresh', period=period)


class TokenInput(BaseModel):
    token: str = Field(min_length=1, max_length=400)


@router.put('/site-analytics/token')
def connect(body: TokenInput, user: Actor, db: DB) -> dict[str, Any]:
    require_permission(db, user, 'admin.settings')
    answer = ask('connect', token=body.token, author=user.name[:100])
    audit(db, user, 'site_analytics', 'yandex', 'connect',
          after={'tail': (answer.get('status') or {}).get('tail'), 'login': (answer.get('status') or {}).get('login')})
    return answer


@router.delete('/site-analytics/token')
def disconnect(user: Actor, db: DB) -> dict[str, Any]:
    require_permission(db, user, 'admin.settings')
    answer = ask('disconnect', author=user.name[:100])
    audit(db, user, 'site_analytics', 'yandex', 'disconnect')
    return answer


@router.post('/site-analytics/goals')
def goals(user: Actor, db: DB) -> dict[str, Any]:
    require_permission(db, user, 'admin.settings')
    answer = ask('goals', author=user.name[:100])
    audit(db, user, 'site_analytics', 'yandex', 'goals',
          after={'created': [g.get('identifier') for g in answer.get('goals') or [] if g.get('state') == 'created']})
    return answer


@router.post('/site-analytics/sitemap')
def sitemap(user: Actor, db: DB) -> dict[str, Any]:
    require_permission(db, user, 'admin.settings')
    answer = ask('sitemap', author=user.name[:100])
    audit(db, user, 'site_analytics', 'yandex', 'sitemap', after=answer)
    return answer
