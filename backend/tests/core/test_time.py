"""Stored timestamps are compared as UTC instants, whatever zone the database reports them in."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.core.db import as_utc


def test_aware_values_are_converted_not_relabelled():
    moscow_noon = datetime(2026, 1, 1, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    converted = as_utc(moscow_noon)
    assert converted == datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
    assert converted.utcoffset().total_seconds() == 0


def test_naive_values_are_read_as_utc():
    assert as_utc(datetime(2026, 1, 1, 9, 0)) == datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
