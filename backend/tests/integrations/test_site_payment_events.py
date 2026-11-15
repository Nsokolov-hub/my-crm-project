"""Money events follow confirmed receipts/refunds, rather than sales stages or allocation changes."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete

from app.commerce.models import Payment, PaymentReversal
from app.core.config import settings
from app.core.db import as_utc, utcnow
from app.core.models import OutboxEvent
from app.crm.models import Request, Seller
from app.integrations.site_payment_backfill import backfill
from tests.integrations.test_site_events import converted, queued


@pytest.fixture
def events(site, monkeypatch):
    monkeypatch.setattr(settings, 'site_events_url', 'https://site.test/api/v1/crm/events')
    monkeypatch.setattr(settings, 'site_events_secret', 'events-secret')
    return site


def payment(events):
    _, created = converted(events)
    with events['sessions'].begin() as db:
        request = db.get(Request, created['request_id'])
        seller = Seller(name='Test seller', currency='USD', details={})
        db.add(seller)
        db.flush()
        row = Payment(request_id=request.id, client_id=request.client_id, seller_id=seller.id,
                      amount=Decimal('123.45000001'), currency='USD', payment_date=date.today(),
                      number='RECEIPT-1', declared_by=events['admin'].id)
        db.add(row)
        db.flush()
        return row.id, created


def test_only_confirmation_emits_once_and_preserves_decimal(events):
    payment_id, created = payment(events)
    assert queued(events, 'request.payment_received') == []
    with events['sessions'].begin() as db:
        row = db.get(Payment, payment_id)
        row.status = 'confirmed'
        row.confirmed_at = utcnow()
        db.flush()
        row.comment = 'Accounting note changes no money'
    receipts = queued(events, 'request.payment_received')
    assert len(receipts) == 1
    data = receipts[0]['data']
    assert data['crm_request_id'] == created['request_id']
    assert data['payment']['amount'] == '123.45000001' and data['payment']['currency'] == 'USD'
    assert set(data['payment']) == {'id', 'amount', 'currency', 'confirmed_at'}


@pytest.mark.parametrize('kind', ['refund', 'correction'])
def test_money_reversal_has_its_own_stable_id_but_unallocation_emits_nothing(events, kind):
    payment_id, _ = payment(events)
    with events['sessions'].begin() as db:
        row = db.get(Payment, payment_id)
        row.status, row.confirmed_at = 'confirmed', utcnow()
    with events['sessions'].begin() as db:
        reversal = PaymentReversal(payment_id=payment_id, amount=Decimal('23.45'), kind=kind,
                                   reason='Returned funds', author_id=events['admin'].id)
        db.add(reversal)
        db.flush()
        refund_id = reversal.id
        refunded_at = reversal.created_at
    refunds = queued(events, 'request.payment_refunded')
    assert len(refunds) == 1
    assert refunds[0]['data']['refund']['id'] == refund_id
    assert refunds[0]['data']['refund']['payment_id'] == payment_id
    assert refunds[0]['data']['refund']['kind'] == kind
    assert refunds[0]['data']['refund']['refunded_at'] == as_utc(refunded_at).isoformat()
    assert refunds[0]['data']['site_request_id'] == queued(events, 'request.payment_received')[0]['data']['site_request_id']
    with events['sessions'].begin() as db:
        db.add(PaymentReversal(payment_id=payment_id, amount=Decimal('1'), kind='unallocate',
                               reason='Changed allocation', author_id=events['admin'].id))
    assert len(queued(events, 'request.payment_refunded')) == 1


def test_rollback_does_not_leave_a_payment_conversion(events):
    payment_id, _ = payment(events)
    with events['sessions']() as db:
        row = db.get(Payment, payment_id)
        row.status, row.confirmed_at = 'confirmed', utcnow()
        db.flush()
        db.rollback()
    assert queued(events, 'request.payment_received') == []


def test_backfill_is_readonly_by_default_and_idempotent(events):
    payment_id, _ = payment(events)
    with events['sessions'].begin() as db:
        row = db.get(Payment, payment_id)
        row.status, row.confirmed_at = 'confirmed', utcnow()
    with events['sessions'].begin() as db:
        db.execute(delete(OutboxEvent).where(OutboxEvent.payload['type'].as_string() == 'request.payment_received'))
    with events['sessions'].begin() as db:
        assert backfill(db)['receipts'] == 1
    assert queued(events, 'request.payment_received') == []
    with events['sessions'].begin() as db:
        assert backfill(db, apply=True)['receipts'] == 1
    with events['sessions'].begin() as db:
        report = backfill(db, apply=True)
        assert report['receipts'] == 0 and report['already_queued'] == 1
    assert len(queued(events, 'request.payment_received')) == 1


def test_backfill_includes_corrections_without_counting_unallocations(events):
    payment_id, _ = payment(events)
    with events['sessions'].begin() as db:
        row = db.get(Payment, payment_id)
        row.status, row.confirmed_at = 'confirmed', utcnow()
    with events['sessions'].begin() as db:
        for kind in ('correction', 'unallocate'):
            db.add(PaymentReversal(payment_id=payment_id, amount=Decimal('10'), kind=kind,
                                   reason='Ledger change', author_id=events['admin'].id))
    with events['sessions'].begin() as db:
        db.execute(delete(OutboxEvent).where(OutboxEvent.payload['type'].as_string().in_(
            ('request.payment_received', 'request.payment_refunded'))))
    with events['sessions'].begin() as db:
        report = backfill(db)
        assert report['receipts'] == report['refunds'] == 1
    assert queued(events, 'request.payment_refunded') == []
    with events['sessions'].begin() as db:
        report = backfill(db, apply=True)
        assert report['receipts'] == report['refunds'] == 1
    with events['sessions'].begin() as db:
        report = backfill(db, apply=True)
        assert report['receipts'] == report['refunds'] == 0 and report['already_queued'] == 2
    [correction] = queued(events, 'request.payment_refunded')
    assert correction['data']['refund']['kind'] == 'correction'
    [receipt] = queued(events, 'request.payment_received')
    # SQLite loads naive UTC values; backfill still follows the site's aware-datetime contract.
    assert datetime.fromisoformat(correction['data']['refund']['refunded_at']).tzinfo is not None
    assert datetime.fromisoformat(receipt['data']['payment']['confirmed_at']).tzinfo is not None
