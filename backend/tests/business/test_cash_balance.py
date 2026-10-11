"""Daily cash balances: the fast running-total reader matches the straightforward definition."""

import random
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.business.cashflow import _balance_reader


def reference_balance(facts, anchors, day, before=False):
    """The original definition: latest balance on or before day plus every later fact up to day."""
    valid = [row for row in sorted(anchors, key=lambda row: row.balance_date) if row.balance_date <= day]
    anchor = valid[-1] if valid else None
    value = anchor.amount if anchor else Decimal(0)
    for row in facts:
        if (not anchor or row.actual_date >= anchor.balance_date) and (
                row.actual_date < day if before else row.actual_date <= day):
            value += row.amount if row.direction == "income" else -row.amount
    return value, anchor


def test_running_total_matches_definition_on_random_ledgers():
    generator = random.Random(20261011)
    origin = date(2026, 1, 1)
    for _ in range(200):
        facts = [SimpleNamespace(actual_date=origin + timedelta(days=generator.randint(0, 90)),
                                 direction=generator.choice(["income", "expense"]),
                                 amount=Decimal(generator.randint(1, 10_000_00)) / 100)
                 for _ in range(generator.randint(0, 40))]
        anchor_days = generator.sample(range(0, 90), generator.randint(0, 4))
        anchors = [SimpleNamespace(balance_date=origin + timedelta(days=day),
                                   amount=Decimal(generator.randint(-500_00, 500_000_00)) / 100)
                   for day in anchor_days]
        balance_at = _balance_reader(facts, anchors)
        for offset in range(-2, 95):
            day = origin + timedelta(days=offset)
            for before in (False, True):
                assert balance_at(day, before=before) == reference_balance(facts, anchors, day, before), (day, before)


def test_balance_starts_from_the_dated_balance_and_counts_later_cash():
    facts = [SimpleNamespace(actual_date=date(2026, 10, 1), direction="income", amount=Decimal("100")),
             SimpleNamespace(actual_date=date(2026, 10, 5), direction="expense", amount=Decimal("30")),
             SimpleNamespace(actual_date=date(2026, 10, 5), direction="income", amount=Decimal("5"))]
    anchor = SimpleNamespace(balance_date=date(2026, 10, 5), amount=Decimal("1000"))
    balance_at = _balance_reader(facts, [anchor])
    assert balance_at(date(2026, 10, 4)) == (Decimal("100"), None)
    assert balance_at(date(2026, 10, 5)) == (Decimal("975"), anchor)
    assert balance_at(date(2026, 10, 5), before=True) == (Decimal("1000"), anchor)
