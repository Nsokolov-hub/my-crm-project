"""Positions of the waves in use, one row per position (business remark of 31 October 2026).

Two kinds of rows. A confirmed order: an accepted position, in the working wave that holds it, or in the
request's wave while it waits for one. A position still offered: a line of the latest calculation of an open
request of the wave that has nothing accepted yet — «Ждём ответа покупателя» once a current proposal is out,
«Расчёт в работе» before. Closed, archived and test requests, requests whose latest proposal was refused and
archived waves are left out. Amounts come from the saved calculation line, scaled to the quantity of the row.
"""

from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter
from sqlalchemy import select

from app.core.db import DB
from app.core.security import Actor, can, require_permission
from app.core.service import prefetch
from app.crm.models import Counterparty, RequestItem
from app.crm.models import Request as CRMRequest

from .calculator import dec
from .models import Calculation, CommercialDocument, Execution, Wave, WaveAllocation
from .wave_board import permitted

router = APIRouter(tags=["Согласования и поставки"])

STATUS_LABELS = {
    "order": "Заказ подтверждён",
    "proposal": "Ждём ответа покупателя",
    "calculation": "Расчёт в работе",
}
CURRENT_PROPOSALS = ("issued", "sent")
REFUSED_PROPOSALS = ("rejected",)


def money(value: Decimal | None) -> str | None:
    return None if value is None else format(value.quantize(Decimal("0.01")), "f")


def pieces(value: Decimal) -> str:
    return format(value.normalize(), "f") if value else "0"


def product_of(line: dict | None, item: RequestItem | None, fallback: str) -> dict:
    """Name, article, packaging and manufacturer of a position: the calculation line first, then the request."""
    product = (line or {}).get("product") or {}
    return {
        "name": product.get("name") or (item.description if item else None) or (line or {}).get("description")
        or fallback,
        "article": product.get("article") or (item.article if item else None),
        "packaging": product.get("packaging") or (line or {}).get("packing") or (item.packaging if item else None),
        "manufacturer": product.get("manufacturer"),
        "cas": product.get("cas") or (item.cas if item else None),
    }


def line_figures(line: dict | None, quantity: Decimal) -> dict:
    """Purchase, sale and expenses of `quantity` pieces of a saved calculation line."""
    if not line or not dec(line.get("quantity") or "0"):
        return {"purchase": None, "sale_unit_price": None, "sale_total": None, "expenses": None,
                "profit": None, "profitability_percent": None}
    factor = quantity / dec(line["quantity"])
    detail = line.get("detail") or {}

    def scaled(key: str) -> Decimal | None:
        return dec(detail[key]) * factor if detail.get(key) not in (None, "") else None

    sale_net = dec(line.get("net") or "0") * factor
    profit = scaled("profit")
    if profit is None and detail.get("cost") not in (None, ""):
        profit = sale_net - dec(detail["cost"]) * factor
    profitability = None
    if profit is not None and sale_net:
        profitability = profit / sale_net * 100
    return {
        "purchase": money(scaled("purchase_rub")),
        "sale_unit_price": money(dec(line["unit_price"])) if line.get("unit_price") else None,
        "sale_total": money(dec(line.get("total") or "0") * factor),
        "expenses": money(scaled("expenses_total")),
        "profit": money(profit),
        "profitability_percent": money(profitability),
    }


class Positions:
    """Loads the rows; the caller decides which amounts the user may see."""

    def __init__(self, db) -> None:
        self.db = db
        self.waves = {row.id: row for row in db.scalars(select(Wave).where(Wave.archived.is_(False)))}
        open_request = (CRMRequest.archived.is_(False), CRMRequest.is_test.is_(False),
                        CRMRequest.closed_at.is_(None), CRMRequest.commercial_stage != "closed_lost")
        allocations = db.scalars(select(WaveAllocation).where(
            WaveAllocation.wave_id.in_(set(self.waves)), WaveAllocation.active.is_(True))).all()
        planned = db.scalars(select(CRMRequest).where(CRMRequest.wave_id.in_(set(self.waves)), *open_request)).all()
        self.executions = prefetch(db, Execution, (row.execution_id for row in allocations))
        for execution in db.scalars(select(Execution).where(Execution.request_id.in_({row.id for row in planned}))):
            self.executions[execution.id] = execution
        self.requests = prefetch(db, CRMRequest, (row.request_id for row in self.executions.values()))
        self.requests.update({row.id: row for row in planned})
        self.allocated: dict[str, Decimal] = defaultdict(Decimal)
        for row in db.scalars(select(WaveAllocation).where(
                WaveAllocation.execution_id.in_(set(self.executions)), WaveAllocation.active.is_(True))):
            self.allocated[row.execution_id] += row.quantity
        self.documents = prefetch(db, CommercialDocument, (row.proposal_id for row in self.executions.values()))
        self.calculations = prefetch(db, Calculation, (row.calculation_id for row in self.documents.values()))
        self.items = prefetch(db, RequestItem, (row.item_id for row in self.executions.values()))
        self.rows: list[dict] = []
        for allocation in allocations:
            execution = self.executions[allocation.execution_id]
            request = self.requests.get(execution.request_id)
            if request and self.is_open(request) and execution.quantity > execution.cancelled_quantity:
                self.order_row(execution, allocation.wave_id, allocation.quantity, allocation.id)
        accepted = defaultdict(list)
        for execution in self.executions.values():
            accepted[execution.request_id].append(execution)
        for request in planned:
            if request.id in accepted:
                # Accepted positions still waiting for a working wave stay in the request's wave.
                for execution in accepted[request.id]:
                    rest = execution.quantity - execution.cancelled_quantity - self.allocated[execution.id]
                    if rest > 0:
                        self.order_row(execution, str(request.wave_id), rest, None)
                continue
            self.offered_rows(request)
        clients = prefetch(db, Counterparty, (row["client_id"] for row in self.rows))
        for row in self.rows:
            client = clients.get(row["client_id"])
            row["client_name"] = client.name if client else None

    @staticmethod
    def is_open(request: CRMRequest) -> bool:
        return (not request.archived and not request.is_test and request.closed_at is None
                and request.commercial_stage != "closed_lost")

    def wave_fields(self, wave_id: str) -> dict:
        wave = self.waves.get(wave_id)
        return {"wave_id": wave_id, "wave_number": wave.number if wave else None,
                "wave_kind": wave.kind if wave else None}

    def order_row(self, execution: Execution, wave_id: str, quantity: Decimal, allocation_id: str | None) -> None:
        request = self.requests[execution.request_id]
        document = self.documents.get(execution.proposal_id)
        calculation = self.calculations.get(document.calculation_id) if document else None
        lines = {line["line_id"]: line for line in (calculation.snapshot.get("lines", []) if calculation else [])}
        line = lines.get(execution.line_id)
        self.rows.append({
            "id": f"order:{allocation_id or execution.id}", "kind": "order", "status": "order",
            "status_label": STATUS_LABELS["order"], "request_id": request.id, "request_number": request.number,
            "client_id": request.client_id, "execution_id": execution.id, "allocation_id": allocation_id,
            **self.wave_fields(wave_id),
            **product_of(line, self.items.get(execution.item_id), str(execution.snapshot.get("description") or "")),
            "quantity": pieces(quantity), "unit": execution.unit,
            "currency": calculation.snapshot.get("currency", "RUB") if calculation else None,
            "calculation_id": calculation.id if calculation else None,
            "calculation_number": calculation.snapshot.get("calculation_number") if calculation else None,
            "calculated_at": calculation.created_at.isoformat() if calculation else None,
            "proposal_number": document.number if document else None,
            **line_figures(line, quantity),
        })

    def offered_rows(self, request: CRMRequest) -> None:
        calculation = self.db.scalar(select(Calculation).where(Calculation.request_id == request.id)
                                     .order_by(Calculation.created_at.desc(), Calculation.id.desc()).limit(1))
        if not calculation:
            return
        proposal = self.db.scalar(select(CommercialDocument).where(
            CommercialDocument.request_id == request.id, CommercialDocument.kind == "proposal",
            CommercialDocument.status.not_in(("replaced", "cancelled")))
            .order_by(CommercialDocument.created_at.desc(), CommercialDocument.id.desc()).limit(1))
        if proposal and proposal.status in REFUSED_PROPOSALS:
            return
        waiting = bool(proposal and proposal.status in CURRENT_PROPOSALS and proposal.calculation_id == calculation.id)
        status = "proposal" if waiting else "calculation"
        items = prefetch(self.db, RequestItem, (line.get("item_id") for line in calculation.snapshot.get("lines", [])))
        for line in calculation.snapshot.get("lines", []):
            quantity = dec(line.get("quantity") or "0")
            self.rows.append({
                "id": f"calc:{calculation.id}:{line['line_id']}", "kind": "offer", "status": status,
                "status_label": STATUS_LABELS[status], "request_id": request.id, "request_number": request.number,
                "client_id": request.client_id, "execution_id": None, "allocation_id": None,
                **self.wave_fields(str(request.wave_id)),
                **product_of(line, items.get(line.get("item_id")), str(line.get("description") or "")),
                "quantity": pieces(quantity), "unit": line.get("unit"),
                "currency": calculation.snapshot.get("currency", "RUB"),
                "calculation_id": calculation.id,
                "calculation_number": calculation.snapshot.get("calculation_number"),
                "calculated_at": calculation.created_at.isoformat(),
                "proposal_number": proposal.number if waiting and proposal else None,
                **line_figures(line, quantity),
            })


@router.get("/waves/positions")
def wave_positions(db: DB, user: Actor, wave_id: str | None = None, status: str | None = None, q: str = ""):
    """Positions of the waves in use, newest calculation first.

    Rows need the request to be readable; amounts also the purchase and calculation rights on it, and
    profitability the profit right.
    """
    require_permission(db, user, "requests.read")
    positions = Positions(db)
    rows = positions.rows
    visible = permitted(db, user, {row["request_id"] for row in rows}, "requests.read")
    priced = permitted(db, user, visible, "finance.purchase.read", "finance.calculations.read") if (
        can(db, user, "finance.purchase.read") and can(db, user, "finance.calculations.read")) else set()
    profitable = permitted(db, user, priced, "finance.profit.read") if can(db, user, "finance.profit.read") else set()
    text = q.strip().casefold()
    items = []
    waves: dict[str, str] = {}
    for row in rows:
        if row["request_id"] not in visible:
            continue
        if row["wave_number"]:
            waves[row["wave_id"]] = row["wave_number"]
        if wave_id and row["wave_id"] != wave_id:
            continue
        if status and row["status"] != status:
            continue
        if text and not any(text in str(row.get(key) or "").casefold() for key in (
                "name", "article", "packaging", "manufacturer", "cas", "client_name", "request_number",
                "wave_number")):
            continue
        if row["request_id"] not in priced:
            row = {**row, **dict.fromkeys(("purchase", "sale_unit_price", "sale_total", "expenses"), None)}
        if row["request_id"] not in profitable:
            row = {**row, "profit": None, "profitability_percent": None}
        items.append(row)
    items.sort(key=lambda row: (row["calculated_at"] or "", row["request_number"] or ""), reverse=True)
    return {"items": items, "total": len(items), "statuses": STATUS_LABELS,
            "waves": [{"id": wave, "number": number} for wave, number in sorted(waves.items(), key=lambda p: p[1])]}
