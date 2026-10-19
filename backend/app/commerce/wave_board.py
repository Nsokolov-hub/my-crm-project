"""Board of waves in use: planned and working lanes with a short sales summary per wave.

The sales of a wave are what was accepted into it, as inside the wave: accepted positions count in the wave
that holds them; positions accepted but not yet handed to a wave count in the request's wave. What its
requests still offer — the latest current commercial proposal of a request with nothing accepted — is shown
apart as «В КП» and is not added to the sales. Every amount comes from the saved calculation lines in RUB, so
sales, profit and logistics on one card share one basis.
"""

from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter
from sqlalchemy import select

from app.core.db import DB
from app.core.errors import DomainError
from app.core.security import Actor, can, request_predicate, require_permission
from app.core.service import prefetch
from app.crm.models import Counterparty
from app.crm.models import Request as CRMRequest

from .calculator import dec, profitability_metrics
from .models import Calculation, CommercialDocument, Execution, Wave, WaveAllocation, WaveForecast

router = APIRouter(tags=["Согласования и поставки"])

CURRENT_PROPOSALS = ("issued", "sent", "accepted")
AMOUNTS = ("quantity", "sales", "net", "profit", "logistics")
BASIS = ("Продажи — реальный объём: принятые клиентом позиции, в волне, где они распределены. Заявки волны, "
         "по которым КП ещё не принято, показаны отдельно («В КП») и в продажи не входят. Продажи с НДС, "
         "рентабельность — прибыль к продажам без НДС, как внутри волны: цены документов и текущие общие "
         "расходы волны. Логистика — международная на штуку, по принятым позициям.")


def line_amounts(line: dict, quantity: Decimal) -> dict[str, Decimal]:
    """The share of a saved calculation line for `quantity` packings."""
    factor = quantity / dec(line["quantity"])
    detail = line.get("detail") or {}
    net = dec(line["net"])
    profit = dec(detail["profit"]) if "profit" in detail else net - dec(detail.get("cost", "0"))
    return {"quantity": quantity, "sales": dec(line["total"]) * factor, "net": net * factor,
            "profit": profit * factor, "logistics": dec(detail.get("international_logistics", "0")) * factor}


class Ledger:
    """What one wave has accepted and still offers, and the requests behind it."""

    def __init__(self) -> None:
        self.accepted = dict.fromkeys(AMOUNTS, Decimal("0"))
        # The part of `accepted` not yet handed to a wave: priced by its saved calculation only.
        self.pending = dict.fromkeys(AMOUNTS, Decimal("0"))
        self.offered = dict.fromkeys(AMOUNTS, Decimal("0"))
        self.proposals = 0
        self.requests: set[str] = set()
        self.without_proposal: set[str] = set()
        self.unpriced = False

    def total(self) -> dict[str, Decimal]:
        """Accepted and offered together: the potential of the wave, not its sales."""
        return {key: self.accepted[key] + self.offered[key] for key in AMOUNTS}


def money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def pieces(value: Decimal) -> str:
    return format(value.normalize(), "f") if value else "0"


def permitted(db, user, ids: set[str], *permissions: str) -> set[str]:
    """The requests among `ids` on which the user holds every one of `permissions`."""
    allowed = set(ids)
    for permission in permissions:
        if allowed:
            allowed &= set(db.scalars(select(CRMRequest.id).where(
                CRMRequest.id.in_(allowed), request_predicate(db, user, permission))))
    return allowed


class Board:
    """Loads the waves in use and spreads accepted positions and proposals over their ledgers."""

    def __init__(self, db) -> None:
        self.db = db
        self.waves = db.scalars(select(Wave).where(Wave.archived.is_(False))
                                .order_by(Wave.arrival_date, Wave.departure_date, Wave.number)).all()
        ids = {wave.id for wave in self.waves}
        self.ledgers: dict[str, Ledger] = defaultdict(Ledger)
        self.planned = db.scalars(select(CRMRequest).where(
            CRMRequest.wave_id.in_(ids), CRMRequest.archived.is_(False), CRMRequest.is_test.is_(False),
            CRMRequest.commercial_stage != "closed_lost")).all()
        self.allocations = db.scalars(select(WaveAllocation).where(
            WaveAllocation.wave_id.in_(ids), WaveAllocation.active.is_(True))).all()
        self.executions = prefetch(db, Execution, (row.execution_id for row in self.allocations))
        planned_ids = {row.id for row in self.planned}
        for execution in db.scalars(select(Execution).where(Execution.request_id.in_(planned_ids))):
            self.executions[execution.id] = execution
        self.allocated: dict[str, Decimal] = defaultdict(Decimal)
        for row in db.scalars(select(WaveAllocation).where(
                WaveAllocation.execution_id.in_(set(self.executions)), WaveAllocation.active.is_(True))):
            self.allocated[row.execution_id] += row.quantity
        self.requests = prefetch(db, CRMRequest, (row.request_id for row in self.executions.values()))
        self.requests.update({row.id: row for row in self.planned})
        accepted = {row.request_id for row in self.executions.values()}
        self.latest: dict[str, CommercialDocument] = {}
        for proposal in db.scalars(select(CommercialDocument).where(
                CommercialDocument.request_id.in_(planned_ids - accepted), CommercialDocument.kind == "proposal",
                CommercialDocument.status.in_(CURRENT_PROPOSALS))
                .order_by(CommercialDocument.created_at.desc(), CommercialDocument.id.desc())):
            self.latest.setdefault(proposal.request_id, proposal)
        self.documents = prefetch(db, CommercialDocument, (row.proposal_id for row in self.executions.values()))
        self.documents.update({row.id: row for row in self.latest.values()})
        self.calculations = prefetch(db, Calculation, (row.calculation_id for row in self.documents.values()))
        self.forecasts: dict[str, Decimal] = defaultdict(Decimal)
        for forecast in db.scalars(select(WaveForecast).where(
                WaveForecast.wave_id.in_(ids), WaveForecast.active.is_(True))):
            self.forecasts[forecast.wave_id] += forecast.target_quantity
        self.spread()

    def lines(self, document_id: str) -> dict[str, dict] | None:
        """Saved calculation lines of a document by line, or None when they are not in RUB."""
        calculation = self.calculations.get(self.documents[document_id].calculation_id)
        snapshot = calculation.snapshot if calculation else {}
        if snapshot.get("currency", "RUB") != "RUB":
            return None
        return {line["line_id"]: line for line in snapshot.get("lines", [])}

    def accept(self, wave_id: str, execution: Execution, quantity: Decimal, allocated: bool = True) -> None:
        ledger = self.ledgers[wave_id]
        ledger.requests.add(execution.request_id)
        line = (self.lines(execution.proposal_id) or {}).get(execution.line_id)
        if not line:
            ledger.unpriced = True
            return
        for key, value in line_amounts(line, quantity).items():
            ledger.accepted[key] += value
            if not allocated:
                ledger.pending[key] += value

    def spread(self) -> None:
        for allocation in self.allocations:
            execution = self.executions[allocation.execution_id]
            if execution.quantity > execution.cancelled_quantity and not self.requests[execution.request_id].is_test:
                self.accept(allocation.wave_id, execution, allocation.quantity)
        by_request: dict[str, list[Execution]] = defaultdict(list)
        for execution in self.executions.values():
            by_request[execution.request_id].append(execution)
        for request in self.planned:
            wave_id = str(request.wave_id)
            if request.id in by_request:
                # Accepted positions still waiting for a working wave belong to the request's wave.
                for execution in by_request[request.id]:
                    rest = execution.quantity - execution.cancelled_quantity - self.allocated[execution.id]
                    if rest > 0:
                        self.accept(wave_id, execution, rest, allocated=False)
                continue
            ledger = self.ledgers[wave_id]
            ledger.requests.add(request.id)
            proposal = self.latest.get(request.id)
            if not proposal:
                ledger.without_proposal.add(request.id)
                continue
            ledger.proposals += 1
            lines = self.lines(proposal.id)
            if lines is None:
                ledger.unpriced = True
                continue
            for line in lines.values():
                for key, value in line_amounts(line, dec(line["quantity"])).items():
                    ledger.offered[key] += value


def current_result(db, wave: Wave, ledger: Ledger) -> dict[str, Decimal] | None:
    """Sales, net and profit of the accepted positions as inside the wave: positions in the wave at their
    document prices with the current shared costs of the wave, plus the accepted positions still waiting
    for a wave at their saved calculation. None when the wave cannot be priced that way."""
    from .financial import wave_financial_summary

    if ledger.accepted["quantity"] == ledger.pending["quantity"]:
        return None
    try:
        actual = (wave_financial_summary(db, wave.id) or {}).get("actual") or {}
    except DomainError:
        return None
    if actual.get("status") != "current":
        return None
    return {"sales": dec(actual["sales"]) + ledger.pending["sales"],
            "net": dec(actual["sale_net"]) + ledger.pending["net"],
            "profit": dec(actual["profit"]) + ledger.pending["profit"]}


@router.get("/waves/board")
def wave_board(db: DB, user: Actor):
    """Waves in use for the board, earliest arrival first.

    Quantities need every request of the wave to be visible, amounts also the purchase and calculation rights
    on each of them, and profitability the profit right.
    """
    require_permission(db, user, "requests.read")
    amounts = can(db, user, "finance.purchase.read") and can(db, user, "finance.calculations.read")
    profitability = amounts and can(db, user, "finance.profit.read")
    board = Board(db)
    involved = {request_id for ledger in board.ledgers.values() for request_id in ledger.requests}
    visible = permitted(db, user, involved, "requests.read")
    priced = permitted(db, user, visible, "finance.purchase.read", "finance.calculations.read")
    profitable = permitted(db, user, priced, "finance.profit.read")
    suppliers = prefetch(db, Counterparty, (wave.supplier_id for wave in board.waves))
    plans = prefetch(db, Wave, (wave.plan_id for wave in board.waves))
    cards = []
    for wave in board.waves:
        ledger = board.ledgers.get(wave.id) or Ledger()
        supplier = suppliers.get(wave.supplier_id) if wave.supplier_id else None
        plan = plans.get(wave.plan_id) if wave.plan_id else None
        card = {"id": wave.id, "number": wave.number, "kind": wave.kind, "status": wave.status,
                "version": wave.version, "route": wave.route, "supplier_id": wave.supplier_id,
                "supplier_name": supplier.name if supplier else None, "plan_id": wave.plan_id,
                "plan_number": plan.number if plan else None,
                "planned_quantity": pieces(board.forecasts[wave.id]) if board.forecasts.get(wave.id) else None,
                "requests": len(ledger.requests & visible), "hidden_requests": len(ledger.requests - visible),
                "requests_without_proposal": len(ledger.without_proposal & visible), "proposals": ledger.proposals,
                "quantity": None, "summary": None}
        for prefix in ("close", "departure", "arrival"):
            day = getattr(wave, f"{prefix}_date")
            year, week, _ = day.isocalendar()
            card.update({f"{prefix}_date": day.isoformat(), f"{prefix}_year": getattr(wave, f"{prefix}_year") or year,
                         f"{prefix}_week": getattr(wave, f"{prefix}_week") or week})
        # Business remark of 19 October 2026: the card shows the real sales, the same as inside the wave.
        real = ledger.accepted
        if ledger.requests <= visible:
            card["quantity"] = pieces(real["quantity"])
            card["offered_quantity"] = pieces(ledger.offered["quantity"])
        if amounts and ledger.requests <= priced:
            card["summary"] = {
                "sales": money(real["sales"]), "sale_net": money(real["net"]),
                "logistics": money(real["logistics"]),
                "logistics_per_unit": money(real["logistics"] / real["quantity"]) if real["quantity"] else None,
                "accepted_quantity": pieces(real["quantity"]), "accepted_sales": money(real["sales"]),
                "offered_quantity": pieces(ledger.offered["quantity"]), "offered_sales": money(ledger.offered["sales"]),
                "potential_sales": money(ledger.total()["sales"]),
                "incomplete": ledger.unpriced, "profit": None, "profitability_percent": None,
                "offered_profitability_percent": None, "profit_basis": "saved",
            }
            if profitability and ledger.requests <= profitable:
                # The same profit as inside the wave: current shared costs, not those of the proposal day.
                current = current_result(db, wave, ledger)
                if current:
                    card["summary"].update(sales=money(current["sales"]), sale_net=money(current["net"]),
                                           accepted_sales=money(current["sales"]), profit_basis="current")
                result = current or real
                card["summary"]["profit"] = money(result["profit"])
                card["summary"]["profitability_percent"] = money(profitability_metrics(
                    result["profit"], result["net"], Decimal("0"))["profitability_percent"]) if result["net"] else None
                card["summary"]["offered_profitability_percent"] = money(profitability_metrics(
                    ledger.offered["profit"], ledger.offered["net"], Decimal("0"))["profitability_percent"]
                ) if ledger.offered["net"] else None
        cards.append(card)
    return {"items": cards, "basis": BASIS, "amounts": amounts, "profitability": profitability}
