"""Flow F1: the site delivers a request, the CRM keeps it as sent; employees assign and reject it."""

import hashlib
import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.communication.models import FileRecord
from app.core.config import settings
from app.core.models import Notification, OutboxEvent
from app.crm.models import Task
from app.integrations.models import SiteRequest, SiteRequestAttribution, SiteRequestItem
from app.integrations.site_requests import add_working_hours
from tests.support.crm import login
from tests.support.site import PDF, deliver, payload


def test_request_is_stored_as_sent_with_attribution_and_attachment(site):
    body = payload(attachments=[{'file_name': 'spec.pdf', 'sha256': hashlib.sha256(PDF).hexdigest(),
                                 'size': len(PDF)}])
    response = deliver(site, body, [('spec.pdf', PDF, 'application/pdf')])
    assert response.status_code == 201, response.text
    receipt = response.json()
    assert receipt['number'] == 'W-1' and receipt['status'] == 'new'
    with site['sessions']() as db:
        row = db.get(SiteRequest, receipt['id'])
        assert (row.company, row.inn, row.phone, row.site_number) == (
            'ООО «Пример-Фарма»', '7701234567', '+79991234567', 'S-2026-000123')
        assert str(row.desired_date) == '2026-11-20'
        item = db.scalar(select(SiteRequestItem).where(SiteRequestItem.site_request_id == row.id))
        assert (item.catalog_number, item.cas, item.analogue, item.packaging) == (
            'Y0001897', '28721-07-5', 'allowed', '100 mg')
        attribution = db.scalar(select(SiteRequestAttribution).where(SiteRequestAttribution.site_request_id == row.id))
        assert (attribution.channel, attribution.first_utm_medium, attribution.last_utm_medium, attribution.yclid) == (
            'cpc', 'organic', 'cpc', '123456')
        file = db.scalar(select(FileRecord).where(FileRecord.site_request_id == row.id))
        assert file.status == 'quarantined' and file.name == 'spec.pdf'
        assert db.scalar(select(OutboxEvent).where(OutboxEvent.event_key == f'file-scan:{file.id}'))
        # The owner holds site_requests.assign and is told about the new request.
        assert db.scalar(select(Notification).where(Notification.event_key == f'site-request-new:{row.id}'))


def test_repeat_with_same_key_returns_the_same_receipt(site):
    body = payload()
    first = deliver(site, body)
    again = deliver(site, body)
    assert first.status_code == 201 and again.status_code == 200
    assert again.json() == first.json()
    with site['sessions']() as db:
        assert len(db.scalars(select(SiteRequest)).all()) == 1


def test_same_key_with_other_content_is_a_conflict(site):
    body = payload()
    assert deliver(site, body).status_code == 201
    changed = {**body, 'customer': {**body['customer'], 'company': 'ООО «Другая»'}}
    response = deliver(site, changed)
    assert response.status_code == 409 and response.json()['code'] == 'IDEMPOTENCY_CONFLICT'


@pytest.mark.parametrize('case, code', [
    ('signature', 'INTEGRATION_SIGNATURE_INVALID'),
    ('token', 'INTEGRATION_UNAUTHENTICATED'),
    ('stamp', 'INTEGRATION_SIGNATURE_EXPIRED'),
])
def test_unsigned_or_stale_calls_are_refused(site, case, code):
    body = payload()
    extra = {'signature': 'sha256=' + '0' * 64} if case == 'signature' else (
        {'token': 'ogk_site_unknown'} if case == 'token' else {'stamp': str(int(time.time()) - 3600)})
    response = deliver(site, body, **extra)
    assert response.status_code == 401 and response.json()['code'] == code
    with site['sessions']() as db:
        assert not db.scalars(select(SiteRequest)).all()


def test_only_allowed_addresses_may_call(site, monkeypatch):
    monkeypatch.setattr(settings, 'site_integration_allowed_ips', '10.0.0.0/8')
    response = deliver(site, payload())
    assert response.status_code == 403 and response.json()['code'] == 'INTEGRATION_ADDRESS_FORBIDDEN'


def test_invalid_payload_names_the_field(site):
    body = payload()
    body['customer']['phone'] = '8 999 123-45-67'
    response = deliver(site, body)
    assert response.status_code == 422
    assert response.json()['field'] == 'payload.customer.phone'


def test_key_must_be_the_site_request_id(site):
    response = deliver(site, payload(), key=str(uuid4()))
    assert response.status_code == 422 and response.json()['code'] == 'IDEMPOTENCY_KEY_MISMATCH'


def test_attachment_must_match_its_declared_checksum(site):
    body = payload(attachments=[{'file_name': 'spec.pdf', 'sha256': '0' * 64, 'size': len(PDF)}])
    response = deliver(site, body, [('spec.pdf', PDF, 'application/pdf')])
    assert response.status_code == 422 and response.json()['code'] == 'ATTACHMENTS_MISMATCH'


def test_one_click_has_exactly_one_line(site):
    body = payload(kind='one_click')
    body['items'] = body['items'] * 2
    assert deliver(site, body).status_code == 422


def test_assign_reassign_and_reject(site):
    receipt = deliver(site, payload()).json()
    env = site
    login(env)
    client = env['client']
    listing = client.get('/api/v1/site-requests').json()
    assert listing['counts']['new'] == 1 and listing['items'][0]['number'] == 'W-1'
    manager = env['manager']
    assigned = client.post(f'/api/v1/site-requests/{receipt["id"]}/assign',
                           json={'assignee_id': manager.id, 'version': 1}, headers={'Idempotency-Key': str(uuid4())})
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()['status'] == 'in_work' and assigned.json()['assignee_name'] == manager.name
    with env['sessions']() as db:
        task = db.scalar(select(Task).where(Task.entity_type == 'site_request', Task.entity_id == receipt['id']))
        assert task.assignee_id == manager.id and task.priority == 'high'

    admin = env['admin']
    no_reason = client.post(f'/api/v1/site-requests/{receipt["id"]}/assign',
                            json={'assignee_id': admin.id, 'version': 2}, headers={'Idempotency-Key': str(uuid4())})
    assert no_reason.status_code == 422 and no_reason.json()['code'] == 'REASON_REQUIRED'
    moved = client.post(f'/api/v1/site-requests/{receipt["id"]}/assign',
                        json={'assignee_id': admin.id, 'version': 2, 'reason': 'Отпуск'},
                        headers={'Idempotency-Key': str(uuid4())})
    assert moved.status_code == 200, moved.text
    rejected = client.post(f'/api/v1/site-requests/{receipt["id"]}/reject',
                           json={'reason': 'Дубль заявки W-0', 'version': 3}, headers={'Idempotency-Key': str(uuid4())})
    assert rejected.status_code == 200, rejected.text
    detail = rejected.json()
    assert detail['status'] == 'rejected' and detail['reject_reason'] == 'Дубль заявки W-0'
    assert [event['action'] for event in detail['history']] == ['received', 'assigned', 'reassigned', 'rejected']
    with env['sessions']() as db:
        statuses = sorted(task.status for task in db.scalars(select(Task).where(Task.entity_id == receipt['id'])))
        assert statuses == ['cancelled', 'cancelled']


def test_employee_without_the_right_does_not_see_the_section(site):
    deliver(site, payload())
    login(site, 'manager@example.com')
    assert site['client'].get('/api/v1/site-requests').status_code == 403


@pytest.mark.parametrize('start, due', [
    # Friday 17:30 Moscow → Monday 10:30 Moscow.
    (datetime(2026, 10, 23, 14, 30, tzinfo=timezone.utc), datetime(2026, 10, 26, 7, 30, tzinfo=timezone.utc)),
    # Saturday → Monday 11:00 Moscow.
    (datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc), datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)),
    # Tuesday 10:00 Moscow → 12:00 the same day.
    (datetime(2026, 10, 27, 7, 0, tzinfo=timezone.utc), datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)),
])
def test_contact_deadline_counts_working_hours(crm, start, due):
    with crm['sessions']() as db:
        assert add_working_hours(db, start, 2) == due
