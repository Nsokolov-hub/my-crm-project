"""Idempotently queue real historical payment facts after the site's receiver has been upgraded.

python -m app.integrations.site_payment_backfill [--apply] [--limit 1000]
The default reports counts only; no contacts, request contents or credentials are printed.
"""

import argparse
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.commerce.models import Payment, PaymentReversal
from app.core.models import OutboxEvent
from app.integrations.models import SiteRequest
from app.integrations.site_events import enabled, payment_event


def backfill(db: Session, *, apply: bool = False, limit: int = 1000) -> dict[str, int | bool]:
    query = select(Payment).where(
        Payment.status == 'confirmed', Payment.confirmed_at.is_not(None),
        Payment.request_id.in_(select(SiteRequest.request_id).where(SiteRequest.request_id.is_not(None))))
    query = query.order_by(Payment.confirmed_at, Payment.id).limit(limit + 1)
    if apply:
        if not enabled():
            raise ValueError('Site events integration is disabled')
        query = query.with_for_update()
    payments = db.scalars(query).all()
    report: dict[str, int | bool] = {'receipts': 0, 'refunds': 0, 'already_queued': 0,
                                     'truncated': len(payments) > limit, 'applied': apply}
    for payment in payments[:limit]:
        refunds = db.scalars(select(PaymentReversal).where(
            PaymentReversal.payment_id == payment.id, PaymentReversal.kind.in_(('refund', 'correction')))
            .order_by(PaymentReversal.created_at, PaymentReversal.id)).all()
        for fact in [None, *refunds]:
            kind = 'request.payment_refunded' if fact is not None else 'request.payment_received'
            key = f'site-event:{kind}:{fact.id if fact is not None else payment.id}'
            if db.scalar(select(OutboxEvent.id).where(OutboxEvent.event_key == key)):
                report['already_queued'] += 1
                continue
            report['refunds' if fact is not None else 'receipts'] += 1
            if apply:
                payment_event(db, payment, refund=fact)
                db.flush()
    return report


def main() -> None:
    import app.models  # noqa: F401
    from app.core.db import SessionLocal

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--limit', type=int, default=1000, choices=range(1, 10001), metavar='1..10000')
    args = parser.parse_args()
    with SessionLocal.begin() as db:
        print(json.dumps(backfill(db, apply=args.apply, limit=args.limit)))


if __name__ == '__main__':
    main()
