"""Counterparty numbers must remain unique under concurrent imports and manual creation."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from test_postgres_commerce import postgres_commerce as postgres_commerce

from app.crm.models import Counterparty

pytestmark = pytest.mark.postgres


def test_concurrent_counterparty_codes_are_unique(postgres_commerce):
    env = postgres_commerce

    def insert(index):
        with env["sessions"].begin() as db:
            rows = [
                Counterparty(name=f"Concurrent {index} / {i}", kind="supplier", owner_id=env["owner_id"])
                for i in range(3)
            ]
            db.add_all(rows)
            db.flush()
            return [row.internal_code for row in rows]

    with ThreadPoolExecutor(max_workers=6) as workers:
        codes = [code for batch in workers.map(insert, range(12)) for code in batch]
    assert len(codes) == 36 and len(set(codes)) == 36
    assert sorted(codes) == list(range(min(codes), max(codes) + 1))
