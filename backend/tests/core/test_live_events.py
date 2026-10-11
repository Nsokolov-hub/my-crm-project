"""Live events: who receives them, and that only committed changes are announced."""

import asyncio
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.core.routes as core_routes
from app.core.config import settings
from app.core.events import EventHub, hub, publish
from tests.support.crm import login
from tests.support.queries import statements_while


def test_events_reach_only_the_streams_of_the_users_they_name():
    async def scenario():
        events = EventHub()
        alice, alice_tab, bob = events.subscribe("alice"), events.subscribe("alice"), events.subscribe("bob")
        events.dispatch({"kind": "chat", "users": ["alice"], "chat": "c1"})
        assert alice.get_nowait() == alice_tab.get_nowait() == {"kind": "chat", "chat": "c1"}
        assert bob.empty()
        events.dispatch({"kind": "resync"})
        assert alice.get_nowait() == bob.get_nowait() == {"kind": "resync"}
        events.unsubscribe("bob", bob)
        events.dispatch({"kind": "notification", "users": ["bob"]})
        assert "bob" not in events.streams
        for _ in range(200):  # a stream that stopped reading does not block the others
            events.dispatch({"kind": "notification", "users": ["alice"]})
        assert alice.qsize() == alice.maxsize

    asyncio.run(scenario())


def test_events_are_sent_with_postgresql_only(crm):
    with crm["sessions"]() as db:
        sent = statements_while(crm, lambda: publish(db, "notification", ["user"]))
        postgresql = db.get_bind().dialect.name == "postgresql"
    assert len(sent) == (1 if postgresql else 0)


def test_the_stream_needs_a_session_and_delivers_the_users_events(crm, monkeypatch):
    assert crm["client"].get("/api/v1/events").status_code == 401
    login(crm)
    # The test client reads a response to its end, so the stream gets a short life here.
    monkeypatch.setattr(core_routes, "KEEPALIVE_SECONDS", 0.05)
    monkeypatch.setattr(core_routes, "STREAM_SECONDS", 0.3)
    subscribe = hub.subscribe

    def subscribe_with_pending_event(user_id):
        queue = subscribe(user_id)
        hub.dispatch({"kind": "notification", "users": [crm["admin"].id]})
        hub.dispatch({"kind": "chat", "users": ["someone else"], "chat": "c1"})
        return queue

    monkeypatch.setattr(hub, "subscribe", subscribe_with_pending_event)
    response = crm["client"].get("/api/v1/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.startswith("retry: 5000\n\nevent: notification\ndata: {\"kind\": \"notification\"}\n\n")
    assert ": keepalive" in response.text and "chat" not in response.text
    assert not hub.streams


@pytest.mark.postgres
def test_only_committed_changes_are_announced():
    url = os.environ.get("POSTGRES_TEST_DATABASE_URL", settings.database_url)
    if not url.startswith("postgresql"):
        pytest.skip("Requires PostgreSQL database")
    engine = create_engine(url)

    def change(commit):
        with Session(engine) as db:
            publish(db, "chat", ["reader"], chat="kept" if commit else "rolled-back")
            db.commit() if commit else db.rollback()

    async def scenario():
        events = EventHub()
        listener = asyncio.create_task(events.listen(url, retry_seconds=0.1))
        stream = events.subscribe("reader")
        try:
            assert await asyncio.wait_for(stream.get(), 5) == {"kind": "resync"}
            await asyncio.to_thread(change, False)
            await asyncio.to_thread(change, True)
            assert await asyncio.wait_for(stream.get(), 5) == {"kind": "chat", "chat": "kept"}
            await asyncio.sleep(0.2)
            assert stream.empty()
        finally:
            listener.cancel()

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()
