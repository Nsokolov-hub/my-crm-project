"""Compact request numbers allocate atomically and survive removing the latest request."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import delete, select
from test_postgres_commerce import postgres_commerce as postgres_commerce

from app.crm.codes import next_request_number
from app.crm.models import NumberCounter, Request

pytestmark = pytest.mark.postgres


def test_compact_request_numbers_concurrent_rollover_and_deleted_tail(postgres_commerce):
    env = postgres_commerce
    with env['sessions'].begin() as db:
        counter = db.get(NumberCounter, 'requests')
        if counter is None:
            db.add(NumberCounter(key='requests', value=998))
        else:
            counter.value = 998

    def insert(index):
        with env['sessions'].begin() as db:
            row = Request(number=next_request_number(db), title=f'Concurrent {index}',
                          client_id=env['client_id'], owner_id=env['owner_id'])
            db.add(row)
            db.flush()
            return row.number

    with ThreadPoolExecutor(max_workers=6) as workers:
        numbers = list(workers.map(insert, range(24)))
    assert len(numbers) == len(set(numbers)) == 24
    assert set(numbers) == {'999', '1000', *(f'A{i}' for i in range(1, 23))}
    with env['sessions'].begin() as db:
        db.execute(delete(Request).where(Request.number == 'A22'))
    assert insert(24) == 'A23'
    with env['sessions']() as db:
        assert db.scalar(select(NumberCounter.value).where(NumberCounter.key == 'requests')) == 1023
