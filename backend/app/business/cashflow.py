"""Payment policy and cash projections; plans never change the actual ledger."""

import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from app.communication.models import FileRecord
from app.core.config import settings
from app.core.db import utcnow
from app.core.errors import error
from app.core.models import AppSetting
from app.core.security import can, request_predicate, scope_for
from app.core.service import audit, serialize
from app.crm.models import Request

from .models import CalendarBalance, CalendarEntry, SupplierOrderLine


def company_today():
    return datetime.now(ZoneInfo(settings.company_timezone)).date()


def visible_query(db, user):
    from app.commerce.models import Execution
    allowed = select(Request.id).where(request_predicate(db, user))
    blocked = select(SupplierOrderLine.order_id).join(Execution, Execution.id == SupplierOrderLine.execution_id).where(
        Execution.request_id.not_in(allowed))
    return select(CalendarEntry).where(
        or_(CalendarEntry.request_id.in_(allowed), CalendarEntry.request_id.is_(None) & (
            True if can(db, user, "approvals.decide") else
            or_(CalendarEntry.author_id == user.id, CalendarEntry.responsible_id == user.id))),
        or_(CalendarEntry.supplier_order_id.is_(None), CalendarEntry.supplier_order_id.not_in(blocked)))


def policy_row(db):
    return db.scalar(select(AppSetting).where(AppSetting.key == "payment_days", AppSetting.status == "published")
                     .order_by(AppSetting.created_at.desc()).limit(1))


def policy(db):
    row = policy_row(db)
    return {"weekdays": (row.value.get("weekdays", [1, 3]) if row else [1, 3]), "version": row.version if row else 1}


def validate_day(db, direction, day, exception, field):
    if direction == "expense" and day.weekday() not in policy(db)["weekdays"] and not exception:
        error("PAYMENT_DAY_REQUIRED", "Выберите платёжный день или отметьте «Платёж вне платёжных дней»", field=field)


def validate_invoice(db, entry, *, confirming=False):
    files = db.scalars(select(FileRecord).where(FileRecord.calendar_entry_id == entry.id)).all()
    if not confirming and entry.direction == "expense" and not any(f.status in ("clean", "quarantined") for f in files):
        error("PAYMENT_INVOICE_REQUIRED", "Загрузите счёт перед отправкой платежа руководителю")
    if confirming and files and not any(f.status == "clean" for f in files):
        error("PAYMENT_INVOICE_NOT_READY", "Дождитесь успешной проверки счёта или загрузите исправный файл")


def confirm(db, user, row, actual_date, outside_payment_days):
    if not actual_date:
        error("ACTUAL_DATE_REQUIRED", "Укажите фактическую дату оплаты", field="actual_date")
    if actual_date > company_today():
        error("ACTUAL_DATE_FUTURE", "Фактическая дата оплаты не может быть в будущем", field="actual_date")
    validate_day(db, row.direction, actual_date, outside_payment_days, "actual_date")
    validate_invoice(db, row, confirming=True)
    before = serialize(row)
    row.actual_date = actual_date
    row.outside_payment_days = row.outside_payment_days or outside_payment_days
    row.status, row.confirmed_by, row.confirmed_at = "confirmed", user.id, utcnow()
    row.version += 1
    if row.recurrence != "none":
        planned = row.planned_date + timedelta(days=7)
        if row.recurrence == "monthly":
            year = row.planned_date.year + (row.planned_date.month == 12)
            month = row.planned_date.month % 12 + 1
            planned = date(year, month, min(row.planned_date.day, calendar.monthrange(year, month)[1]))
        if row.direction == "expense":
            while planned.weekday() not in policy(db)["weekdays"]:
                planned += timedelta(days=1)
        fields = {key: getattr(row, key) for key in (
            "direction", "amount", "currency", "purpose", "counterparty_id", "request_id", "supplier_order_id",
            "document_id", "payment_kind", "recurrence", "responsible_id", "author_id")}
        db.add(CalendarEntry(**fields, planned_date=planned))
    audit(db, user, "calendar_entry", row.id, "confirmed", before=before, after=serialize(row))


def global_balance_allowed(db, user):
    return can(db, user, "approvals.decide") and scope_for(db, user, "requests.read") == "all"


def projection(db, user, rows, start, end, selected_currency=None):
    actual = [r for r in rows if r.status == "confirmed" and r.actual_date]
    balances = db.scalars(select(CalendarBalance).order_by(CalendarBalance.balance_date)).all() if global_balance_allowed(db, user) else []
    currencies = sorted({r.currency for r in rows} | {r.currency for r in balances} | {selected_currency or "RUB"})
    summary, daily = [], []
    for currency in currencies:
        if selected_currency and currency != selected_currency:
            continue
        facts = [r for r in actual if r.currency == currency]
        anchors = [r for r in balances if r.currency == currency]
        def balance_at(day, *, before=False):
            valid = [r for r in anchors if r.balance_date <= day]
            anchor = valid[-1] if valid else None
            value = anchor.amount if anchor else Decimal(0)
            for r in facts:
                if (not anchor or r.actual_date >= anchor.balance_date) and (r.actual_date < day if before else r.actual_date <= day):
                    value += r.amount if r.direction == "income" else -r.amount
            return value, anchor
        opening, anchor = balance_at(start, before=True)
        closing, closing_anchor = balance_at(end)
        current, current_anchor = balance_at(company_today())
        item = {"currency": currency, "income": Decimal(0), "expense": Decimal(0),
                "planned_income": Decimal(0), "planned_expense": Decimal(0), "prepayment_count": 0, "deferred_count": 0,
                "opening_balance": opening if anchor else None, "closing_balance": closing if closing_anchor else None,
                "current_balance": current if current_anchor else None,
                "balance_date": closing_anchor.balance_date if closing_anchor else None}
        days = defaultdict(lambda: {"income": Decimal(0), "expense": Decimal(0), "planned_income": Decimal(0), "planned_expense": Decimal(0)})
        for r in rows:
            if r.currency != currency:
                continue
            if r.status == "confirmed" and r.actual_date and start <= r.actual_date <= end:
                item[r.direction] += r.amount
                days[r.actual_date][r.direction] += r.amount
                if r.direction == "income" and r.payment_kind in ("prepayment", "deferred"):
                    item[r.payment_kind + "_count"] += 1
            elif r.status in ("pending", "approved") and start <= r.planned_date <= end:
                item["planned_" + r.direction] += r.amount
                days[r.planned_date]["planned_" + r.direction] += r.amount
        item["balance"] = item["income"] - item["expense"]
        summary.append(item)
        day = start
        while day <= end:
            value, day_anchor = balance_at(day)
            daily.append({"date": day.isoformat(), "currency": currency, **days[day],
                          "balance": value if day_anchor else None})
            day += timedelta(days=1)
    return summary, daily
