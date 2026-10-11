"""Company calendar date, approvers and payment visibility, shared by the business endpoints."""

from datetime import timezone
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.errors import error
from app.core.security import can, check_request, users_with_permission

from .models import SupplierOrder


def managers(db):
    return users_with_permission(db, "approvals.decide")


def local_date(value):
    return (
        value.replace(tzinfo=value.tzinfo or timezone.utc)
        .astimezone(ZoneInfo(settings.company_timezone))
        .date()
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
