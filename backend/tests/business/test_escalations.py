"""Escalations to managers: overdue tasks and unconfirmed payments, to those who may see them."""

from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.business.models import CalendarEntry
from app.business.worker import reminders
from app.core.models import Notification, PermissionGrant, User
from app.crm.models import Counterparty, Request, RequestMember, Task
from tests.support.queries import statements_while

COMPANY = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 11, 10, tzinfo=COMPANY)


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=COMPANY).astimezone(timezone.utc)


def team(env):
    """A second manager who decides approvals but sees only own tasks and requests."""
    with env["sessions"].begin() as db:
        lead = User(email="lead@example.com", name="Руководитель группы", password_hash="x")
        db.add(lead)
        db.flush()
        db.add_all(PermissionGrant(user_id=lead.id, code=code, scope="own")
                   for code in ("approvals.decide", "tasks.read", "requests.read"))
        client = Counterparty(name="Клиент эскалаций", owner_id=env["admin"].id)
        db.add(client)
        db.flush()
        requests = [Request(number=f"ESC-{index}", title="Заявка", client_id=client.id, owner_id=env["admin"].id)
                    for index in range(2)]
        db.add_all(requests)
        db.flush()
        db.add(RequestMember(request_id=requests[1].id, user_id=lead.id))
        return lead, requests[0].id, requests[1].id


def add_task(env, due_at, assignee, status="assigned"):
    with env["sessions"].begin() as db:
        task = Task(title="Отгрузка", assignee_id=assignee.id, author_id=env["admin"].id, due_at=due_at, status=status)
        db.add(task)
        db.flush()
        return task.id


def add_entry(env, planned, status="pending", request_id=None):
    with env["sessions"].begin() as db:
        entry = CalendarEntry(direction="expense", planned_date=planned, amount=Decimal("10"), currency="RUB",
                              purpose="Оплата поставщику", request_id=request_id, status=status,
                              responsible_id=env["manager"].id, author_id=env["admin"].id)
        db.add(entry)
        db.flush()
        return entry.id


def run(env, now=NOW):
    with env["sessions"].begin() as db:
        reminders(db, now)


def delivered(env, entity_type):
    with env["sessions"]() as db:
        return {(row.entity_id, row.user_id) for row in
                db.scalars(select(Notification).where(Notification.entity_type == entity_type))}


def test_tasks_two_days_overdue_are_escalated_to_managers_who_see_them(crm):
    lead, _, _ = team(crm)
    admin, manager = crm["admin"], crm["manager"]
    managers_task = add_task(crm, at(8, 9), manager)
    leads_task = add_task(crm, at(6, 9), lead)
    late_evening = add_task(crm, at(9, 23, 59), manager, status="completion_pending")
    add_task(crm, at(10, 0), manager)  # one day overdue
    add_task(crm, at(1, 9), manager, status="done")

    run(crm)
    expected = {(managers_task, admin.id), (leads_task, admin.id), (leads_task, lead.id), (late_evening, admin.id)}
    assert delivered(crm, "task") == expected
    run(crm)
    assert delivered(crm, "task") == expected

    with crm["sessions"].begin() as db:
        db.get(Task, managers_task).version += 1
    run(crm)
    with crm["sessions"]() as db:
        keys = set(db.scalars(select(Notification.event_key).where(Notification.entity_id == managers_task)))
    assert len(keys) == 2


def test_unconfirmed_payments_are_escalated_to_those_who_may_read_the_request(crm):
    lead, foreign, shared = team(crm)
    admin, manager = crm["admin"], crm["manager"]
    plain = add_entry(crm, date(2026, 10, 10))
    add_entry(crm, date(2026, 10, 11))
    add_entry(crm, date(2026, 10, 1), status="confirmed")
    of_foreign = add_entry(crm, date(2026, 10, 5), status="draft", request_id=foreign)
    of_shared = add_entry(crm, date(2026, 10, 5), status="approved", request_id=shared)

    run(crm)
    expected = {(plain, manager.id), (plain, admin.id), (plain, lead.id), (of_foreign, admin.id),
                (of_shared, admin.id), (of_shared, lead.id)}
    assert delivered(crm, "calendar_entry") == expected
    run(crm)
    assert delivered(crm, "calendar_entry") == expected


def test_escalation_pass_does_not_query_per_task_or_payment(crm):
    lead, foreign, shared = team(crm)

    def steady_state():
        run(crm)
        return len(statements_while(crm, lambda: run(crm)))

    def overdue(count):
        for _ in range(count):
            add_task(crm, at(1, 9), crm["manager"])
            add_task(crm, at(1, 9), lead)
            add_entry(crm, date(2026, 10, 1))
            add_entry(crm, date(2026, 10, 1), request_id=foreign)
            add_entry(crm, date(2026, 10, 1), request_id=shared)

    overdue(2)
    few = steady_state()
    overdue(6)
    assert steady_state() == few
