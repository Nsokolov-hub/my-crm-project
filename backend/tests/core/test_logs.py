"""Every log record is one JSON line a collector can parse."""

import json
import logging
import sys

from app.core.logs import JsonFormatter
from app.core.service import correlation_id


def render(message, *args, exc_info=None, **extra):
    record = logging.getLogger("crm.test").makeRecord(
        "crm.test", logging.WARNING, __file__, 1, message, args, exc_info, extra=extra)
    line = JsonFormatter().format(record)
    assert "\n" not in line
    return json.loads(line)


def test_event_messages_keep_their_fields_with_time_level_and_logger():
    entry = render(json.dumps({"event": "outbox_failed", "attempts": 3}))
    assert entry["event"] == "outbox_failed" and entry["attempts"] == 3
    assert entry["level"] == "warning" and entry["logger"] == "crm.test" and entry["time"].endswith("+00:00")


def test_plain_messages_and_extra_fields():
    entry = render("started %s workers", 2, port=8000)
    assert entry["message"] == "started 2 workers" and entry["port"] == 8000


def test_a_traceback_stays_on_the_same_line():
    try:
        raise ValueError("broken")
    except ValueError:
        entry = render(json.dumps({"event": "unhandled_error"}), exc_info=sys.exc_info())
    assert entry["event"] == "unhandled_error"
    assert "Traceback" in entry["exception"] and "ValueError: broken" in entry["exception"]


def test_records_of_a_request_carry_its_code():
    assert "requestId" not in render("background work")
    token = correlation_id.set("req-42")
    try:
        assert render("inside a request")["requestId"] == "req-42"
    finally:
        correlation_id.reset(token)
