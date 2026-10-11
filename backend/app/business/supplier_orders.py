"""Delivery tariffs and orders to suppliers with their lines and purchase order files."""

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import select

from app.commerce.calculator import dec
from app.commerce.files import workbook
from app.commerce.models import Execution
from app.commerce.procurement import product_view, supplier
from app.commerce.schemas import Command, VersionCommand
from app.core.db import DB
from app.core.errors import error
from app.core.security import (
    Actor,
    check_request,
    has_request_permission,
    request_predicate,
    require_permission,
)
from app.core.service import advisory, audit, check_version, idem, lock, serialize
from app.crm.models import Counterparty, Nomenclature, Packing, QuoteItem, Seller
from app.crm.models import Request as CRMRequest

from .models import SupplierOrder, SupplierOrderLine

router = APIRouter(tags=["Платёжный календарь и заказы поставщикам"])


TARIFFS = json.loads((Path(__file__).parent / "delivery_tariffs.json").read_text())


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
