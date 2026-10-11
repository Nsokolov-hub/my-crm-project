"""Live events for open pages, so they refresh at once instead of waiting for their next poll.

A command calls publish() inside its transaction; PostgreSQL delivers a NOTIFY only when that
transaction commits, so a page never refreshes for a change that was rolled back. Every API
process keeps one LISTEN connection (EventHub) and forwards each event to the open streams of the
users it names. Events say what to reload (kind and ids), never content: the page reloads it through
the usual permission checks. Without PostgreSQL there are no events and pages keep polling.
"""

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import Iterable

import psycopg
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

logger = logging.getLogger("crm.events")
CHANNEL = "crm_events"
# A NOTIFY payload is limited to 8000 bytes; larger audiences are sent in several notifications.
USERS_PER_EVENT = 100
STREAM_BACKLOG = 100


def publish(db: Session, kind: str, users: Iterable[str], **data: str) -> None:
    """Tell the users' open pages that `kind` changed, once the current transaction commits."""
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    audience = sorted(set(users))
    for start in range(0, len(audience), USERS_PER_EVENT):
        payload = json.dumps({"kind": kind, "users": audience[start:start + USERS_PER_EVENT], **data})
        db.execute(text("SELECT pg_notify(:channel, :payload)"), {"channel": CHANNEL, "payload": payload})


class EventHub:
    """The process's LISTEN connection and the event queues of the open streams, by user."""

    def __init__(self) -> None:
        self.streams: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, user_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=STREAM_BACKLOG)
        self.streams[user_id].add(queue)
        return queue

    def unsubscribe(self, user_id: str, queue: asyncio.Queue) -> None:
        self.streams[user_id].discard(queue)
        if not self.streams[user_id]:
            del self.streams[user_id]

    def dispatch(self, event: dict) -> None:
        message = {key: value for key, value in event.items() if key != "users"}
        targets = self.streams.values() if event.get("kind") == "resync" else (
            self.streams.get(user, set()) for user in event.get("users", ()))
        for queues in targets:
            for queue in queues:
                # A stream that stopped reading misses events; the page resyncs when it reconnects.
                if not queue.full():
                    queue.put_nowait(message)

    async def listen(self, database_url: str, retry_seconds: float = 5) -> None:
        """Forward NOTIFY events until cancelled, reconnecting after a lost connection."""
        conninfo = make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as connection:
                    await connection.execute(f"LISTEN {CHANNEL}")
                    # Events sent while the connection was down are lost: open pages reload everything.
                    self.dispatch({"kind": "resync"})
                    async for notification in connection.notifies():
                        try:
                            self.dispatch(json.loads(notification.payload))
                        except ValueError:
                            logger.warning(json.dumps({"event": "live_event_invalid"}))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(json.dumps({"event": "live_events_disconnected"}), exc_info=True)
            await asyncio.sleep(retry_seconds)


hub = EventHub()
