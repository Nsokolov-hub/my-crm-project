"""Logs as one JSON object per line, so a log collector reads every record without patterns.

Messages that are JSON objects (the API's events) keep their fields; any other message goes into
"message". A traceback becomes the "exception" field of the same line instead of following it as
plain text, and records of an HTTP request carry its requestId.
"""

import json
import logging
from datetime import datetime, timezone

from app.core.service import BACKGROUND, correlation_id

# Attributes every LogRecord has; anything else was passed with extra={...} and is logged as a field.
# uvicorn adds color_message, a copy of the message with terminal colours.
_RECORD_FIELDS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName", "color_message"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
        }
        message = record.getMessage()
        try:
            fields = json.loads(message) if message.startswith("{") else None
        except ValueError:
            fields = None
        if isinstance(fields, dict):
            entry.update(fields)
        else:
            entry["message"] = message
        entry.update({key: value for key, value in vars(record).items() if key not in _RECORD_FIELDS})
        request = correlation_id.get()
        if request != BACKGROUND:
            entry.setdefault("requestId", request)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Send every logger, uvicorn's included, through one JSON handler on standard error."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        # Replace uvicorn's text handlers; a logger it switched off (--no-access-log) stays off.
        if logger.handlers:
            logger.handlers[:] = []
            logger.propagate = True
