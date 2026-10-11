"""Workflow approvals: submitting objects for review and deciding on them."""

from datetime import date
from typing import Literal

from fastapi import APIRouter
from pydantic import Field
from sqlalchemy import or_, select

from app.business.common import calendar_visible, managers
from app.commerce.calculator import dec
from app.commerce.models import Calculation, Execution
from app.commerce.procurement import DB, Actor
from app.commerce.schemas import VersionCommand
from app.core.db import utcnow
from app.core.errors import error
from app.core.security import (
    can,
    check_request,
    has_request_permission,
    request_predicate,
    require_permission,
    task_predicate,
)
from app.core.service import audit, check_version, idem, lock, notify, page, serialize
from app.crm.models import Request as CRMRequest

from . import cashflow
from .models import CalendarEntry, SupplierOrderLine, WorkflowReview

router = APIRouter(tags=["Платёжный календарь и заказы поставщикам"])


def submit_review(db, user, kind, entity, title, snapshot, request_id=None):
    old = db.scalar(
        select(WorkflowReview).where(
            WorkflowReview.kind == kind,
            WorkflowReview.entity_id == entity.id,
            WorkflowReview.source_version == entity.version,
        )
    )
    if old:
        return old
    reviewers = managers(db)
    if not reviewers:
        error("MANAGER_REQUIRED", "Назначьте руководителя с правом принятия решений")
    row = WorkflowReview(
        kind=kind,
        entity_id=entity.id,
        source_version=entity.version,
        title=title[:250],
        submitted_by=user.id,
        snapshot=snapshot,
        request_id=request_id,
    )
    db.add(row)
    db.flush()
    for target in reviewers:
        if kind == "task":
            from app.crm.models import Task

            if not db.scalar(select(Task.id).where(Task.id == entity.id, task_predicate(db, target))):
                continue
        if not request_id or has_request_permission(db, target, request_id, "approvals.decide"):
            notify(db, target.id, f"review:{row.id}", title[:250], "workflow_review", row.id)
    audit(db, user, "workflow_review", row.id, "submitted", after=serialize(row))
    return row


def review_calculation(db, user, calculation):
    # The restriction applies to sales work, including users combining several roles.
    if not can(db, user, "documents.write") or can(db, user, "approvals.decide"):
        return
    low = [
        line
        for line in calculation.snapshot.get("lines", [])
        if (
            (line.get("product_group") or {}).get("slug") in ("reference_standards", "standards", "reagents")
            or any(
                word in (line.get("product_group") or {}).get("name", "").lower()
                for word in ("стандарт", "реактив")
            )
        )
        and dec((line.get("detail") or {}).get("markup_coefficient", "1")) <= dec("1.25")
    ]
    if low:
        submit_review(
            db,
            user,
            "calculation",
            calculation,
            "Наценка 25% или ниже: согласование расчёта",
            {
                "calculation_id": calculation.id,
                "digest": calculation.digest,
                "lines": [
                    {
                        "description": line["description"],
                        "markup_coefficient": line["detail"]["markup_coefficient"],
                    }
                    for line in low
                ],
            },
            calculation.request_id,
        )


def require_calculation_approved(db, calculation):
    row = db.scalar(
        select(WorkflowReview).where(
            WorkflowReview.kind == "calculation", WorkflowReview.entity_id == calculation.id
        )
    )
    if row and (row.status != "approved" or row.snapshot["digest"] != calculation.digest):
        error(
            "CALCULATION_APPROVAL_REQUIRED", "Руководитель должен согласовать этот расчёт перед выпуском КП"
        )


class ReviewDecision(VersionCommand):
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=1, max_length=4000)
    actual_date: date | None = None
    outside_payment_days: bool = False


@router.get("/workflow-approvals")
def reviews(db: DB, user: Actor, status: str | None = None):
    query = select(WorkflowReview)
    if not can(db, user, "approvals.decide"):
        query = query.where(WorkflowReview.submitted_by == user.id)
    query = query.where(
        or_(
            WorkflowReview.request_id.is_(None),
            WorkflowReview.request_id.in_(select(CRMRequest.id).where(request_predicate(db, user))),
        )
    )
    from app.crm.models import Task

    query = query.where(
        or_(
            WorkflowReview.kind != "task",
            WorkflowReview.entity_id.in_(select(Task.id).where(task_predicate(db, user))),
        )
    )
    allowed_requests = select(CRMRequest.id).where(request_predicate(db, user))
    blocked_orders = (
        select(SupplierOrderLine.order_id)
        .join(Execution, Execution.id == SupplierOrderLine.execution_id)
        .where(Execution.request_id.not_in(allowed_requests))
    )
    hidden_calendar = select(CalendarEntry.id).where(CalendarEntry.supplier_order_id.in_(blocked_orders))
    query = query.where(
        or_(WorkflowReview.kind != "calendar", WorkflowReview.entity_id.not_in(hidden_calendar))
    )
    if status:
        query = query.where(WorkflowReview.status == status)
    return page(db, query.order_by(WorkflowReview.created_at.desc()), 1, 100)


@router.post("/workflow-approvals/{review_id}/decision")
def review_decision(review_id: str, data: ReviewDecision, db: DB, user: Actor):
    require_permission(db, user, "approvals.decide")

    def operation():
        row = lock(db, WorkflowReview, review_id)
        check_version(row, data.version)
        if row.request_id:
            check_request(db, user, row.request_id, "approvals.decide")
        if row.status != "pending":
            error("REVIEW_STATE", "По этому согласованию уже принято решение", 409)
        if row.kind == "calculation":
            calc = db.get(Calculation, row.entity_id)
            if not calc or calc.digest != row.snapshot["digest"]:
                error("REVIEW_STALE", "Расчёт изменился. Требуется новое согласование", 409)
        elif row.kind == "calendar":
            entry = lock(db, CalendarEntry, row.entity_id)
            calendar_visible(db, user, entry)
            if entry.version != row.source_version or entry.status != "pending":
                error("REVIEW_STALE", "Платёж изменился", 409)
            if data.decision == "approved":
                cashflow.confirm(db, user, entry, data.actual_date, data.outside_payment_days or entry.outside_payment_days)
            else:
                entry.status = "rejected"
                entry.version += 1
        elif row.kind == "task":
            from app.crm.models import Task

            task = lock(db, Task, row.entity_id)
            if not db.scalar(select(Task.id).where(Task.id == task.id, task_predicate(db, user))):
                error("NOT_FOUND", "Задача недоступна", 404)
            if task.version != row.source_version or task.status != "completion_pending":
                error("REVIEW_STALE", "Задача изменена", 409)
            task.status = (
                row.snapshot.get("requested_status", "completed")
                if data.decision == "approved"
                else "in_progress"
            )
            task.completed_at = utcnow() if task.status in ("completed", "cancelled") else None
            task.version += 1
        row.status, row.reason, row.decided_by, row.decided_at = data.decision, data.reason, user.id, utcnow()
        row.version += 1
        notify(
            db,
            row.submitted_by,
            f"review-decision:{row.id}",
            f"{row.title}: {'согласовано' if row.status == 'approved' else 'отклонено'}"[:250],
            "workflow_review",
            row.id,
        )
        audit(db, user, "workflow_review", row.id, "decided", after=serialize(row), reason=data.reason)
        return serialize(row)

    return idem(
        db, user, data.idempotency_key, f"review:{review_id}", data.model_dump(mode="json"), operation
    )
