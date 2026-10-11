"""Sales manager report."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from sqlalchemy import select

from app.commerce.calculator import dec, profitability_metrics
from app.commerce.models import Calculation, CommercialDocument, Execution
from app.commerce.procurement import DB, Actor
from app.core.config import settings
from app.core.errors import error
from app.core.models import User
from app.core.security import request_predicate, require_permission
from app.core.service import plain
from app.crm.models import Request as CRMRequest

router = APIRouter(tags=["Платёжный календарь и заказы поставщикам"])


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
