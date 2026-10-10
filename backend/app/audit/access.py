"""Which CRM request an audit record belongs to, and what its reader may see of the snapshots."""

from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.business.models import CalendarEntry, SupplierMail, SupplierOrderLine, WorkflowReview
from app.commerce.financial import filter_calculation_snapshot
from app.commerce.models import (
    Approval,
    Calculation,
    CommercialDocument,
    Execution,
    FulfillmentEvent,
    Payment,
    PaymentAllocation,
    PaymentReversal,
    Quote,
    SupplierRequest,
    WaveAllocation,
)
from app.communication.models import FileRecord
from app.core.models import User
from app.core.security import can, request_predicate
from app.crm.models import QuoteItem, QuoteSheet, Request, RequestItem, TableImport

FINANCE = ("finance.purchase.read", "finance.calculations.read", "finance.reward.read", "finance.profit.read")
# Audit rows of a request itself; "requests" is written by owner reassignment (table name).
REQUEST_TYPES = {"request", "requests"}
# A request-bound record whose request can no longer be determined is shown as fully restricted.
UNRESOLVED = "unresolved"


def _direct(model: Any) -> Callable[[set[str]], Select]:
    return lambda ids: select(model.id, model.request_id).where(model.id.in_(ids))


REQUEST_OF: dict[str, Callable[[set[str]], Select]] = {
    "request_item": _direct(RequestItem),
    "calculation": _direct(Calculation),
    "quote": _direct(Quote),
    "quote_sheet": _direct(QuoteSheet),
    "quote_item": lambda ids: select(QuoteItem.id, QuoteSheet.request_id)
    .join(QuoteSheet, QuoteSheet.id == QuoteItem.quote_id).where(QuoteItem.id.in_(ids)),
    "document": _direct(CommercialDocument),
    "execution": _direct(Execution),
    "approval": _direct(Approval),
    "payment": _direct(Payment),
    "payment_allocation": lambda ids: select(PaymentAllocation.id, Payment.request_id)
    .join(Payment, Payment.id == PaymentAllocation.payment_id).where(PaymentAllocation.id.in_(ids)),
    "payment_reversal": lambda ids: select(PaymentReversal.id, Payment.request_id)
    .join(Payment, Payment.id == PaymentReversal.payment_id).where(PaymentReversal.id.in_(ids)),
    "supplier_request": _direct(SupplierRequest),
    "supplier_mail": _direct(SupplierMail),
    "table_import": _direct(TableImport),
    "workflow_review": _direct(WorkflowReview),
    "calendar_entry": _direct(CalendarEntry),
    "file": _direct(FileRecord),
    "wave_allocation": lambda ids: select(WaveAllocation.id, Execution.request_id)
    .join(Execution, Execution.id == WaveAllocation.execution_id).where(WaveAllocation.id.in_(ids)),
    "fulfillment_event": lambda ids: select(FulfillmentEvent.id, Execution.request_id)
    .join(WaveAllocation, WaveAllocation.id == FulfillmentEvent.allocation_id)
    .join(Execution, Execution.id == WaveAllocation.execution_id).where(FulfillmentEvent.id.in_(ids)),
    "supplier_order_line": lambda ids: select(SupplierOrderLine.id, Execution.request_id)
    .join(Execution, Execution.id == SupplierOrderLine.execution_id).where(SupplierOrderLine.id.in_(ids)),
}


def request_ids_of(db: Session, rows: list[dict[str, Any]]) -> dict[str, str | None]:
    """Audit row id -> CRM request id; None for records outside requests (users, settings, ...)."""
    wanted: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row["entity_type"] in REQUEST_OF:
            wanted[row["entity_type"]].add(row["entity_id"])
    found: dict[tuple[str, str], str | None] = {}
    for entity_type, ids in wanted.items():
        found.update(((entity_type, entity_id), request_id)
                     for entity_id, request_id in db.execute(REQUEST_OF[entity_type](ids)))
    owners: dict[str, str | None] = {}
    for row in rows:
        kind = row["entity_type"]
        if kind in REQUEST_TYPES:
            owners[row["id"]] = row["entity_id"]
        elif kind in REQUEST_OF:
            # Optional links (files, calendar entries, reviews) may legitimately have no request.
            owners[row["id"]] = found.get((kind, row["entity_id"]), UNRESOLVED)
        else:
            owners[row["id"]] = None
    return owners


def finance_rights(db: Session, user: User, request_ids: Iterable[str | None]) -> dict[str | None, dict[str, bool]]:
    """Finance rights per request (one query per right), globally for records outside requests."""
    rights: dict[str | None, dict[str, bool]] = {None: {code: can(db, user, code) for code in FINANCE}}
    rights[UNRESOLVED] = dict.fromkeys(FINANCE, False)
    ids = {request_id for request_id in request_ids if request_id and request_id != UNRESOLVED}
    for code in FINANCE:
        allowed = set(db.scalars(select(Request.id).where(Request.id.in_(ids), request_predicate(db, user, code)))) if ids else set()
        for request_id in ids:
            rights.setdefault(request_id, {})[code] = request_id in allowed
    return rights


def redact(db: Session, user: User, row: dict[str, Any], request_id: str | None,
           rights: dict[str | None, dict[str, bool]]) -> dict[str, Any]:
    allowed = rights[request_id]
    purchase, calculations, reward, profit = (allowed[code] for code in FINANCE)
    sides = [row[side] for side in ("before", "after") if isinstance(row.get(side), dict)]
    if row["entity_type"] == "calculation":
        snapshot_owner = request_id if request_id != UNRESOLVED else None
        for side in sides:
            if "snapshot" in side:
                side["snapshot"] = filter_calculation_snapshot(db, user, snapshot_owner, side["snapshot"])
    elif row["entity_type"] == "quote":
        if not purchase:
            for side in sides:
                for field in ("price", "sample", "revision_reason"):
                    side.pop(field, None)
    elif row["entity_type"] == "calculation_profile":
        for side in sides:
            definition = side.get("definition")
            if not isinstance(definition, dict):
                continue
            if not reward:
                for field in ("reward_enabled", "reward_label", "reward_basis"):
                    definition.pop(field, None)
            if not profit:
                definition.pop("constants", None)
                definition.pop("formulas", None)
    elif not (purchase and calculations and reward and profit):
        row.pop("before", None)
        row.pop("after", None)
    return row
