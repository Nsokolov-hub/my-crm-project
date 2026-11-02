"""The site's Metrica and Webmaster in the CRM: signed requests to the site, rights, the token never kept here."""

import hashlib
import hmac
import json

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.models import AuditEvent
from app.integrations import site_analytics
from tests.support.crm import login

SECRET = 'events-secret'
TOKEN = 'y0_AgAAAAB-test-token_0123456789abcdefXYZ'
REPORT = {'status': {'connected': True, 'tail': 'fXYZ'}, 'period': 30, 'fetchedAt': '2026-11-02T10:00:00+00:00',
          'metrika': {'totals': {'visits': 20}}, 'webmaster': {'summary': {'inSearch': 9}}}


class FakeSite:
    def __init__(self) -> None:
        self.bodies: list[dict] = []
        self.answer: httpx.Response | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == 'https://site.test/api/v1/crm/analytics'
        stamp = request.headers['X-CRM-Timestamp']
        expected = 'sha256=' + hmac.new(SECRET.encode(), stamp.encode() + b'.' + request.content,
                                        hashlib.sha256).hexdigest()
        assert request.headers['X-CRM-Signature'] == expected
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.answer is not None:
            return self.answer
        if body['action'] == 'connect':
            return httpx.Response(200, json={'status': {'connected': True, 'tail': body['token'][-4:],
                                                        'login': 'ogkteam'}, 'access': {}, 'goals': []})
        if body['action'] == 'goals':
            return httpx.Response(200, json={'goals': [{'identifier': 'form_submit', 'state': 'created'}]})
        return httpx.Response(200, json={**REPORT, 'period': body.get('period', 30)})


@pytest.fixture
def fake(monkeypatch) -> FakeSite:
    fake = FakeSite()
    monkeypatch.setattr(settings, 'site_events_url', 'https://site.test/api/v1/crm/events')
    monkeypatch.setattr(settings, 'site_events_secret', SECRET)
    monkeypatch.setattr(site_analytics, 'http_client',
                        lambda: httpx.Client(transport=httpx.MockTransport(fake.handle)))
    return fake


def test_the_report_comes_from_the_site_for_those_with_the_reports(crm, fake):
    login(crm)
    client = crm['client']
    answer = client.get('/api/v1/site-analytics', params={'period': 7})
    assert answer.status_code == 200, answer.text
    assert answer.json()['metrika']['totals']['visits'] == 20 and fake.bodies[-1] == {'action': 'report', 'period': 7}
    assert client.get('/api/v1/site-analytics', params={'period': 14}).json()['code'] == 'PERIOD_INVALID'
    assert client.post('/api/v1/site-analytics/refresh').json()['period'] == 30
    assert fake.bodies[-1]['action'] == 'refresh'

    login(crm, email='manager@example.com')
    assert client.get('/api/v1/site-analytics').status_code == 403
    assert client.put('/api/v1/site-analytics/token', json={'token': TOKEN}).status_code == 403
    assert len(fake.bodies) == 2


def test_the_token_goes_to_the_site_and_stays_out_of_the_crm(crm, fake):
    login(crm)
    client = crm['client']
    connected = client.put('/api/v1/site-analytics/token', json={'token': TOKEN})
    assert connected.status_code == 200, connected.text
    assert connected.json()['status']['tail'] == TOKEN[-4:]
    assert fake.bodies[-1] == {'action': 'connect', 'token': TOKEN, 'author': 'Владелец теста'}
    assert client.post('/api/v1/site-analytics/goals').json()['goals'][0]['state'] == 'created'
    assert client.post('/api/v1/site-analytics/sitemap').status_code == 200
    assert client.delete('/api/v1/site-analytics/token').status_code == 200
    assert [b['action'] for b in fake.bodies] == ['connect', 'goals', 'sitemap', 'disconnect']
    with crm['sessions']() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.entity_type == 'site_analytics')).all()
        assert sorted(e.action for e in events) == ['connect', 'disconnect', 'goals', 'sitemap']
        assert TOKEN not in json.dumps([[e.before, e.after] for e in events], ensure_ascii=False)
        assert next(e for e in events if e.action == 'connect').after == {'tail': 'fXYZ', 'login': 'ogkteam'}


def test_the_sites_refusals_are_explained(crm, fake, monkeypatch):
    login(crm)
    client = crm['client']
    fake.answer = httpx.Response(422, json={'code': 'TOKEN_REFUSED', 'message': 'Метрика: нет доступа', 'field': 'token'})
    refused = client.put('/api/v1/site-analytics/token', json={'token': TOKEN})
    assert refused.status_code == 422 and refused.json()['code'] == 'TOKEN_REFUSED'
    assert 'нет доступа' in refused.json()['message']
    fake.answer = httpx.Response(401, json={'code': 'CRM_SIGNATURE_INVALID', 'message': 'Подпись неверна'})
    assert client.get('/api/v1/site-analytics').json()['code'] == 'SITE_LINK_REFUSED'
    fake.answer = httpx.Response(409, json={'code': 'YANDEX_NOT_CONNECTED', 'message': 'Сначала подключите Яндекс'})
    assert client.post('/api/v1/site-analytics/sitemap').status_code == 409

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('down')

    monkeypatch.setattr(site_analytics, 'http_client', lambda: httpx.Client(transport=httpx.MockTransport(down)))
    assert client.get('/api/v1/site-analytics').json()['code'] == 'SITE_UNREACHABLE'
    monkeypatch.setattr(settings, 'site_events_secret', '')
    assert client.get('/api/v1/site-analytics').json()['code'] == 'SITE_LINK_DISABLED'
