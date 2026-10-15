"""Requests and orders by manufacturer or umbrella brand."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.models import User
from app.core.security import current_user, request_predicate, require_permission
from app.core.service import plain, prefetch
from app.crm.models import Nomenclature, QuoteItem, Request, RequestItem

router = APIRouter(tags=["Аналитика"])
# Request tables name the producer in a column with one of these headings.
PRODUCER_HEADINGS = {"producer", "produce", "manufacturer", "brand", "производитель", "бренд", "изготовитель"}


def item_manufacturers(db: Session, items: list[RequestItem]) -> dict[str, str | None]:
    """The registry entry of each position: its nomenclature's, the producer of the request table, or the one
    every supplier quoted. Positions without one map to None."""
    from app.commerce.models import Manufacturer
    from app.crm.manufacturers import current, find_manufacturer

    nomenclatures = prefetch(db, Nomenclature, (item.nomenclature_id for item in items))
    quoted: dict[str, set[str]] = defaultdict(set)
    for item_id, nomenclature_id in db.execute(select(QuoteItem.source_request_item_id, QuoteItem.nomenclature_id)
                                               .where(QuoteItem.source_request_item_id.in_([i.id for i in items]),
                                                      QuoteItem.archived.is_(False))):
        quoted[item_id].add(nomenclature_id)
    quoted_nomenclatures = prefetch(db, Nomenclature, (value for values in quoted.values() for value in values))
    makers = prefetch(db, Manufacturer, [*(row.manufacturer_id for row in nomenclatures.values()),
                                         *(row.manufacturer_id for row in quoted_nomenclatures.values())])
    found: dict[str, str | None] = {}
    for item in items:
        nomenclature = nomenclatures.get(item.nomenclature_id) if item.nomenclature_id else None
        maker_id = nomenclature.manufacturer_id if nomenclature else None
        if not maker_id:
            producer = next((value for column, value in zip(item.source_columns, item.source_values, strict=False)
                             if column.strip().casefold() in PRODUCER_HEADINGS and value), None)
            maker = find_manufacturer(db, producer) if producer else None
            maker_id = maker.id if maker else None
        if not maker_id:
            offered = {quoted_nomenclatures[value].manufacturer_id for value in quoted[item.id]
                       if value in quoted_nomenclatures}
            maker_id = offered.pop() if len(offered) == 1 else None
        if maker_id and maker_id in makers:
            maker_id = current(db, makers[maker_id]).id
        found[item.id] = maker_id
    return found


@router.get("/analytics/manufacturers")
def manufacturer_report(
    date_from: date | None = None,
    date_to: date | None = None,
    by: Literal["manufacturer", "umbrella"] = "manufacturer",
    include_test: bool = False,
    user: User = Depends(current_user),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    """Positions of requests created in the period, their supplier prices and accepted orders, by manufacturer."""
    from app.commerce.models import Execution, Manufacturer

    require_permission(db, user, "analytics.reports")
    zone = ZoneInfo(settings.company_timezone)
    start = datetime.combine(date_from or date.today() - timedelta(days=30), time.min, zone).astimezone(timezone.utc)
    finish = datetime.combine((date_to or date.today()) + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
    requests = select(Request.id).where(request_predicate(db, user), Request.created_at >= start,
                                        Request.created_at < finish, Request.archived.is_(False))
    if not include_test:
        requests = requests.where(Request.is_test.is_(False))
    items = list(db.scalars(select(RequestItem).where(RequestItem.request_id.in_(requests),
                                                      RequestItem.archived.is_(False))))
    makers = item_manufacturers(db, items)
    entries = prefetch(db, Manufacturer, makers.values())
    umbrellas = prefetch(db, Manufacturer, (row.umbrella_id for row in entries.values()))
    entries.update(umbrellas)
    quoted = set(db.scalars(select(QuoteItem.source_request_item_id).where(
        QuoteItem.source_request_item_id.in_([item.id for item in items]), QuoteItem.archived.is_(False))))
    ordered: dict[str, Decimal] = defaultdict(Decimal)
    sales: dict[str, Decimal] = defaultdict(Decimal)
    for execution in db.scalars(select(Execution).where(Execution.item_id.in_([item.id for item in items]))):
        rest = execution.quantity - execution.cancelled_quantity
        if rest > 0:
            ordered[execution.item_id] += rest
            sales[execution.item_id] += Decimal(str(execution.snapshot.get("total", "0"))) * rest / execution.quantity

    def key_of(maker_id: str | None) -> str | None:
        if maker_id and by == "umbrella":
            return entries[maker_id].umbrella_id or maker_id
        return maker_id

    rows: dict[str | None, dict[str, Any]] = {}
    for item in items:
        key = key_of(makers[item.id])
        maker = entries.get(key) if key else None
        row = rows.setdefault(key, {
            "id": key or "none", "manufacturer_id": key, "internal_code": maker.internal_code if maker else None,
            "name": maker.name if maker else "Производитель не указан",
            "umbrella_name": (entries[maker.umbrella_id].name if maker and maker.umbrella_id else None),
            "requests": set(), "positions": 0, "quoted_positions": 0, "ordered_positions": 0,
            "ordered_quantity": Decimal("0"), "ordered_sales": Decimal("0"),
        })
        row["requests"].add(item.request_id)
        row["positions"] += 1
        row["quoted_positions"] += item.id in quoted
        row["ordered_positions"] += item.id in ordered
        row["ordered_quantity"] += ordered.get(item.id, Decimal("0"))
        row["ordered_sales"] += sales.get(item.id, Decimal("0"))
    result = []
    for row in rows.values():
        row["requests"] = len(row["requests"])
        row["ordered_sales"] = row["ordered_sales"].quantize(Decimal("0.01"))
        row["ordered_quantity"] = format(row["ordered_quantity"].normalize(), "f") if row["ordered_quantity"] else "0"
        result.append(row)
    result.sort(key=lambda row: (row["manufacturer_id"] is None, -row["positions"], row["name"].casefold()))
    return plain({
        "items": result, "total": len(result), "by": by,
        "period": {"from": start, "to": finish - timedelta(microseconds=1), "timezone": settings.company_timezone},
        "basis": "Позиции заявок, созданных за период: производитель — из номенклатуры позиции, из колонки "
                 "производителя в таблице заявки или по квотам поставщиков. Заказано — принятые клиентом позиции, "
                 "сумма с НДС по документам.",
    })
