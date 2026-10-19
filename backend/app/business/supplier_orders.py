"""Delivery tariffs and orders to suppliers with their lines and purchase order files."""

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import select

from app.commerce.calculator import dec
from app.commerce.files import pdf_decimal, workbook
from app.commerce.models import Calculation, CommercialDocument, Execution, Wave
from app.commerce.procurement import product_view, supplier
from app.commerce.schemas import Command, Input, Nonnegative, VersionCommand
from app.commerce.waves import finish_delivered_waves
from app.core.db import DB, utcnow
from app.core.errors import error
from app.core.security import (
    Actor,
    can,
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


LINE_STATUSES = ("waiting", "delivery", "arrived_ru", "delivered")
STATUS_LABELS = {
    "waiting": "Ожидаем",
    "delivery": "В пути",
    "arrived_ru": "Прибыло в РФ",
    "delivered": "Доставлено клиенту",
    "cancelled": "Отменено",
}


def statuses(value: str | None) -> set[str]:
    """«waiting,delivery» → {"waiting", "delivery"}: several statuses can be shown at once."""
    return {part.strip() for part in (value or "").split(",") if part.strip()}


def require_ordered_access(db, user):
    """Ordering is procurement; a logistician only moves ordered positions along the delivery."""
    if not (can(db, user, "procurement.write") or can(db, user, "supplier_orders.delivery")):
        error("FORBIDDEN", "Недостаточно прав для этого действия", 403)


@router.get("/supplier-orders/positions")
def order_positions(
    db: DB, user: Actor, ordered: bool = False, supplier_id: str | None = None, status: str | None = None
):
    if ordered:
        require_ordered_access(db, user)
    else:
        require_permission(db, user, "procurement.write")
    visible = select(CRMRequest.id).where(request_predicate(db, user))
    items = []
    if not ordered:
        live = select(SupplierOrderLine.execution_id).where(SupplierOrderLine.status != "cancelled")
        query = select(Execution).where(
            Execution.request_id.in_(visible),
            Execution.quantity > Execution.cancelled_quantity,
            Execution.procurement_at.is_not(None),
            Execution.id.not_in(live),
        )
        for ex in db.scalars(query.order_by(Execution.created_at)):
            item = order_candidate(db, ex)
            if not supplier_id or item["supplier_id"] == supplier_id:
                items.append(item)
        return {"items": items, "total": len(items), "page": 1, "page_size": len(items)}
    prices = can(db, user, "procurement.write") or can(db, user, "finance.purchase.read")
    wanted = statuses(status)
    query = (
        select(SupplierOrderLine, SupplierOrder.number, CRMRequest.number, Counterparty.name)
        .join(SupplierOrder, SupplierOrder.id == SupplierOrderLine.order_id)
        .join(Execution, Execution.id == SupplierOrderLine.execution_id)
        .join(CRMRequest, CRMRequest.id == Execution.request_id)
        .join(Counterparty, Counterparty.id == CRMRequest.client_id)
        .where(Execution.request_id.in_(visible))
        .order_by(SupplierOrderLine.created_at, SupplierOrderLine.id)
    )
    for line, order_number, request_number, client_name in db.execute(query):
        item = {
            **line.snapshot,
            "id": line.id,
            "status": line.status,
            "version": line.version,
            "order_id": line.order_id,
            "order_number": order_number,
            "request_number": request_number,
            "client_name": client_name,
            "cancel_reason": line.cancel_reason,
            "cancelled_at": line.cancelled_at.isoformat() if line.cancelled_at else None,
        }
        if not prices:
            item.pop("unit_price", None)
        if (not supplier_id or item["supplier_id"] == supplier_id) and (not wanted or line.status in wanted):
            items.append(item)
    return {"items": items, "total": len(items), "page": 1, "page_size": len(items)}


def logistics_hint(db, executions: list[Execution], currency: str, supplier_id: str) -> dict:
    """International logistics the order should carry, in its currency, with where the amount comes from.

    The wave budget is preferred; otherwise the calculations behind the accepted proposals; otherwise the
    supplier card. Wave logistics already put into another live order of the supplier is not offered again.
    """
    amount, sources, notes, wave_ids = Decimal("0"), [], [], []

    def take(expenses, source):
        nonlocal amount
        found = False
        for row in expenses:
            value = dec(row.get("amount") or "0")
            if row.get("stage") != "INTERNATIONAL_LOGISTICS" or row.get("calculation_type", "FIXED") != "FIXED":
                continue
            if not value:
                continue
            if str(row.get("currency") or "RUB").upper() == currency:
                amount += value
                found = True
            else:
                notes.append(f"{source}: {pdf_decimal(value, money=True)} {row.get('currency')} — "
                             f"переведите в {currency} и укажите вручную")
        if found:
            sources.append(source)
        return found

    requests = {ex.request_id: db.get(CRMRequest, ex.request_id) for ex in executions}
    ordered_waves = set()
    for order in db.scalars(select(SupplierOrder).where(SupplierOrder.supplier_id == supplier_id)):
        logistics = (order.snapshot or {}).get("logistics") or {}
        if dec(logistics.get("amount") or "0") > 0 and db.scalar(
            select(SupplierOrderLine.id).where(
                SupplierOrderLine.order_id == order.id, SupplierOrderLine.status != "cancelled"
            ).limit(1)
        ):
            ordered_waves.update((wave_id, order.number) for wave_id in logistics.get("wave_ids") or [])
    covered = set()
    for wave_id in dict.fromkeys(req.wave_id for req in requests.values() if req.wave_id):
        wave = db.get(Wave, wave_id)
        already = next((number for ordered, number in ordered_waves if ordered == wave_id), None)
        if already:
            notes.append(f"Логистика волны {wave.number} уже включена в заказ {already}")
            covered.add(wave_id)
        elif wave.budget_expenses is not None:
            shared = [row for row in wave.budget_expenses if row.get("scope", "WAVE") == "WAVE"]
            if take(shared, f"бюджет волны {wave.number}"):
                wave_ids.append(wave_id)
            covered.add(wave_id)
    calculations = {}
    for ex in executions:
        proposal = db.get(CommercialDocument, ex.proposal_id)
        calculation = db.get(Calculation, proposal.calculation_id) if proposal else None
        if calculation:
            calculations.setdefault(calculation.id, (calculation, requests[ex.request_id]))
    for calculation, req in sorted(calculations.values(), key=lambda pair: pair[0].created_at, reverse=True):
        expenses = (calculation.snapshot.get("input") or {}).get("expenses") or []
        label = f"расчёт заявки №{req.number}"
        own = [row for row in expenses if row.get("scope") == "REQUEST" or not req.wave_id]
        take(own, label)
        if req.wave_id and req.wave_id not in covered:
            covered.add(req.wave_id)
            if take([row for row in expenses if row.get("scope", "WAVE") == "WAVE"], label + " (общие расходы волны)"):
                wave_ids.append(req.wave_id)
    if not sources and not notes:
        from app.commerce.financial import supplier_logistics

        default = supplier_logistics(db, supplier_id)
        if default:
            take([default], "карточка поставщика")
    return {
        "amount": str(amount),
        "currency": currency,
        "basis": "; ".join(sources),
        "notes": notes,
        "wave_ids": wave_ids,
    }


def selected_executions(db, user, execution_ids: list[str], *, locked: bool) -> list[Execution]:
    if len(set(execution_ids)) != len(execution_ids):
        error("DUPLICATE_SELECTION", "Позиция выбрана повторно")
    rows = []
    for entity_id in execution_ids:
        ex = lock(db, Execution, entity_id) if locked else db.get(Execution, entity_id)
        if not ex:
            error("NOT_FOUND", "Позиция не найдена", 404)
        check_request(db, user, ex.request_id)
        rows.append(ex)
    return rows


@router.get("/supplier-orders/logistics")
def order_logistics(db: DB, user: Actor, execution_ids: list[str] = Query(default=[], max_length=500)):
    require_permission(db, user, "procurement.write")
    executions = selected_executions(db, user, execution_ids, locked=False)
    candidates = [order_candidate(db, ex) for ex in executions]
    if not candidates or len({(r["supplier_id"], r["currency"]) for r in candidates}) != 1:
        error("ORDER_SUPPLIER", "Выберите позиции одного поставщика в одной валюте")
    return logistics_hint(db, executions, candidates[0]["currency"], candidates[0]["supplier_id"])


class OrderIn(Command):
    execution_ids: list[str] = Field(min_length=1, max_length=500)
    seller_id: str
    expected_date: date
    contract: str = Field(default="", max_length=2000)
    payment_terms: str = Field(default="", max_length=4000)
    delivery_terms: str = Field(default="", max_length=2000)
    prices: dict[str, str] = Field(default_factory=dict)
    # None: the logistics the order carries is taken from the wave, the calculation or the supplier card.
    logistics_amount: Nonnegative | None = None
    # Small orders are consolidated and their logistics paid once later: the order and its PO carry none.
    logistics_separately: bool = False


@router.post("/supplier-orders")
def order_create(data: OrderIn, db: DB, user: Actor):
    require_permission(db, user, "procurement.write")

    def operation():
        advisory(db, "supplier-orders.create")
        executions = selected_executions(db, user, data.execution_ids, locked=True)
        rows = []
        for ex in executions:
            if ex.quantity <= ex.cancelled_quantity or db.scalar(
                select(SupplierOrderLine.id).where(
                    SupplierOrderLine.execution_id == ex.id, SupplierOrderLine.status != "cancelled"
                )
            ):
                error("ORDER_POSITION_USED", "Позиция отменена или уже заказана", 409)
            if ex.procurement_at is None:
                error("PROCUREMENT_HANDOFF_REQUIRED", "Сначала согласуйте и передайте позицию в закупки")
            row = order_candidate(db, ex)
            row["unit_price"] = str(dec(data.prices.get(ex.id, row["unit_price"])))
            if dec(row["unit_price"]) < 0:
                error("ORDER_PRICE", "Закупочная цена не может быть отрицательной")
            rows.append(row)
        if len({(r["supplier_id"], r["currency"]) for r in rows}) != 1:
            error("ORDER_SUPPLIER", "Выберите позиции одного поставщика в одной валюте")
        seller = db.get(Seller, data.seller_id)
        if not seller or seller.archived:
            error("SELLER_REQUIRED", "Выберите действующую организацию покупателя")
        party = supplier(db, rows[0]["supplier_id"])
        hint = logistics_hint(db, executions, rows[0]["currency"], party.id)
        logistics_amount = hint["amount"] if data.logistics_amount is None else str(data.logistics_amount)
        if data.logistics_separately:
            logistics_amount = "0"
        logistics = {
            "amount": logistics_amount,
            "currency": rows[0]["currency"],
            "basis": "Оплачивается отдельно" if data.logistics_separately
            else hint["basis"] if dec(logistics_amount) == dec(hint["amount"]) else "Указано вручную",
            "wave_ids": hint["wave_ids"] if dec(logistics_amount) > 0 else [],
            "separately": data.logistics_separately,
        }
        from app.crm.models import NumberCounter

        prefix = re.sub(r"[^\w]", "", party.name)[:5].upper() or "PO"
        counter = db.get(NumberCounter, "supplier-order:" + party.id)
        if not counter:
            counter = NumberCounter(key="supplier-order:" + party.id, value=0)
            db.add(counter)
        counter.value += 1
        number = f"{prefix}{party.internal_code}-{counter.value:06d}"
        total = sum(dec(r["quantity"]) * dec(r["unit_price"]) for r in rows) + dec(logistics_amount)
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
            snapshot={
                "supplier": serialize(party),
                "customer": serialize(seller),
                "lines": rows,
                "logistics": logistics,
            },
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
        return order_view(order)

    return idem(
        db, user, data.idempotency_key, "supplier-order.create", data.model_dump(mode="json"), operation
    )


def order_state(lines) -> str:
    live = {line.status for line in lines if line.status != "cancelled"}
    if not live:
        return "cancelled" if lines else "waiting"
    if live == {"delivered"}:
        return "delivered"
    if live <= {"arrived_ru", "delivered"}:
        return "arrived_ru"
    if live & {"delivery", "arrived_ru", "delivered"}:
        return "delivery"
    return "waiting"


def order_view(order, lines=None) -> dict:
    logistics = (order.snapshot or {}).get("logistics") or {}
    value = {
        **serialize(order),
        "supplier_name": order.snapshot["supplier"]["name"],
        "logistics_amount": str(logistics.get("amount") or "0"),
        "logistics_basis": logistics.get("basis") or "",
        "logistics_separately": bool(logistics.get("separately")),
    }
    if lines is not None:
        value["status"] = order_state(lines)
    return value


@router.get("/supplier-orders")
def order_list(db: DB, user: Actor, supplier_id: str | None = None, status: str | None = None):
    require_permission(db, user, "procurement.write")
    wanted = statuses(status)
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
        item = order_view(order, lines)
        if wanted and item["status"] not in wanted:
            continue
        items.append(item)
    return {"items": items, "total": len(items), "page": 1, "page_size": len(items)}


def locked_lines(db, user, refs) -> list:
    if len({ref.id for ref in refs}) != len(refs):
        error("DUPLICATE_SELECTION", "Позиция выбрана повторно")
    rows = []
    for ref in refs:
        row = lock(db, SupplierOrderLine, ref.id)
        check_request(db, user, db.get(Execution, row.execution_id).request_id)
        check_version(row, ref.version)
        if row.status == "cancelled":
            error("ORDER_LINE_CANCELLED", f"Позиция «{row.snapshot.get('name')}» уже отменена", 409)
        rows.append(row)
    return rows


def set_line_status(db, user, row, status):
    if row.status == status:
        return
    before = serialize(row)
    row.status = status
    row.version += 1
    audit(db, user, "supplier_order_line", row.id, "status", before, serialize(row))


class OrderLineStatus(VersionCommand):
    status: Literal["waiting", "delivery", "arrived_ru", "delivered"]


@router.patch("/supplier-order-lines/{line_id}")
def order_line_status(line_id: str, data: OrderLineStatus, db: DB, user: Actor):
    require_ordered_access(db, user)

    def operation():
        row = locked_lines(db, user, [LineRef(id=line_id, version=data.version)])[0]
        set_line_status(db, user, row, data.status)
        finish_delivered_waves(db, user, [row.execution_id])
        return serialize(row)

    return idem(
        db, user, data.idempotency_key, f"order-line:{line_id}", data.model_dump(mode="json"), operation
    )


class LineRef(Input):
    id: str
    version: int = Field(ge=1)


class OrderLinesStatusIn(Command):
    lines: list[LineRef] = Field(min_length=1, max_length=500)
    status: Literal["waiting", "delivery", "arrived_ru", "delivered"]


@router.post("/supplier-order-lines/status")
def order_lines_status(data: OrderLinesStatusIn, db: DB, user: Actor):
    """Moves the selected ordered positions to one delivery status, for instance «Прибыло в РФ»."""
    require_ordered_access(db, user)

    def operation():
        advisory(db, "supplier-order-lines.status")
        rows = locked_lines(db, user, data.lines)
        for row in rows:
            set_line_status(db, user, row, data.status)
        # Every order of a wave delivered to its client: the wave goes to the archive as delivered.
        finished = finish_delivered_waves(db, user, [row.execution_id for row in rows])
        return {"items": [serialize(row) for row in rows], "count": len(rows), "finished_waves": finished}

    return idem(
        db, user, data.idempotency_key, "order-lines:status", data.model_dump(mode="json"), operation
    )


class OrderLinesCancelIn(Command):
    lines: list[LineRef] = Field(min_length=1, max_length=500)
    reason: str = Field(max_length=2000)


@router.post("/supplier-order-lines/cancel")
def order_lines_cancel(data: OrderLinesCancelIn, db: DB, user: Actor):
    """Cancels ordered positions with a reason; each returns to «Позиции к заказу» for a new order."""
    require_permission(db, user, "procurement.write")
    reason = " ".join(data.reason.split())
    if len(reason) < 3:
        error("CANCEL_REASON_REQUIRED", "Укажите причину отмены", field="reason")

    def operation():
        advisory(db, "supplier-order-lines.status")
        rows = locked_lines(db, user, data.lines)
        for row in rows:
            if row.status == "delivered":
                error("ORDER_LINE_DELIVERED",
                      f"Позиция «{row.snapshot.get('name')}» уже доставлена клиенту и не отменяется", 409)
            before = serialize(row)
            row.status = "cancelled"
            row.cancel_reason, row.cancelled_at, row.cancelled_by = reason, utcnow(), user.id
            row.version += 1
            audit(db, user, "supplier_order_line", row.id, "cancel", before, serialize(row), reason=reason)
        return {"items": [serialize(row) for row in rows], "count": len(rows)}

    return idem(
        db, user, data.idempotency_key, "order-lines:cancel", data.model_dump(mode="json"), operation
    )


@router.get("/supplier-orders/positions/export.xlsx")
def positions_export(db: DB, user: Actor, supplier_id: str | None = None, status: str | None = None):
    rows = order_positions(db, user, True, supplier_id, status)["items"]
    return Response(
        workbook(
            ["Order", "Request", "Client", "Name", "Manufacturer", "Article", "Packing", "Qty", "Status",
             "Cancel reason"],
            [
                [
                    r.get("order_number"),
                    r.get("request_number"),
                    r.get("client_name"),
                    r["name"],
                    r.get("manufacturer"),
                    r.get("article"),
                    r.get("packaging"),
                    r["quantity"],
                    STATUS_LABELS.get(r["status"], r["status"]),
                    r.get("cancel_reason") or "",
                ]
                for r in rows
            ],
            "Ordered positions",
        ),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def readable_order(db, user, order_id):
    require_permission(db, user, "procurement.write")
    order = db.get(SupplierOrder, order_id)
    if not order:
        error("NOT_FOUND", "Заказ не найден", 404)
    for row in order.snapshot["lines"]:
        check_request(db, user, row["request_id"])
    return order


def attachment(name: str) -> dict:
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return {"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"}


@router.get("/supplier-orders/{order_id}/po.xlsx")
def order_po(order_id: str, db: DB, user: Actor, without_logistics: bool = False):
    order = readable_order(db, user, order_id)
    from .purchase_order import purchase_order

    return Response(
        purchase_order(order, with_logistics=not without_logistics),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=attachment(f"PO_{order.number}.xlsx"),
    )


@router.get("/supplier-orders/{order_id}/po.pdf")
def order_po_pdf(order_id: str, db: DB, user: Actor, without_logistics: bool = False):
    """The PO to send; `without_logistics` leaves the freight out for an order paid in a consolidation."""
    order = readable_order(db, user, order_id)
    from .purchase_order import purchase_order_pdf

    return Response(
        purchase_order_pdf(order, with_logistics=not without_logistics), media_type="application/pdf",
        headers=attachment(f"PO_{order.number}.pdf"),
    )
