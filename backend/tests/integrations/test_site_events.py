"""Flow F2: changes in the CRM become signed events for the site, delivered by the worker with retries."""

import hashlib
import hmac
import json
import time
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.commerce.models import Manufacturer
from app.communication import worker
from app.core.config import settings
from app.core.models import OutboxEvent
from app.crm.models import Nomenclature, ProductGroup, Request
from app.integrations import site_events
from app.integrations.models import SiteSubscriber
from app.integrations.site_auth import sign
from tests.integrations.test_site_conversion import command, conversion_body, received
from tests.support.crm import login


@pytest.fixture
def events(site, monkeypatch):
    monkeypatch.setattr(settings, 'site_events_url', 'https://site.test/api/v1/crm/events')
    monkeypatch.setattr(settings, 'site_events_secret', 'events-secret')
    return site


def queued(env, kind=None):
    with env['sessions']() as db:
        rows = db.scalars(select(OutboxEvent).where(OutboxEvent.kind == 'site.event').order_by(OutboxEvent.created_at))
        return [r.payload for r in rows if kind is None or r.payload['type'] == kind]


def converted(env):
    site_request = received(env)
    login(env)
    command(env, f'{site_request}/assign', {'assignee_id': env['admin'].id, 'version': 1})
    window = env['client'].get(f'/api/v1/site-requests/{site_request}/conversion').json()
    return site_request, command(env, f'{site_request}/convert', conversion_body(window, version=2))['created']


def test_no_events_without_the_sites_address(site):
    converted(site)
    assert queued(site) == []


def test_fate_of_a_site_request_and_stages_of_its_crm_request(events):
    site_request, created = converted(events)
    kinds = [e['type'] for e in queued(events)]
    assert kinds[:2] == ['site_request.assigned', 'site_request.converted']
    assigned = queued(events, 'site_request.assigned')[0]['data']
    assert assigned['crm_site_request_id'] == site_request and assigned['status'] == 'in_work'
    assert 'company' not in json.dumps(queued(events)) and 'zakupki' not in json.dumps(queued(events))
    with events['sessions'].begin() as db:
        db.get(Request, created['request_id']).commercial_stage = 'proposal_sent'
    with events['sessions'].begin() as db:
        from app.core.db import utcnow

        db.get(Request, created['request_id']).sale_confirmed_at = utcnow()
    stages = [e['data']['stage'] for e in queued(events, 'request.stage_changed')]
    assert stages == ['proposal_sent', 'sale_confirmed']
    sale = queued(events, 'request.stage_changed')[1]['data']
    assert sale['crm_request_number'] == created['request_number'] and 'revenue' in sale
    with events['sessions'].begin() as db:
        db.get(Request, created['request_id']).commercial_stage = 'proposal_sent'
    # The same change twice is one event (its key).
    assert len(queued(events, 'request.stage_changed')) == 2


def test_rejected_site_request(events):
    site_request = received(events)
    login(events)
    command(events, f'{site_request}/reject', {'reason': 'Спам', 'version': 1})
    assert queued(events, 'site_request.rejected')[0]['data']['status'] == 'rejected'


def test_published_nomenclature_goes_to_the_site_as_a_product(events):
    login(events)
    with events['sessions'].begin() as db:
        group = ProductGroup(name='Стандарты', slug=f'std-{uuid4().hex[:6]}')
        db.add(group)
        db.flush()
        maker = Manufacturer(name='EDQM', normalized_name='edqm', internal_code=950)
        db.add(maker)
        db.flush()
        row = Nomenclature(name='Oxcarbazepine CRS', article='Y0001897', manufacturer_id=maker.id, cas='28721-07-5',
                           product_group_id=group.id)
        db.add(row)
        db.flush()
        nomenclature_id = row.id
    assert queued(events, 'product.upserted') == []
    patched = events['client'].patch(f'/api/v1/nomenclatures/{nomenclature_id}',
                                     json={'version': 1, 'publish_to_site': True})
    assert patched.status_code == 200, patched.text
    product = queued(events, 'product.upserted')[0]['data']
    assert (product['crm_product_id'], product['brand'], product['catalog_number'], product['publish']) == (
        nomenclature_id, 'EDQM', 'Y0001897', True)
    assert 'price' not in json.dumps(product)
    # The site's id coming back is stored and sends nothing new.
    with events['sessions'].begin() as db:
        site_events.remember_site_product(db, nomenclature_id, 'site-product-1')
    assert len(queued(events, 'product.upserted')) == 1
    with events['sessions']() as db:
        assert db.get(Nomenclature, nomenclature_id).site_product_id == 'site-product-1'


def test_merged_manufacturer(events):
    with events['sessions'].begin() as db:
        a = Manufacturer(name='Honeywell', normalized_name='honeywell', internal_code=960)
        b = Manufacturer(name='Riedel', normalized_name='riedel', internal_code=961)
        db.add_all([a, b])
        db.flush()
        ids = a.id, b.id
    with events['sessions'].begin() as db:
        db.get(Manufacturer, ids[1]).merged_into_id = ids[0]
    assert queued(events, 'manufacturer.merged')[0]['data'] == {'id': ids[1], 'merged_into_id': ids[0]}


def test_worker_signs_delivers_and_retries(events, monkeypatch):
    converted(events)
    calls: list[httpx.Request] = []
    answers = iter([httpx.Response(503), httpx.Response(200, json={'ok': True})])

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return next(answers, httpx.Response(200, json={'ok': True}))

    monkeypatch.setattr(site_events, 'http_client', lambda: httpx.Client(transport=httpx.MockTransport(handle)))
    with events['sessions'].begin() as db:
        first = worker.process_events(db)
    assert first['retried'] == 1 and first['succeeded'] >= 1
    request = calls[0]
    stamp = request.headers['X-CRM-Timestamp']
    expected = 'sha256=' + hmac.new(b'events-secret', stamp.encode() + b'.' + request.content, hashlib.sha256).hexdigest()
    assert request.headers['X-CRM-Signature'] == expected and request.url.path == '/api/v1/crm/events'
    assert json.loads(request.content)['id'] == request.headers['X-CRM-Event-Id']
    # A refused event (422: the site found it wrong) is not retried.
    monkeypatch.setattr(site_events, 'http_client',
                        lambda: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(422))))
    with events['sessions'].begin() as db:
        site_events.emit(db, 'manufacturer.upserted', 'refused', {'id': 'x'})
    with events['sessions'].begin() as db:
        db.execute(OutboxEvent.__table__.update().values(next_attempt_at=None))
    with events['sessions'].begin() as db:
        assert worker.process_events(db)['failed'] >= 1


def subscriber_call(env, body, *, secret=None):
    raw = json.dumps(body).encode()
    stamp = str(int(time.time()))
    return env['client'].post('/api/v1/integrations/site/subscribers', content=raw, headers={
        'Content-Type': 'application/json', 'Authorization': f'Bearer {env["token"]}', 'X-Site-Timestamp': stamp,
        'X-Site-Signature': sign(secret or env['secret'], stamp, raw), 'Idempotency-Key': f'{body["site_subscriber_id"]}:1'})


def test_subscribers_from_the_site(site):
    body = {'site_subscriber_id': 's-1', 'version': 2, 'email': 'A@Example.ru', 'lang': 'ru', 'subscribed': True,
            'consent_at': '2026-10-26T08:00:00Z', 'source': 'home'}
    assert subscriber_call(site, body, secret='wrong').status_code == 401
    answer = subscriber_call(site, body)
    assert answer.status_code == 200, answer.text and answer.json()['applied'] is True
    stale = subscriber_call(site, {**body, 'version': 1, 'subscribed': False})
    assert stale.json()['applied'] is False
    with site['sessions']() as db:
        row = db.scalar(select(SiteSubscriber))
        assert (row.email, row.subscribed) == ('a@example.ru', True)
    login(site)
    listing = site['client'].get('/api/v1/site-subscribers').json()
    assert listing['counts'] == {'subscribed': 1, 'unsubscribed': 0} and listing['items'][0]['email'] == 'a@example.ru'
    exported = site['client'].get('/api/v1/site-subscribers/export')
    assert exported.status_code == 200 and 'a@example.ru;ru' in exported.text
