import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from html import escape
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import AwareDatetime, EmailStr, Field, TypeAdapter, ValidationError
from sqlalchemy import or_, select

from app.commerce.calculator import dec, profitability_metrics
from app.commerce.files import workbook
from app.commerce.models import Calculation, CommercialDocument, Execution
from app.commerce.procurement import DB, Actor, product_view, rfq_table, supplier
from app.commerce.schemas import Command, Currency, Positive, VersionCommand
from app.core.config import settings
from app.core.db import utcnow
from app.core.errors import error
from app.core.models import AppSetting, OutboxEvent, User
from app.core.security import (
    can,
    check_client,
    check_request,
    has_request_permission,
    request_predicate,
    require_permission,
    task_predicate,
)
from app.core.service import advisory, audit, check_version, idem, lock, notify, page, plain, serialize
from app.crm.models import Counterparty, Nomenclature, Packing, QuoteItem, RequestItem, Seller
from app.crm.models import Request as CRMRequest

from . import cashflow
from .models import (
    CalendarBalance,
    CalendarEntry,
    EmployeeAbsence,
    SupplierMail,
    SupplierOrder,
    SupplierOrderLine,
    WorkflowReview,
)

router = APIRouter(tags=["Платёжный календарь и заказы поставщикам"])
TARIFFS = json.loads((Path(__file__).parent / "delivery_tariffs.json").read_text())


def managers(db):
    return [
        user
        for user in db.scalars(select(User).where(User.active.is_(True)))
        if can(db, user, "approvals.decide")
    ]


def local_date(value):
    return (
        value.replace(tzinfo=value.tzinfo or timezone.utc)
        .astimezone(ZoneInfo(settings.company_timezone))
        .date()
    )


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


def calendar_visible(db, user, row):
    if row.supplier_order_id:
        order = db.get(SupplierOrder, row.supplier_order_id)
        if not order:
            error("NOT_FOUND", "Заказ поставщику недоступен", 404)
        for line in order.snapshot["lines"]:
            check_request(db, user, line["request_id"])
    if row.request_id:
        check_request(db, user, row.request_id)
    elif not can(db, user, "approvals.decide") and row.author_id != user.id and row.responsible_id != user.id:
        error("NOT_FOUND", "Платёж недоступен", 404)


def calendar_view(db, row):
    result = serialize(row)
    party = db.get(Counterparty, row.counterparty_id) if row.counterparty_id else None
    result["counterparty_name"] = party.name if party else None
    review = db.scalar(select(WorkflowReview).where(WorkflowReview.kind == "calendar", WorkflowReview.entity_id == row.id)
                       .order_by(WorkflowReview.created_at.desc()).limit(1))
    result["review_id"] = review.id if review and review.status == "pending" else None
    result["review_version"] = review.version if review and review.status == "pending" else None
    result["decision_reason"] = review.reason if review else None
    return result


class CalendarIn(Command):
    direction: Literal["income", "expense"]
    planned_date: date
    amount: Positive
    currency: Currency = "RUB"
    purpose: str = Field(min_length=1, max_length=4000)
    counterparty_id: str | None = None
    request_id: str | None = None
    document_id: str | None = None
    supplier_order_id: str | None = None
    responsible_id: str | None = None
    payment_kind: Literal["prepayment", "deferred", "other"] = "other"
    recurrence: Literal["none", "weekly", "monthly"] = "none"
    outside_payment_days: bool = False


@router.get("/payment-calendar")
def calendar_list(
    db: DB, user: Actor, from_date: date | None = None, to_date: date | None = None,
    direction: str | None = None, status: str | None = None, counterparty_id: str | None = None,
    currency: str | None = None, payment_kind: str | None = None,
    date_basis: Literal["effective", "planned", "actual"] = "effective",
    page_number: int = Query(1, alias="page", ge=1), page_size: int = Query(50, ge=1, le=100),
):
    all_rows = db.scalars(cashflow.visible_query(db, user).order_by(CalendarEntry.planned_date, CalendarEntry.id)).all()
    dates = [row.actual_date or row.planned_date for row in all_rows]
    today = cashflow.company_today()
    end = to_date or max([today, *dates])
    start = from_date or max(min([today.replace(day=1), *dates]), end - timedelta(days=365))
    if end < start or (end - start).days > 365:
        error("CALENDAR_PERIOD", "Выберите период от 1 до 366 дней", field="to_date")
    rows = []
    for row in all_rows:
        day = row.planned_date if date_basis == "planned" else row.actual_date if date_basis == "actual" else (
            row.actual_date if row.status == "confirmed" else row.planned_date)
        if not day or not start <= day <= end:
            continue
        if any(value and getattr(row, key) != value for key, value in (
            ("direction", direction), ("status", status), ("counterparty_id", counterparty_id),
            ("currency", currency), ("payment_kind", payment_kind))):
            continue
        rows.append(row)
    totals, daily = cashflow.projection(db, user, all_rows, start, end, currency)
    parties = {}
    for row in all_rows:
        if row.status != "confirmed" or not row.actual_date or not start <= row.actual_date <= end or not row.counterparty_id:
            continue
        if currency and currency != row.currency:
            continue
        key = (row.counterparty_id, row.currency, row.direction)
        parties.setdefault(key, {"counterparty_name": calendar_view(db, row)["counterparty_name"],
                               "currency": row.currency, "direction": row.direction, "amount": Decimal(0)})["amount"] += row.amount
    for row in parties.values():
        denominator = next(item[row["direction"]] for item in totals if item["currency"] == row["currency"])
        row["share_percent"] = row["amount"] / denominator * 100 if denominator else Decimal(0)
    return {"items": [calendar_view(db, row) for row in rows[(page_number-1)*page_size:page_number*page_size]],
            "total": len(rows), "page": page_number, "page_size": page_size,
            "summary": plain(totals), "daily": plain(daily), "parties": plain(list(parties.values())),
            "from_date": start.isoformat(), "to_date": end.isoformat(),
            "global_balance": cashflow.global_balance_allowed(db, user), "payment_days": cashflow.policy(db)}


@router.get("/payment-calendar/rules")
def calendar_rules(db: DB, user: Actor):
    return cashflow.policy(db)


class PaymentDaysIn(VersionCommand):
    weekdays: list[Literal[0, 1, 2, 3, 4, 5, 6]] = Field(min_length=1, max_length=7)


@router.put("/payment-calendar/rules")
def calendar_rules_save(data: PaymentDaysIn, db: DB, user: Actor):
    require_permission(db, user, "approvals.decide")
    def operation():
        advisory(db, "setting:payment_days")
        row = cashflow.policy_row(db)
        if row:
            check_version(row, data.version)
        elif data.version != 1:
            error("VERSION_CONFLICT", "Платёжные дни изменены. Обновите страницу", 409)
        if len(set(data.weekdays)) != len(data.weekdays):
            error("PAYMENT_DAYS_INVALID", "Выберите каждый день один раз", field="weekdays")
        before = cashflow.policy(db)
        previous = row
        if previous:
            previous.status = "archived"
            previous.effective_until = cashflow.company_today()
        row = AppSetting(key="payment_days", value={"weekdays": sorted(data.weekdays)}, status="published",
                         version=data.version + 1, author_id=user.id,
                         previous_id=previous.id if previous else None, effective_from=cashflow.company_today())
        db.add(row)
        db.flush()
        audit(db, user, "setting", row.id, "payment_days", before=before, after=cashflow.policy(db))
        return cashflow.policy(db)
    return idem(db, user, data.idempotency_key, "calendar.rules", data.model_dump(mode="json"), operation)


class CalendarBalanceIn(Command):
    balance_date: date
    currency: Currency = "RUB"
    amount: Decimal = Field(max_digits=24, decimal_places=8)
    reason: str = Field(min_length=1, max_length=4000)
    version: int | None = Field(default=None, ge=1)


def require_global_calendar(db, user):
    require_permission(db, user, "approvals.decide")
    if not cashflow.global_balance_allowed(db, user):
        error("BALANCE_SCOPE_REQUIRED", "Сальдо организации доступно руководителю с доступом ко всем заявкам", 403)


@router.get("/payment-calendar/balances")
def calendar_balances(db: DB, user: Actor):
    require_global_calendar(db, user)
    return {"items": [serialize(row) for row in db.scalars(select(CalendarBalance).order_by(CalendarBalance.balance_date.desc()))]}


@router.put("/payment-calendar/balances")
def calendar_balance_save(data: CalendarBalanceIn, db: DB, user: Actor):
    require_global_calendar(db, user)
    if data.balance_date > cashflow.company_today():
        error("BALANCE_DATE_FUTURE", "Сальдо фиксируется на текущую или прошедшую дату", field="balance_date")
    def operation():
        advisory(db, f"calendar-balance:{data.currency}:{data.balance_date}")
        row = db.scalar(select(CalendarBalance).where(CalendarBalance.currency == data.currency,
                        CalendarBalance.balance_date == data.balance_date).with_for_update())
        before = serialize(row) if row else None
        if row:
            if data.version is None:
                error("VERSION_REQUIRED", "Сальдо на эту дату уже существует. Откройте его для изменения", 409, field="version")
            check_version(row, data.version)
            row.amount, row.reason, row.author_id = data.amount, data.reason, user.id
            row.version += 1
        else:
            row = CalendarBalance(**data.model_dump(exclude={"idempotency_key", "version"}), author_id=user.id)
            db.add(row)
        db.flush()
        audit(db, user, "calendar_balance", row.id, "saved", before=before, after=serialize(row), reason=data.reason)
        return serialize(row)
    return idem(db, user, data.idempotency_key, "calendar.balance", data.model_dump(mode="json"), operation)


@router.get("/payment-calendar/export")
def calendar_export(on_date: date, db: DB, user: Actor, date_basis: Literal["planned", "actual", "effective"] = "effective"):
    require_permission(db, user, "exports.download")
    rows = db.scalars(cashflow.visible_query(db, user).order_by(CalendarEntry.planned_date, CalendarEntry.id)).all()
    selected = []
    for row in rows:
        day = row.planned_date if date_basis == "planned" else row.actual_date if date_basis == "actual" else (
            row.actual_date if row.status == "confirmed" else row.planned_date)
        if day == on_date:
            selected.append(row)
    statuses = {"draft": "Черновик", "pending": "На согласовании", "approved": "Согласовано",
                "confirmed": "Подтверждено", "rejected": "Отклонено", "cancelled": "Отменено"}
    content = workbook(["Контрагент", "Сумма", "Плановая дата", "Фактическая дата", "Валюта", "Тип", "Статус", "Назначение"],
        [[calendar_view(db, row)["counterparty_name"] or "—", row.amount, row.planned_date, row.actual_date,
          row.currency, "Приход" if row.direction == "income" else "Расход", statuses.get(row.status, row.status), row.purpose]
         for row in selected], f"Реестр {on_date}")
    from io import BytesIO

    from openpyxl import load_workbook
    from openpyxl.styles import PatternFill
    book = load_workbook(BytesIO(content))
    for index, row in enumerate(selected, 2):
        book.active.cell(index, 2).number_format = '#,##0.00########'
        for column in (3, 4):
            book.active.cell(index, column).number_format = 'DD.MM.YYYY'
        if row.status == "rejected":
            for cell in book.active[index]:
                cell.fill = PatternFill("solid", fgColor="FFF1F3")
    output = BytesIO()
    book.save(output)
    content = output.getvalue()
    audit(db, user, "calendar_export", on_date.isoformat(), "export", after={"rows": len(selected), "date_basis": date_basis})
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="payments-{on_date}.xlsx"', "Cache-Control": "private, no-store"})


@router.post("/payment-calendar")
def calendar_create(data: CalendarIn, db: DB, user: Actor):
    def operation():
        cashflow.validate_day(db, data.direction, data.planned_date, data.outside_payment_days, "planned_date")
        if data.request_id:
            check_request(db, user, data.request_id)
        if data.counterparty_id:
            check_client(db, user, data.counterparty_id)
        if data.document_id:
            doc = db.get(CommercialDocument, data.document_id)
            if not doc or doc.request_id != data.request_id or data.direction != "income":
                error("CALENDAR_SOURCE", "Документ не соответствует приходу по заявке")
        if data.supplier_order_id:
            require_permission(db, user, "procurement.write")
            order = db.get(SupplierOrder, data.supplier_order_id)
            if not order or data.direction != "expense":
                error("CALENDAR_SOURCE", "Заказ поставщику не соответствует расходу")
            for line in order.snapshot["lines"]:
                check_request(db, user, line["request_id"])
        responsible = data.responsible_id or user.id
        ensure_available(db, responsible)
        row = CalendarEntry(
            **data.model_dump(exclude={"idempotency_key", "responsible_id"}),
            responsible_id=responsible,
            author_id=user.id,
        )
        db.add(row)
        db.flush()
        audit(db, user, "calendar_entry", row.id, "created", after=serialize(row))
        return calendar_view(db, row)

    return idem(db, user, data.idempotency_key, "calendar.create", data.model_dump(mode="json"), operation)


class CalendarPatch(CalendarIn):
    version: int


@router.patch("/payment-calendar/{entry_id}")
def calendar_edit(entry_id: str, data: CalendarPatch, db: DB, user: Actor):
    def operation():
        row = lock(db, CalendarEntry, entry_id)
        calendar_visible(db, user, row)
        check_version(row, data.version)
        if row.status not in ("draft", "rejected"):
            error("CALENDAR_STATE", "Изменять можно только черновик или отклонённый платёж")
        cashflow.validate_day(db, data.direction, data.planned_date, data.outside_payment_days, "planned_date")
        # Source links are immutable; validate the changed counterparty and responsible person.
        if data.counterparty_id:
            check_client(db, user, data.counterparty_id)
        responsible = data.responsible_id or row.responsible_id
        ensure_available(db, responsible)
        before = serialize(row)
        for key in (
            "direction",
            "planned_date",
            "amount",
            "currency",
            "purpose",
            "counterparty_id",
            "payment_kind",
            "recurrence",
            "outside_payment_days",
        ):
            setattr(row, key, getattr(data, key))
        if (row.document_id and row.direction != "income") or (
            row.supplier_order_id and row.direction != "expense"
        ):
            error("CALENDAR_SOURCE", "Направление платежа не соответствует исходному документу")
        row.responsible_id, row.status, row.version = responsible, "draft", row.version + 1
        audit(db, user, "calendar_entry", row.id, "updated", before, serialize(row))
        return calendar_view(db, row)

    return idem(
        db, user, data.idempotency_key, f"calendar.edit:{entry_id}", data.model_dump(mode="json"), operation
    )


@router.post("/payment-calendar/{entry_id}/submit")
def calendar_submit(entry_id: str, data: VersionCommand, db: DB, user: Actor):
    def operation():
        row = lock(db, CalendarEntry, entry_id)
        calendar_visible(db, user, row)
        check_version(row, data.version)
        if row.status not in ("draft", "rejected"):
            error("CALENDAR_STATE", "На согласование можно отправить черновик или отклонённый платёж")
        cashflow.validate_day(db, row.direction, row.planned_date, row.outside_payment_days, "planned_date")
        cashflow.validate_invoice(db, row)
        row.status = "pending"
        row.version += 1
        submit_review(db, user, "calendar", row, f"Платёж: {row.purpose}", serialize(row), row.request_id)
        return calendar_view(db, row)

    return idem(
        db, user, data.idempotency_key, f"calendar.submit:{entry_id}", data.model_dump(mode="json"), operation
    )


class CalendarConfirm(VersionCommand):
    actual_date: date | None = None
    outside_payment_days: bool = False


@router.post("/payment-calendar/{entry_id}/confirm")
def calendar_confirm(entry_id: str, data: CalendarConfirm, db: DB, user: Actor):
    require_permission(db, user, "approvals.decide")

    def operation():
        row = lock(db, CalendarEntry, entry_id)
        calendar_visible(db, user, row)
        check_version(row, data.version)
        if row.status != "approved":
            error("CALENDAR_APPROVAL_REQUIRED", "Сначала согласуйте платёж")
        cashflow.confirm(db, user, row, data.actual_date, data.outside_payment_days or row.outside_payment_days)
        return calendar_view(db, row)

    return idem(
        db,
        user,
        data.idempotency_key,
        f"calendar.confirm:{entry_id}",
        data.model_dump(mode="json"),
        operation,
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


@router.get("/delivery-tariffs")
def delivery_tariffs(db: DB, user: Actor, q: str = ""):
    require_permission(db, user, "catalog.read")
    rows = [
        {"id": row["city"], "name": row["city"], **row}
        for row in TARIFFS
        if q.casefold() in row["city"].casefold()
    ]
    return {"items": rows, "total": len(rows), "page": 1, "page_size": 100}


def delivery_expense(db, request, chosen_city, required):
    if not required:
        return None
    client = db.get(Counterparty, request.client_id)
    details = client.details or {}
    address = str(
        details.get("Юридический адрес") or details.get("legal_address") or details.get("city") or ""
    )
    city = chosen_city or next(
        (
            row["city"]
            for row in sorted(TARIFFS, key=lambda r: -len(r["city"]))
            if re.search(r"(?<!\w)" + re.escape(row["city"]) + r"(?!\w)", address, re.IGNORECASE)
        ),
        None,
    )
    tariff = next((row for row in TARIFFS if row["city"] == city), None)
    if not tariff:
        error(
            "DELIVERY_CITY_REQUIRED",
            "Выберите город доставки или отметьте «Доставка не требуется»",
            field="delivery_city",
        )
    return {
        "name": f"Доставка СДЭК до 15 кг: {city}",
        "amount": tariff["amount"],
        "currency": "RUB",
        "method": "BY_QUANTITY",
        "scope": "REQUEST",
        "stage": "CLIENT_DELIVERY",
        "calculation_type": "FIXED",
        "include_in_cost": True,
        "include_in_cash": True,
        "basis": "ТМ-35, тариф без НДС из реестра СДЭК",
    }


def order_candidate(db, ex):
    from app.commerce.models import Product, Quote

    if ex.quote_item_id:
        quote = db.get(QuoteItem, ex.quote_item_id)
        n = db.get(Nomenclature, quote.nomenclature_id)
        packing = db.get(Packing, quote.packing_id)
        from app.crm.models import Currency as CurrencyModel

        currency = db.get(CurrencyModel, quote.currency_id).code
        product = {
            "name": n.name,
            "manufacturer": n.manufacturer,
            "article": n.article,
            "packaging": packing.display_name,
        }
        price, days = quote.unit_price, quote.delivery_days
    else:
        quote = db.get(Quote, ex.quote_id)
        product = product_view(db, db.get(Product, quote.product_id))
        currency, price, days = quote.currency, quote.price, quote.terms.get("delivery_days")
        from app.commerce.calculator import convert

        price *= convert(Decimal("1"), ex.unit, quote.price_unit)
    party = db.get(Counterparty, quote.supplier_id)
    return {
        "id": ex.id,
        "execution_id": ex.id,
        "request_id": ex.request_id,
        "supplier_id": party.id,
        "supplier_name": party.name,
        "contract": (party.details or {}).get("contract", ""),
        "payment_terms": (party.details or {}).get("payment_terms", ""),
        "delivery_terms": (party.details or {}).get("delivery_terms", ""),
        "currency": currency,
        "quantity": str(ex.quantity - ex.cancelled_quantity),
        "unit_price": str(price),
        "delivery_days": days,
        **product,
    }


@router.get("/supplier-orders/positions")
def order_positions(
    db: DB, user: Actor, ordered: bool = False, supplier_id: str | None = None, status: str | None = None
):
    require_permission(db, user, "procurement.write")
    query = select(Execution).where(
        Execution.request_id.in_(select(CRMRequest.id).where(request_predicate(db, user)))
    )
    if not ordered:
        query = query.where(Execution.quantity > Execution.cancelled_quantity, Execution.procurement_at.is_not(None))
    items = []
    for ex in db.scalars(query.order_by(Execution.created_at)):
        line = db.scalar(select(SupplierOrderLine).where(SupplierOrderLine.execution_id == ex.id))
        if ordered != bool(line):
            continue
        item = {
            **(line.snapshot if line else order_candidate(db, ex)),
            **(
                {
                    "id": line.id,
                    "status": line.status,
                    "version": line.version,
                    "order_id": line.order_id,
                    "order_number": db.get(SupplierOrder, line.order_id).number,
                }
                if line
                else {}
            ),
        }
        if (not supplier_id or item["supplier_id"] == supplier_id) and (
            not status or item.get("status") == status
        ):
            items.append(item)
    return {"items": items, "total": len(items), "page": 1, "page_size": len(items)}


class OrderIn(Command):
    execution_ids: list[str] = Field(min_length=1, max_length=500)
    seller_id: str
    expected_date: date
    contract: str = Field(default="", max_length=2000)
    payment_terms: str = Field(default="", max_length=4000)
    delivery_terms: str = Field(default="", max_length=2000)
    prices: dict[str, str] = Field(default_factory=dict)


@router.post("/supplier-orders")
def order_create(data: OrderIn, db: DB, user: Actor):
    require_permission(db, user, "procurement.write")

    def operation():
        advisory(db, "supplier-orders.create")
        if len(set(data.execution_ids)) != len(data.execution_ids):
            error("DUPLICATE_SELECTION", "Позиция выбрана повторно")
        rows = []
        for entity_id in data.execution_ids:
            ex = lock(db, Execution, entity_id)
            check_request(db, user, ex.request_id)
            if ex.quantity <= ex.cancelled_quantity or db.scalar(
                select(SupplierOrderLine.id).where(SupplierOrderLine.execution_id == ex.id)
            ):
                error("ORDER_POSITION_USED", "Позиция отменена или уже заказана", 409)
            if ex.procurement_at is None:
                error("PROCUREMENT_HANDOFF_REQUIRED", "Сначала согласуйте и передайте позицию в закупки")
            row = order_candidate(db, ex)
            row["unit_price"] = str(dec(data.prices.get(entity_id, row["unit_price"])))
            if dec(row["unit_price"]) < 0:
                error("ORDER_PRICE", "Закупочная цена не может быть отрицательной")
            rows.append(row)
        if len({(r["supplier_id"], r["currency"]) for r in rows}) != 1:
            error("ORDER_SUPPLIER", "Выберите позиции одного поставщика в одной валюте")
        seller = db.get(Seller, data.seller_id)
        if not seller or seller.archived:
            error("SELLER_REQUIRED", "Выберите действующую организацию покупателя")
        party = supplier(db, rows[0]["supplier_id"])
        from app.crm.models import NumberCounter

        prefix = re.sub(r"[^\w]", "", party.name)[:5].upper() or "PO"
        counter = db.get(NumberCounter, "supplier-order:" + party.id)
        if not counter:
            counter = NumberCounter(key="supplier-order:" + party.id, value=0)
            db.add(counter)
        counter.value += 1
        number = f"{prefix}{party.internal_code}-{counter.value:06d}"
        total = sum(dec(r["quantity"]) * dec(r["unit_price"]) for r in rows)
        order = SupplierOrder(
            number=number,
            supplier_id=party.id,
            seller_id=seller.id,
            currency=rows[0]["currency"],
            total=total,
            expected_date=data.expected_date,
            contract=data.contract,
            payment_terms=data.payment_terms,
            delivery_terms=data.delivery_terms,
            author_id=user.id,
            snapshot={"supplier": serialize(party), "customer": serialize(seller), "lines": rows},
        )
        db.add(order)
        db.flush()
        for row in rows:
            db.add(
                SupplierOrderLine(
                    order_id=order.id,
                    execution_id=row["execution_id"],
                    quantity=dec(row["quantity"]),
                    unit_price=dec(row["unit_price"]),
                    snapshot=row,
                )
            )
        audit(db, user, "supplier_order", order.id, "created", after=serialize(order))
        return serialize(order)

    return idem(
        db, user, data.idempotency_key, "supplier-order.create", data.model_dump(mode="json"), operation
    )


@router.get("/supplier-orders")
def order_list(db: DB, user: Actor, supplier_id: str | None = None, status: str | None = None):
    require_permission(db, user, "procurement.write")
    rows = db.scalars(select(SupplierOrder).order_by(SupplierOrder.created_at.desc())).all()
    items = []
    for order in rows:
        if supplier_id and order.supplier_id != supplier_id:
            continue
        if any(
            not has_request_permission(db, user, line["request_id"], "requests.read")
            for line in order.snapshot["lines"]
        ):
            continue
        lines = db.scalars(select(SupplierOrderLine).where(SupplierOrderLine.order_id == order.id)).all()
        state = (
            "delivered"
            if all(line.status == "delivered" for line in lines)
            else "delivery"
            if any(line.status == "delivery" for line in lines)
            else "waiting"
        )
        if status and state != status:
            continue
        items.append(
            {**serialize(order), "supplier_name": order.snapshot["supplier"]["name"], "status": state}
        )
    return {"items": items, "total": len(items), "page": 1, "page_size": len(items)}


class OrderLineStatus(VersionCommand):
    status: Literal["waiting", "delivery", "delivered"]


@router.patch("/supplier-order-lines/{line_id}")
def order_line_status(line_id: str, data: OrderLineStatus, db: DB, user: Actor):
    require_permission(db, user, "procurement.write")

    def operation():
        row = lock(db, SupplierOrderLine, line_id)
        check_request(db, user, db.get(Execution, row.execution_id).request_id)
        check_version(row, data.version)
        before = serialize(row)
        row.status = data.status
        row.version += 1
        audit(db, user, "supplier_order_line", row.id, "status", before, serialize(row))
        return serialize(row)

    return idem(
        db, user, data.idempotency_key, f"order-line:{line_id}", data.model_dump(mode="json"), operation
    )


@router.get("/supplier-orders/positions/export.xlsx")
def positions_export(db: DB, user: Actor, supplier_id: str | None = None, status: str | None = None):
    rows = order_positions(db, user, True, supplier_id, status)["items"]
    return Response(
        workbook(
            ["Order", "Name", "Manufacturer", "Article", "Packing", "Qty", "Status"],
            [
                [
                    r.get("order_number"),
                    r["name"],
                    r.get("manufacturer"),
                    r.get("article"),
                    r.get("packaging"),
                    r["quantity"],
                    r["status"],
                ]
                for r in rows
            ],
            "Ordered positions",
        ),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/supplier-orders/{order_id}/po.xlsx")
def order_po(order_id: str, db: DB, user: Actor):
    require_permission(db, user, "procurement.write")
    order = db.get(SupplierOrder, order_id)
    if not order:
        error("NOT_FOUND", "Заказ не найден", 404)
    for row in order.snapshot["lines"]:
        check_request(db, user, row["request_id"])
    from .purchase_order import purchase_order

    return Response(
        purchase_order(order), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@router.get("/requests/{request_id}/supplier-mail/items")
def mail_items(request_id: str, db: DB, user: Actor):
    check_request(db, user, request_id, "quotes.write")
    ids = list(
        db.scalars(
            select(RequestItem.id)
            .where(
                RequestItem.request_id == request_id,
                RequestItem.archived.is_(False),
                RequestItem.quote_only.is_(False),
            )
            .order_by(RequestItem.created_at, RequestItem.id)
        )
    )
    return {"items": [{"id": entity_id} for entity_id in ids], "total": len(ids)}


class MailPreviewIn(Command):
    supplier_ids: list[str] = Field(min_length=1, max_length=100)
    item_ids: list[str] = Field(min_length=1, max_length=10000)
    introduction: str = Field(default="Dear Colleagues,\n\nPlease send us offer for:", max_length=4000)
    cc: list[EmailStr] = Field(default_factory=list, max_length=100)


@router.post("/requests/{request_id}/supplier-mail/preview")
def mail_preview(request_id: str, data: MailPreviewIn, db: DB, user: Actor):
    request = check_request(db, user, request_id, "quotes.write")
    items = db.scalars(select(RequestItem).where(
        RequestItem.id.in_(set(data.item_ids)), RequestItem.request_id == request.id,
        RequestItem.archived.is_(False)).order_by(RequestItem.created_at, RequestItem.id)).all()
    if len(items) != len(set(data.item_ids)):
        error("ITEM_NOT_FOUND", "Выберите действующие позиции этой заявки")
    headers, lines = rfq_table(db, items)
    table = '<table border="1" cellpadding="6"><tr>' + "".join(f"<th>{escape(h)}</th>" for h in headers) + "</tr>"
    table += (
        "".join("<tr>" + "".join(f"<td>{escape(str(c) if c is not None else '')}</td>" for c in row) + "</tr>" for row in lines)
        + "</table>"
    )
    body = (
        data.introduction
        + "\n\n"
        + "\t".join(headers)
        + "\n"
        + "\n".join("\t".join(str(c) if c is not None else "" for c in row) for row in lines)
    )
    drafts = []
    for supplier_id in dict.fromkeys(data.supplier_ids):
        party = supplier(db, supplier_id)
        email = (party.details or {}).get("rfq_email") or party.email
        if not email:
            error("SUPPLIER_EMAIL", f"Укажите почту для запросов в карточке {party.name}")
        try:
            email = str(TypeAdapter(EmailStr).validate_python(email))
        except ValidationError:
            error("SUPPLIER_EMAIL", f"Исправьте почту для запросов в карточке {party.name}")
        drafts.append(
            {
                "supplier_id": party.id,
                "supplier_name": party.name,
                "recipient": email,
                "cc": list(dict.fromkeys(str(address) for address in data.cc if str(address).casefold() != email.casefold())),
                "subject": f"Request {request.number}",
                "body": body,
                "table_html": table,
                "introduction": data.introduction,
            }
        )
    return {"items": drafts}


class MailSendIn(MailPreviewIn):
    subject: str = Field(min_length=1, max_length=250)


@router.post("/requests/{request_id}/supplier-mail/send")
def mail_send(request_id: str, data: MailSendIn, db: DB, user: Actor):
    if not settings.smtp_host or not settings.smtp_from:
        error("SMTP_REQUIRED", "Настройте SMTP_HOST и SMTP_FROM на сервере для отправки писем")

    def operation():
        drafts = mail_preview(request_id, data, db, user)["items"]
        result = []
        for draft in drafts:
            mail = SupplierMail(
                request_id=request_id,
                supplier_id=draft["supplier_id"],
                recipient=draft["recipient"],
                cc=draft["cc"],
                subject=data.subject,
                body=draft["body"],
                html_body="<p>"
                + escape(data.introduction).replace("\n", "<br>")
                + "</p>"
                + draft["table_html"],
                author_id=user.id,
            )
            db.add(mail)
            db.flush()
            db.add(
                OutboxEvent(
                    event_key=f"supplier-mail:{mail.id}", kind="supplier.mail", payload={"mail_id": mail.id}
                )
            )
            audit(
                db,
                user,
                "supplier_mail",
                mail.id,
                "queued",
                after={"recipient": mail.recipient, "cc": mail.cc, "request_id": request_id},
            )
            result.append(serialize(mail))
        return {"items": result}

    return idem(
        db, user, data.idempotency_key, f"supplier-mail:{request_id}", data.model_dump(mode="json"), operation
    )


@router.get("/requests/{request_id}/supplier-mail")
def mail_list(request_id: str, db: DB, user: Actor):
    check_request(db, user, request_id)
    require_permission(db, user, "quotes.write")
    return page(
        db,
        select(SupplierMail)
        .where(SupplierMail.request_id == request_id)
        .order_by(SupplierMail.created_at.desc()),
        1,
        100,
    )


@router.get("/analytics/sales-managers")
def manager_report(db: DB, user: Actor, from_date: date, to_date: date):
    require_permission(db, user, "analytics.read")
    require_permission(db, user, "finance.profit.read")
    start = datetime.combine(from_date, datetime.min.time(), ZoneInfo(settings.company_timezone))
    end = datetime.combine(
        to_date + timedelta(days=1), datetime.min.time(), ZoneInfo(settings.company_timezone)
    )
    requests = db.scalars(
        select(CRMRequest).where(
            request_predicate(db, user),
            CRMRequest.is_test.is_(False),
            CRMRequest.sale_confirmed_at >= start,
            CRMRequest.sale_confirmed_at < end,
        )
    ).all()
    totals = {}
    for request in requests:
        for ex in db.scalars(select(Execution).where(Execution.request_id == request.id)):
            if ex.quantity <= ex.cancelled_quantity:
                continue
            doc = db.get(CommercialDocument, ex.proposal_id)
            calc = db.get(Calculation, doc.calculation_id)
            source = next(line for line in calc.snapshot["lines"] if line["line_id"] == ex.line_id)
            ratio = (ex.quantity - ex.cancelled_quantity) / dec(source["quantity"])
            key = (request.owner_id, doc.currency)
            totals.setdefault(
                key,
                {
                    "id": request.owner_id + doc.currency,
                    "manager": db.get(User, request.owner_id).name,
                    "currency": doc.currency,
                    "sales": Decimal(0),
                    "sale_net": Decimal(0),
                    "cost": Decimal(0),
                },
            )
            row = totals[key]
            row["sales"] += dec(source["total"]) * ratio
            row["sale_net"] += dec(source["net"]) * ratio
            cost = dec(source.get("detail", {}).get("cost", "0")) * ratio
            if calc.snapshot.get("management_currency", doc.currency) != doc.currency:
                rate = next(
                    (
                        dec(r.get("management_per_unit", r.get("rate")))
                        for r in calc.snapshot.get("rates", [])
                        if r["currency"] == doc.currency
                    ),
                    None,
                )
                if not rate:
                    error(
                        "REPORT_RATE_REQUIRED", "В старом расчёте отсутствует курс валюты продажи для отчёта"
                    )
                cost /= rate
            row["cost"] += cost
    for row in totals.values():
        row["gross_profit"] = row["sale_net"] - row["cost"]
        row.update(profitability_metrics(row["gross_profit"], row["sale_net"], row["cost"]))
    return {
        "items": plain(list(totals.values())),
        "total": len(totals),
        "basis": "Принятые позиции продаж, подтверждённых за период; без НДС и тестовых заявок",
    }
