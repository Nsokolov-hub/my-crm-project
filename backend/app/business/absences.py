"""Employee absences and the availability check used when work is assigned."""

from datetime import timedelta

from fastapi import APIRouter
from pydantic import AwareDatetime, Field
from sqlalchemy import select

from app.commerce.procurement import DB, Actor
from app.commerce.schemas import Command
from app.core.db import utcnow
from app.core.errors import error
from app.core.models import User
from app.core.security import require_permission
from app.core.service import advisory, audit, idem, notify, page, serialize

from .models import EmployeeAbsence

router = APIRouter(tags=["Платёжный календарь и заказы поставщикам"])


def ensure_available(db, user_id):
    advisory(db, "employee-availability:" + user_id)
    user = db.get(User, user_id)
    if not user or not user.active:
        error("USER_INACTIVE", "Выберите действующего сотрудника")
    now = utcnow()
    if db.scalar(
        select(EmployeeAbsence.id).where(
            EmployeeAbsence.user_id == user_id,
            EmployeeAbsence.starts_at <= now,
            EmployeeAbsence.ends_at > now,
        )
    ):
        error("EMPLOYEE_ABSENT", "Сотрудник отсутствует. Выберите другого исполнителя", field="assignee_id")


class AbsenceIn(Command):
    user_id: str
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    reason: str = Field(min_length=1, max_length=4000)


@router.get("/employee-absences")
def absences(db: DB, user: Actor):
    require_permission(db, user, "approvals.decide")
    result = page(db, select(EmployeeAbsence).order_by(EmployeeAbsence.starts_at.desc()), 1, 100)
    for row in result["items"]:
        row["employee_name"] = db.get(User, row["user_id"]).name
    return result


@router.post("/employee-absences")
def absence_create(data: AbsenceIn, db: DB, user: Actor):
    require_permission(db, user, "approvals.decide")

    def operation():
        from app.crm.models import Task

        advisory(db, "employee-availability:" + data.user_id)
        if data.ends_at <= data.starts_at or not db.get(User, data.user_id):
            error("ABSENCE_PERIOD", "Проверьте сотрудника и период отсутствия")
        if db.scalar(
            select(EmployeeAbsence.id).where(
                EmployeeAbsence.user_id == data.user_id,
                EmployeeAbsence.starts_at < data.ends_at,
                EmployeeAbsence.ends_at > data.starts_at,
            )
        ):
            error("ABSENCE_OVERLAP", "Периоды отсутствия пересекаются")
        row = EmployeeAbsence(**data.model_dump(exclude={"idempotency_key"}), author_id=user.id)
        db.add(row)
        db.flush()
        delta = data.ends_at - data.starts_at + timedelta(hours=24)
        tasks = db.scalars(
            select(Task)
            .where(Task.assignee_id == data.user_id, Task.status.in_(["assigned", "in_progress"]))
            .with_for_update()
        ).all()
        for task in tasks:
            before = serialize(task)
            task.due_at += delta
            task.version += 1
            audit(db, user, "task", task.id, "absence_postponed", before, serialize(task), data.reason)
        notify(
            db,
            data.user_id,
            f"absence:{row.id}",
            f"Зарегистрировано отсутствие. Перенесено задач: {len(tasks)}",
            "employee_absence",
            row.id,
        )
        audit(db, user, "employee_absence", row.id, "created", after=serialize(row), reason=data.reason)
        return {**serialize(row), "postponed_tasks": len(tasks)}

    return idem(db, user, data.idempotency_key, "absence.create", data.model_dump(mode="json"), operation)
