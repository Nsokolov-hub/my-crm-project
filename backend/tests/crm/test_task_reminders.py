"""Task reminders: who is reminded and when, once per stage, with a fixed number of queries."""

import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.communication.worker import ReminderPolicy, reminder_at, reminder_horizon, schedule_reminders
from app.core.errors import DomainError
from app.core.models import AppSetting, OutboxEvent
from app.crm.models import Task
from tests.support.queries import statements_while

COMPANY = ZoneInfo("Europe/Moscow")


def at(day, hour, minute=0):
    """A moment of October 2026 in company time (Friday is the 9th, Monday the 12th)."""
    return datetime(2026, 10, day, hour, minute, tzinfo=COMPANY).astimezone(timezone.utc)


FRIDAY_NOON = at(9, 12)


def add_task(env, due_at, *, status="assigned", assignee=None):
    with env["sessions"].begin() as db:
        task = Task(title="Позвонить клиенту", assignee_id=(assignee or env["manager"]).id,
                    author_id=env["admin"].id, due_at=due_at, status=status)
        db.add(task)
        db.flush()
        return task.id


def set_policy(env, **value):
    with env["sessions"].begin() as db:
        setting = db.scalar(select(AppSetting).where(AppSetting.key == "task_reminders",
                                                     AppSetting.status == "published"))
        setting.value = value


def schedule(env, now):
    with env["sessions"].begin() as db:
        return schedule_reminders(db, now)


def reminders(env):
    with env["sessions"]() as db:
        return {(event.payload["task_id"], event.payload["stage"]) for event in
                db.scalars(select(OutboxEvent).where(OutboxEvent.kind == "task.reminder"))}


def test_reminds_a_working_day_ahead_and_when_overdue_once_per_stage(crm):
    assert FRIDAY_NOON.astimezone(COMPANY).weekday() == 4
    monday_morning = add_task(crm, at(12, 10))  # reminded on Friday at 10:00
    monday_afternoon = add_task(crm, at(12, 13))  # reminded on Friday at 13:00
    tuesday = add_task(crm, at(13, 9))
    overdue = add_task(crm, at(8, 18), status="in_progress")
    add_task(crm, at(8, 18), status="done")
    add_task(crm, at(30, 9))

    assert schedule(crm, FRIDAY_NOON) == 2
    assert reminders(crm) == {(monday_morning, "approaching"), (overdue, "overdue")}
    assert schedule(crm, FRIDAY_NOON) == 0

    assert schedule(crm, at(9, 13)) == 1
    assert (monday_afternoon, "approaching") in reminders(crm)
    assert schedule(crm, at(12, 9, 30)) == 1
    assert (tuesday, "approaching") in reminders(crm)
    assert schedule(crm, at(12, 10)) == 1
    assert (monday_morning, "overdue") in reminders(crm)


def test_a_new_assignee_or_due_date_is_reminded_again(crm):
    task_id = add_task(crm, at(12, 10))
    assert schedule(crm, FRIDAY_NOON) == 1
    with crm["sessions"].begin() as db:
        db.get(Task, task_id).assignee_id = crm["admin"].id
    assert schedule(crm, FRIDAY_NOON) == 1
    with crm["sessions"].begin() as db:
        db.get(Task, task_id).due_at = at(12, 11)
    assert schedule(crm, FRIDAY_NOON) == 1
    with crm["sessions"]() as db:
        keys = set(db.scalars(select(OutboxEvent.event_key)))
    assert len(keys) == 3


def test_policy_lead_minutes_holidays_and_switches(crm):
    soon, later = add_task(crm, FRIDAY_NOON + timedelta(minutes=80)), add_task(crm, FRIDAY_NOON + timedelta(minutes=100))
    set_policy(crm, lead_minutes=90)
    assert schedule(crm, FRIDAY_NOON) == 1
    assert reminders(crm) == {(soon, "approaching")}

    # With Friday a holiday, a Monday task is reminded on Thursday.
    monday = add_task(crm, at(12, 10))
    set_policy(crm, lead_working_days=1, holidays=["2026-10-09"])
    assert schedule(crm, at(8, 11)) == 1
    assert (monday, "approaching") in reminders(crm)
    assert (later, "approaching") not in reminders(crm)

    set_policy(crm, enabled=False)
    add_task(crm, at(1, 10))
    assert schedule(crm, FRIDAY_NOON) == 0

    set_policy(crm, working_days=[7])
    with pytest.raises(DomainError) as error:
        schedule(crm, FRIDAY_NOON)
    assert error.value.code == "REMINDER_SETTINGS_INVALID"


def test_reminder_pass_does_not_query_per_task(crm):
    def steady_state():
        schedule(crm, FRIDAY_NOON)  # creates the reminders of new tasks
        return len(statements_while(crm, lambda: schedule(crm, FRIDAY_NOON)))

    for _ in range(2):
        add_task(crm, at(8, 9))
        add_task(crm, at(12, 9))
    few = steady_state()
    for _ in range(8):
        add_task(crm, at(7, 9))
        add_task(crm, at(12, 8))
    assert steady_state() == few


@pytest.mark.parametrize("seed", range(4))
def test_horizon_never_skips_a_task_that_is_due_for_a_reminder(seed):
    """reminder_horizon() bounds the SQL scan; every task whose reminder time has come is within it."""
    rng = random.Random(seed)
    for _ in range(300):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=rng.randrange(60 * 24 * 365 * 2))
        holidays = sorted({now.date() + timedelta(days=rng.randrange(-5, 40)) for _ in range(rng.randrange(8))})
        policy = ReminderPolicy(
            timezone=rng.choice(["Europe/Moscow", "Europe/Berlin", "America/New_York", "Asia/Kolkata", "UTC"]),
            lead_working_days=rng.randrange(6),
            lead_minutes=rng.choice([None, None, rng.randrange(0, 60 * 24 * 5)]),
            working_days=sorted(rng.sample(range(7), rng.randrange(1, 8))),
            holidays=holidays,
        )
        horizon = reminder_horizon(now, policy)
        for _ in range(40):
            due_at = now + timedelta(minutes=rng.randrange(-60 * 24 * 3, 60 * 24 * 60))
            if reminder_at(due_at, policy) <= now:
                assert due_at <= horizon, (policy, now, due_at)
