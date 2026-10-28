from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select

from app.commerce.recalculate import recalculate_fulfillment
from app.core.db import DB, utcnow
from app.core.errors import DomainError, error
from app.core.models import User
from app.core.security import (
    Actor,
    can,
    check_request,
    has_request_permission,
    request_predicate,
    require_permission,
)
from app.core.service import advisory, audit, check_version, idem, lock, notify, serialize
from app.crm.models import Counterparty, Nomenclature, ProductGroup, QuoteItem, RequestItem, Task
from app.crm.models import Request as CRMRequest

from .calculator import convert, dec, digest
from .models import (
    Approval,
    Calculation,
    CommercialDocument,
    Execution,
    FulfillmentEvent,
    Product,
    Quote,
    Wave,
    WaveAllocation,
    WaveForecast,
)
from .payments import funding_for_execution
from .procurement import check_quote
from .recalculate import recalculate_funding
from .schemas import (
    AllocateWaveIn,
    ApprovalIn,
    DecisionIn,
    ExecutionCancelIn,
    ExecutionReviseIn,
    FulfillmentIn,
    TransferIn,
    WaveArchiveIn,
    WaveBudgetIn,
    WaveCompleteIn,
    WaveForecastIn,
    WaveForecastRemoveIn,
    WaveIn,
    WaveTakeRequestsIn,
    WaveUpdate,
)
from .waves import OPEN_WAVES, active_plan, finish_wave, open_working_wave, usable, working_wave

router = APIRouter(tags=["Согласования и поставки"])
WAVE_TRANSITIONS = {
    "planned": {"assembling", "closed", "cancelled"},
    "assembling": {"closed", "cancelled"},
    "closed": {"assembling", "shipped", "cancelled"},
    "shipped": {"arrived"},
    "arrived": {"completed"},
    "completed": set(),
    "cancelled": set(),
}


def commerce_locks(db, executions: list[Execution]) -> None:
    keys = set()
    for execution in executions:
        doc = db.get(CommercialDocument, execution.proposal_id)
        keys.add(f"funding:{doc.seller_id}:{doc.client_id}:{doc.currency}")
    for key in sorted(keys):
        advisory(db, key)
    for request_id in sorted({row.request_id for row in executions}):
        advisory(db, f"request-commerce:{request_id}")


def check_execution_quote(db, execution: Execution):
    if execution.quote_item_id:
        quote_item = db.get(QuoteItem, execution.quote_item_id)
        if not quote_item:
            error("QUOTE_ITEM_NOT_FOUND", "Исходная позиция квоты не найдена")
        valid_until = quote_item.valid_until
        if valid_until.replace(tzinfo=valid_until.tzinfo or timezone.utc) <= datetime.now(timezone.utc):
            error("QUOTE_EXPIRED", "Срок действия позиции квоты истёк; получите подтверждение поставщика")
        return quote_item
    quote = db.get(Quote, execution.quote_id)
    check_quote(db, quote)
    return quote


def event_totals(db, allocation_id: str) -> dict[str, Decimal]:
    rows = db.scalars(select(FulfillmentEvent).where(FulfillmentEvent.allocation_id == allocation_id)).all()
    totals = {
        kind: Decimal("0")
        for kind in ("ordered", "ready", "shipped", "arrived", "delivered", "claim_opened", "claim_resolved")
    }
    originals = {row.id: row for row in rows if row.kind != "correction"}
    for row in rows:
        if row.kind == "correction":
            original = originals[row.correction_of]
            totals[original.kind] -= row.quantity
        else:
            totals[row.kind] += row.quantity
    return totals


def allocation_view(db, allocation: WaveAllocation) -> dict:
    return {
        **serialize(allocation),
        **{key: str(value) for key, value in event_totals(db, allocation.id).items()},
        "events": [
            serialize(row)
            for row in db.scalars(
                select(FulfillmentEvent)
                .where(FulfillmentEvent.allocation_id == allocation.id)
                .order_by(FulfillmentEvent.created_at)
            ).all()
        ],
    }


def execution_view(db, execution: Execution) -> dict:
    value = serialize(execution)
    value.pop("snapshot")
    value["description"] = execution.snapshot.get("description") or "Позиция исполнения"
    allocations = db.scalars(
        select(WaveAllocation).where(
            WaveAllocation.execution_id == execution.id, WaveAllocation.active.is_(True)
        )
    ).all()
    allocated = sum((row.quantity for row in allocations), Decimal("0"))
    totals = [event_totals(db, row.id) for row in allocations]
    shipped = sum((row["shipped"] for row in totals), Decimal("0"))
    delivered = sum((row["delivered"] for row in totals), Decimal("0"))
    claims = sum((row["claim_opened"] - row["claim_resolved"] for row in totals), Decimal("0"))
    approval_state = "not_submitted"
    if not allocations:
        for approval in db.scalars(
            select(Approval)
            .where(Approval.request_id == execution.request_id)
            .order_by(Approval.created_at.desc())
        ).all():
            if any(
                line["execution_id"] == execution.id and line["revision"] == execution.revision
                for line in approval.snapshot["lines"]
            ):
                approval_state = approval.status
                break
    value.update(
        allocated=str(allocated),
        unallocated=str(execution.quantity - execution.cancelled_quantity - allocated),
        shipped=str(shipped),
        delivered=str(delivered),
        open_claims=str(claims),
        state="cancelled"
        if execution.cancelled_quantity == execution.quantity
        else "fulfilled"
        if delivered + execution.cancelled_quantity == execution.quantity and claims == 0
        else "partial"
        if delivered > 0
        else "in_progress"
        if shipped > 0
        else "allocated"
        if allocated > 0
        else approval_state,
        funding=funding_for_execution(db, execution),
        allocations=[allocation_view(db, row) for row in allocations],
    )
    return value


def check_approval(db, approval: Approval, execution: Execution) -> dict:
    if approval.status != "approved" or approval.request_id != execution.request_id:
        error("APPROVAL_REQUIRED", "Позиция должна быть согласована руководителем")
    line = next((row for row in approval.snapshot["lines"] if row["execution_id"] == execution.id), None)
    if (
        not line
        or line["revision"] != execution.revision
        or dec(line["quantity"]) != execution.quantity - execution.cancelled_quantity
        or line.get("quote_id") != execution.quote_id
        or line.get("quote_item_id") != execution.quote_item_id
    ):
        error("APPROVAL_STALE", "Состав изменён после согласования. Требуется новое решение", 409)
    return line


def approval_snapshot(db, executions: list[Execution], reviewer_id: str) -> dict:
    lines = []
    for execution in executions:
        if execution.cancelled_quantity == execution.quantity:
            error("EXECUTION_CANCELLED", "Позиция отменена")
        quote = check_execution_quote(db, execution)
        proposal = db.get(CommercialDocument, execution.proposal_id)
        calculation = db.get(Calculation, proposal.calculation_id)
        threshold = dec(calculation.snapshot["profile"].get("funding_ratio", "1"))
        funding = funding_for_execution(db, execution)
        lines.append(
            {
                "execution_id": execution.id,
                "revision": execution.revision,
                "quote_id": execution.quote_id,
                "quote_item_id": execution.quote_item_id,
                "quote_revision": quote.revision if execution.quote_id else None,
                "quantity": str(execution.quantity - execution.cancelled_quantity),
                "unit": execution.unit,
                "funding_ratio": str(threshold),
                "funding": funding,
                "profile_id": calculation.profile_id,
            }
        )
    return {"lines": lines, "reviewer_id": reviewer_id}


@router.post("/executions/{execution_id}/cancel")
def cancel_execution(execution_id: str, data: ExecutionCancelIn, db: DB, user: Actor):
    execution = db.get(Execution, execution_id)
    if not execution:
        error("NOT_FOUND", "Исполнение не найдено", 404)
    check_request(db, user, execution.request_id, "documents.write")

    def operation():
        commerce_locks(db, [execution])
        advisory(db, f"request-commerce:{execution.request_id}")
        current = lock(db, Execution, execution_id)
        check_version(current, data.version)
        if current.cancelled_quantity == current.quantity:
            error("ALREADY_CANCELLED", "Исполнение уже отменено", 409)

        invoices = db.scalars(
            select(CommercialDocument).where(
                CommercialDocument.request_id == current.request_id,
                CommercialDocument.kind == "invoice",
                CommercialDocument.status != "cancelled",
            )
        ).all()
        for inv in invoices:
            if any(line.get("execution_id") == current.id for line in inv.snapshot.get("lines", [])):
                error(
                    "HAS_INVOICE",
                    "Нельзя отменить или пересмотреть исполнение, по которому выставлен счёт. Сначала отмените счёт.",
                )

        allocs = db.scalars(
            select(WaveAllocation).where(
                WaveAllocation.execution_id == current.id, WaveAllocation.active.is_(True)
            )
        ).all()
        if allocs:
            error(
                "HAS_ALLOCATION",
                "Нельзя отменить или пересмотреть исполнение, которое распределено в волны. Сначала отмените распределения.",
            )

        current.cancelled_quantity = current.quantity
        current.cancel_reason = data.reason
        current.version += 1
        db.flush()

        audit(db, user, "execution", current.id, "cancel", reason=data.reason)
        recalculate_funding(db, current.request_id)
        recalculate_fulfillment(db, current.request_id)

        return execution_view(db, current)

    return idem(
        db,
        user,
        data.idempotency_key,
        f"cancel-execution:{execution_id}",
        data.model_dump(mode="json"),
        operation,
    )


@router.post("/executions/{execution_id}/revise")
def revise_execution(execution_id: str, data: ExecutionReviseIn, db: DB, user: Actor):
    execution = db.get(Execution, execution_id)
    if not execution:
        error("NOT_FOUND", "Исполнение не найдено", 404)
    check_request(db, user, execution.request_id, "documents.write")

    def operation():
        commerce_locks(db, [execution])
        advisory(db, f"request-commerce:{execution.request_id}")
        current = lock(db, Execution, execution_id)
        check_version(current, data.version)

        if current.cancelled_quantity == current.quantity:
            error("ALREADY_CANCELLED", "Нельзя редактировать отменённое исполнение")

        invoices = db.scalars(
            select(CommercialDocument).where(
                CommercialDocument.request_id == current.request_id,
                CommercialDocument.kind == "invoice",
                CommercialDocument.status != "cancelled",
            )
        ).all()
        for inv in invoices:
            if any(line.get("execution_id") == current.id for line in inv.snapshot.get("lines", [])):
                error(
                    "HAS_INVOICE",
                    "Нельзя отменить или пересмотреть исполнение, по которому выставлен счёт. Сначала отмените счёт.",
                )

        allocs = db.scalars(
            select(WaveAllocation).where(
                WaveAllocation.execution_id == current.id, WaveAllocation.active.is_(True)
            )
        ).all()
        if allocs:
            error(
                "HAS_ALLOCATION",
                "Нельзя отменить или пересмотреть исполнение, которое распределено в волны. Сначала отмените распределения.",
            )

        quote = check_execution_quote(db, current)
        item = db.get(RequestItem, current.item_id) if current.quote_item_id else check_quote(db, quote)
        product = db.get(Product, quote.product_id) if current.quote_id else None
        from .documents import partial_amount, validate_quantity

        quantity = dec(data.quantity)
        if current.quote_item_id:
            if quantity <= 0 or quantity != quantity.to_integral_value():
                error("PACKING_QUANTITY", "Количество фасовок должно быть целым числом больше нуля")
        else:
            validate_quantity(quote, product, quantity, current.unit)
        if not item.quantity or not item.unit:
            error("DEMAND_INCOMPLETE", "Укажите согласованный объём потребности")
        calc = db.get(Calculation, db.get(CommercialDocument, current.proposal_id).calculation_id)
        source = next((line for line in calc.snapshot["lines"] if line["line_id"] == current.line_id), None)
        if source is None:
            error("EXECUTION_SOURCE", "Исходная строка принятого состава не найдена")

        existing = db.scalars(select(Execution).where(Execution.item_id == current.item_id)).all()
        accepted = sum(
            (
                convert(ex.quantity - ex.cancelled_quantity, ex.unit, item.unit)
                for ex in existing
                if ex.id != current.id
            ),
            Decimal("0"),
        )
        requested = convert(quantity, current.unit, item.unit)
        if not current.quote_item_id and accepted + requested > item.quantity:
            error("ACCEPTANCE_EXCEEDED", "Сумма принятых количеств превышает потребность")
        profile = calc.snapshot["profile"]
        if not profile.get("allow_partial_acceptance", True) and requested != (
            dec(source["quantity"]) if current.quote_item_id else item.quantity - accepted
        ):
            error("PARTIAL_ACCEPTANCE", "Профиль требует принятия полного остатка потребности")
        # Count only the latest member of each revision chain. Historical
        # snapshots remain intact without consuming the same quantity twice.
        superseded = {ex.previous_id for ex in existing if ex.previous_id}
        same_line = [
            ex.snapshot
            for ex in existing
            if ex.proposal_id == current.proposal_id
            and ex.line_id == current.line_id
            and ex.id != current.id
            and ex.id not in superseded
        ]
        part = partial_amount(source, quantity, same_line, profile)

        current.cancelled_quantity = current.quantity
        current.cancel_reason = data.reason
        current.version += 1
        new_execution = Execution(
            request_id=current.request_id,
            item_id=current.item_id,
            proposal_id=current.proposal_id,
            quote_id=current.quote_id,
            quote_item_id=current.quote_item_id,
            line_id=current.line_id,
            quantity=quantity,
            unit=current.unit,
            snapshot=part,
            acceptance_reason=data.reason,
            accepted_by=user.id,
            revision=current.revision + 1,
            previous_id=current.id,
        )
        db.add(new_execution)
        db.flush()

        audit(db, user, "execution", current.id, "cancel", reason=f"Пересмотр: {data.reason}")
        audit(
            db,
            user,
            "execution",
            new_execution.id,
            "revise",
            after=serialize(new_execution),
            reason=data.reason,
        )

        recalculate_funding(db, current.request_id)
        recalculate_fulfillment(db, current.request_id)

        return execution_view(db, new_execution)

    return idem(
        db,
        user,
        data.idempotency_key,
        f"revise-execution:{execution_id}",
        data.model_dump(mode="json"),
        operation,
    )


@router.get("/requests/{request_id}/executions")
def list_executions(request_id: str, db: DB, user: Actor):
    check_request(db, user, request_id)
    return {
        "items": [
            execution_view(db, row)
            for row in db.scalars(
                select(Execution).where(Execution.request_id == request_id).order_by(Execution.created_at)
            ).all()
        ]
    }


@router.get("/requests/{request_id}/approvals")
def list_approvals(request_id: str, db: DB, user: Actor):
    check_request(db, user, request_id)
    return {
        "items": [
            serialize(row)
            for row in db.scalars(
                select(Approval).where(Approval.request_id == request_id).order_by(Approval.created_at.desc())
            ).all()
        ]
    }


@router.post("/requests/{request_id}/approvals")
def submit_approval(request_id: str, data: ApprovalIn, db: DB, user: Actor):
    check_request(db, user, request_id, "approvals.submit")

    def operation():
        if len(set(data.execution_ids)) != len(data.execution_ids):
            error("DUPLICATE_EXECUTION", "Позиция указана повторно")
        executions = db.scalars(
            select(Execution)
            .where(Execution.request_id == request_id, Execution.id.in_(data.execution_ids))
            .order_by(Execution.id)
        ).all()
        if len(executions) != len(data.execution_ids):
            error("EXECUTION_NOT_FOUND", "Позиция исполнения не найдена", 404)
        commerce_locks(db, executions)
        reviewer = db.get(User, data.reviewer_id)
        if not reviewer or not reviewer.active:
            error("REVIEWER_REQUIRED", "Выберите действующего руководителя")
        check_request(db, reviewer, request_id, "approvals.decide")
        existing = db.scalars(
            select(Approval).where(
                Approval.request_id == request_id, Approval.status.in_(("pending", "approved"))
            )
        ).all()
        for approval in existing:
            for line in approval.snapshot["lines"]:
                current = next((row for row in executions if row.id == line["execution_id"]), None)
                if current and line["revision"] == current.revision:
                    error("ALREADY_SUBMITTED", "Эта редакция состава уже передана или согласована", 409)
        snapshot = approval_snapshot(db, executions, reviewer.id)
        obj = Approval(
            request_id=request_id,
            snapshot=snapshot,
            digest=digest(snapshot),
            submitted_by=user.id,
            reviewer_id=reviewer.id,
        )
        db.add(obj)
        db.flush()
        db.add(
            Task(
                title="Согласовать состав исполнения",
                entity_type="request",
                entity_id=request_id,
                assignee_id=reviewer.id,
                author_id=user.id,
                due_at=utcnow() + timedelta(days=1),
                priority="high",
                event_key=f"approval:{obj.id}",
            )
        )
        notify(
            db,
            reviewer.id,
            f"approval-submitted:{obj.id}",
            "Состав исполнения ожидает согласования",
            "request",
            request_id,
        )
        audit(db, user, "approval", obj.id, "submit", after=snapshot)
        return serialize(obj)

    return idem(
        db,
        user,
        data.idempotency_key,
        f"submit-approval:{request_id}",
        data.model_dump(mode="json"),
        operation,
    )


def approval_imports(executions: list[Execution]) -> bool:
    """Imports go to a wave; DAP and purchases in Russia need none."""
    return any((execution.snapshot or {}).get("calculation_type", "IMPORT") == "IMPORT" for execution in executions)


def wave_option(db, wave: Wave, *, new: bool = False) -> dict:
    supplier = db.get(Counterparty, wave.supplier_id) if wave.supplier_id else None
    week = f" · отправка: неделя {wave.departure_week}, {wave.departure_year}" if wave.departure_week else ""
    label = (f"Новая рабочая волна из плановой «{wave.number}»" if new
             else f"Рабочая · {wave.number}{week}")
    return {"id": wave.id, "label": label, "kind": wave.kind, "number": wave.number, "status": wave.status,
            "supplier_name": supplier.name if supplier else None, "new": new}


@router.get("/approvals/{approval_id}/waves")
def approval_waves(approval_id: str, db: DB, user: Actor):
    """Business remark of 28 October 2026: the waves the leader may send the approved orders to."""
    approval = db.get(Approval, approval_id)
    if not approval:
        error("NOT_FOUND", "Согласование не найдено", 404)
    req = check_request(db, user, approval.request_id, "approvals.decide")
    executions = [db.get(Execution, row["execution_id"]) for row in approval.snapshot["lines"]]
    if not approval_imports(executions):
        return {"items": [], "wave_id": None, "required": False}
    suppliers = set()
    for execution in executions:
        source = db.get(QuoteItem, execution.quote_item_id) if execution.quote_item_id else db.get(Quote, execution.quote_id)
        if source:
            suppliers.add(source.supplier_id)
    items = []
    for supplier_id in sorted(suppliers):
        items += [wave_option(db, row) for row in db.scalars(select(Wave).where(
            Wave.supplier_id == supplier_id, Wave.kind == "work", Wave.archived.is_(False),
            Wave.status.in_(OPEN_WAVES)).order_by(Wave.close_date, Wave.created_at, Wave.id))]
        if (plan := active_plan(db, supplier_id)) and plan.status in OPEN_WAVES:
            items.append(wave_option(db, plan, new=True))
    current = db.get(Wave, req.wave_id) if req.wave_id else None
    default = None
    if current and not current.archived and current.status in OPEN_WAVES:
        joined = open_working_wave(db, current) if current.kind == "plan" else current
        default = (joined or current).id
    return {"items": items, "wave_id": default if any(row["id"] == default for row in items) else None,
            "required": True}


@router.post("/approvals/{approval_id}/decision")
def decide_approval(approval_id: str, data: DecisionIn, db: DB, user: Actor):
    initial = db.get(Approval, approval_id)
    if not initial:
        error("NOT_FOUND", "Согласование не найдено", 404)
    req = check_request(db, user, initial.request_id, "approvals.decide")

    def operation():
        executions = [db.get(Execution, row["execution_id"]) for row in initial.snapshot["lines"]]
        commerce_locks(db, executions)
        approval = lock(db, Approval, approval_id)
        check_version(approval, data.version)
        if approval.status != "pending":
            error("APPROVAL_DECIDED", "Решение уже принято", 409)
        if data.decision != "approved" and len(data.reason.strip()) < 3:
            error("REASON_REQUIRED", "Возврат и отказ требуют причины", field="reason")
        if data.decision == "approved":
            current = approval_snapshot(db, executions, approval.reviewer_id)
            old_composition = [
                {key: value for key, value in row.items() if key != "funding"}
                for row in approval.snapshot["lines"]
            ]
            new_composition = [
                {key: value for key, value in row.items() if key != "funding"} for row in current["lines"]
            ]
            if old_composition != new_composition:
                error("APPROVAL_STALE", "Состав изменён после передачи. Направьте новую версию", 409)
            if req.sale_confirmed_at is None:
                req.sale_confirmed_at = utcnow()
                audit(
                    db,
                    user,
                    "request",
                    req.id,
                    "sale_confirmed",
                    after={"sale_confirmed_at": req.sale_confirmed_at},
                )
            for execution, approved_line in zip(executions, current["lines"], strict=True):
                execution.procurement_at = utcnow()
                execution.financing_deficit = dec(approved_line["funding"]["ratio"]) < dec(approved_line["funding_ratio"])
                execution.version += 1
            chosen = data.wave_id or req.wave_id
            if data.wave_id and not approval_imports(executions):
                error("WAVE_NOT_USED", "Волна указывается только для импортных позиций", field="wave_id")
            if chosen:
                for wave_id in sorted({value for value in (req.wave_id, chosen) if value}):
                    advisory(db, f"wave:{wave_id}")
                wave = lock(db, Wave, chosen) if db.get(Wave, chosen) else None
                if not wave:
                    error("WAVE_REQUIRED", "Волна не найдена", field="wave_id")
                usable(wave, field="wave_id")
                if wave.status not in OPEN_WAVES:
                    error("WAVE_CLOSED", "Для передачи в закупки выберите открытую волну", field="wave_id")
                # The leader's planned wave means a new working wave copied from it; the request's own
                # planned wave (no choice made) keeps joining the plan's open working wave.
                wave = working_wave(db, user, wave, f"Подтверждён заказ по заявке №{req.number}",
                                    new=bool(data.wave_id) and wave.kind == "plan")
                for execution in executions:
                    quote = check_execution_quote(db, execution)
                    if wave.supplier_id != quote.supplier_id:
                        error("WAVE_SUPPLIER", "Волна не соответствует поставщику подтверждённых позиций",
                              field="wave_id")
                    allocated = sum((row.quantity for row in db.scalars(select(WaveAllocation).where(
                        WaveAllocation.execution_id == execution.id, WaveAllocation.active.is_(True)
                    ))), Decimal("0"))
                    remaining = execution.quantity - execution.cancelled_quantity - allocated
                    if remaining > 0:
                        db.add(WaveAllocation(wave_id=wave.id, execution_id=execution.id,
                                              approval_id=approval.id, quantity=remaining))
                        audit(db, user, "execution", execution.id, "procurement_handoff",
                              after={"wave_id": wave.id, "quantity": str(remaining)})
                wave.version += 1
                if data.wave_id and req.wave_id != wave.id:
                    # Later calculations of the request share costs with the orders it now has in this wave.
                    before = {"wave_id": req.wave_id}
                    req.wave_id = wave.id
                    audit(db, user, "request", req.id, "assign_wave", before=before, after={"wave_id": wave.id},
                          reason=f"Волна указана руководителем при согласовании: {wave.number}")
            req.version += 1
            recalculate_fulfillment(db, req.id)
        approval.status, approval.reason = data.decision, data.reason
        approval.decided_by, approval.decided_at = user.id, utcnow()
        approval.version += 1
        task = db.scalar(select(Task).where(Task.event_key == f"approval:{approval.id}"))
        if task:
            task.status, task.result, task.completed_at = "completed", data.reason or data.decision, utcnow()
            task.version += 1
        notify(
            db,
            approval.submitted_by,
            f"approval-decision:{approval.id}",
            {
                "approved": "Состав исполнения согласован",
                "returned": "Состав возвращён на доработку",
                "rejected": "Состав исполнения отклонён",
            }[data.decision],
            "request",
            req.id,
        )
        audit(
            db,
            user,
            "approval",
            approval.id,
            "decision",
            after={"decision": data.decision, "digest": approval.digest,
                   **({"wave_id": req.wave_id} if data.decision == "approved" else {})},
            reason=data.reason,
        )
        return {**serialize(approval), "wave_id": req.wave_id if data.decision == "approved" else None}

    return idem(
        db,
        user,
        data.idempotency_key,
        f"approval-decision:{approval_id}",
        data.model_dump(mode="json"),
        operation,
    )


def wave_schedule(data, current: Wave | None = None) -> dict:
    result = {}
    for prefix in ("close", "departure", "arrival"):
        week = getattr(data, f"{prefix}_week")
        year = getattr(data, f"{prefix}_year")
        exact = getattr(data, f"{prefix}_date")
        if week is not None or year is not None:
            if week is None or year is None:
                error("WAVE_WEEK", "Укажите номер недели и год вместе", field=f"{prefix}_week")
            try:
                scheduled = date.fromisocalendar(year, week, 1)
            except ValueError:
                error("WAVE_WEEK", "В выбранном ISO-году нет такой недели", field=f"{prefix}_week")
            if exact is not None and exact.isocalendar()[:2] != (year, week):
                error("WAVE_WEEK", "Дата и номер ISO-недели не совпадают", field=f"{prefix}_week")
        elif exact is not None:
            scheduled = exact
            year, week, _ = exact.isocalendar()
        elif current:
            scheduled = getattr(current, f"{prefix}_date")
            year, week, _ = scheduled.isocalendar()
        else:
            error("WAVE_WEEK", "Укажите неделю и год планирования", field=f"{prefix}_week")
        result.update({f"{prefix}_week": week, f"{prefix}_year": year, f"{prefix}_date": scheduled})
    if not result["close_date"] <= result["departure_date"] <= result["arrival_date"]:
        error("WAVE_DATES", "Недели закрытия, отправления и прибытия должны идти по порядку")
    return result


def wave_order_lines(db, wave: Wave) -> dict[str, int]:
    """Positions ordered from the supplier for the wave (a plan: its working waves), counted by status."""
    from app.business.models import SupplierOrderLine

    waves = [wave.id] + (list(db.scalars(select(Wave.id).where(Wave.plan_id == wave.id))) if wave.kind == "plan" else [])
    counts: dict[str, int] = {}
    for status in db.scalars(select(SupplierOrderLine.status)
                             .join(WaveAllocation, WaveAllocation.execution_id == SupplierOrderLine.execution_id)
                             .where(WaveAllocation.wave_id.in_(waves), WaveAllocation.active.is_(True))):
        counts[status] = counts.get(status, 0) + 1
    return counts


def wave_view(db, user, wave: Wave) -> dict:
    accessible = select(CRMRequest.id).where(request_predicate(db, user))
    allocations = db.scalars(
        select(WaveAllocation)
        .join(Execution, Execution.id == WaveAllocation.execution_id)
        .where(WaveAllocation.wave_id == wave.id, WaveAllocation.active.is_(True), Execution.request_id.in_(accessible))
    ).all()
    supplier = db.get(Counterparty, wave.supplier_id) if wave.supplier_id else None
    value = serialize(wave)
    value.pop("budget_expenses", None)
    value.pop("budget_rates", None)
    forecasts = db.scalars(select(WaveForecast).where(WaveForecast.wave_id == wave.id).order_by(WaveForecast.created_at)).all()
    quantities = {}
    full_composition_visible = True
    # A plan's remainder counts the orders of its working waves that are still being assembled.
    sources = (select(Wave.id).where(Wave.plan_id == wave.id, Wave.archived.is_(False), Wave.status.in_(OPEN_WAVES))
               if wave.kind == "plan" else [wave.id])
    real_allocations = db.scalars(select(WaveAllocation).where(
        WaveAllocation.wave_id.in_(sources), WaveAllocation.active.is_(True))).all()
    for allocation in real_allocations:
        execution = db.get(Execution, allocation.execution_id)
        if not execution or execution.quantity <= execution.cancelled_quantity:
            continue
        if not has_request_permission(db, user, execution.request_id, "requests.read"):
            full_composition_visible = False
        group_id = (execution.snapshot.get("product_group") or {}).get("id")
        if not group_id and execution.quote_item_id:
            quote = db.get(QuoteItem, execution.quote_item_id)
            product = db.get(Nomenclature, quote.nomenclature_id) if quote else None
            group_id = product.product_group_id if product else None
        if group_id:
            quantities[group_id] = quantities.get(group_id, dec("0")) + allocation.quantity
        else:
            full_composition_visible = False
    value["forecasts"] = [{
        **serialize(row), "product_group_name": db.get(ProductGroup, row.product_group_id).name,
        "filled_quantity": str(quantities.get(row.product_group_id, dec("0"))) if full_composition_visible else None,
        "remaining_quantity": (str(max(dec("0"), row.target_quantity - quantities.get(row.product_group_id, dec("0"))))
                               if full_composition_visible else None),
    } for row in forecasts]
    for prefix in ("close", "departure", "arrival"):
        year, week, _ = getattr(wave, f"{prefix}_date").isocalendar()
        value[f"{prefix}_year"] = getattr(wave, f"{prefix}_year") or year
        value[f"{prefix}_week"] = getattr(wave, f"{prefix}_week") or week
    value["financial_summary"] = None
    if all(can(db, user, code) for code in ("finance.purchase.read", "finance.calculations.read")):
        from .financial import wave_financial_summary, wave_selections
        peers, _, _ = wave_selections(db, wave.id)
        finance_requests = {row["_request_id"] for row in peers if row.get("_request_id")}
        finance_requests.update(db.scalars(select(Execution.request_id).join(
            WaveAllocation, WaveAllocation.execution_id == Execution.id).where(
            WaveAllocation.wave_id == wave.id, WaveAllocation.active.is_(True))).all())
        if all(has_request_permission(db, user, request_id, "finance.purchase.read")
               and has_request_permission(db, user, request_id, "finance.calculations.read") for request_id in finance_requests):
            try:
                value["financial_summary"] = wave_financial_summary(db, wave.id)
                if not all(has_request_permission(db, user, request_id, "finance.profit.read") for request_id in finance_requests):
                    value["financial_summary"].pop("actual", None)
            except DomainError as exc:
                value["financial_summary"] = {"status": "error", "error": exc.message, "code": exc.code,
                                              "provisional": True, "allocations": []}
    if value["financial_summary"] is not None:
        value["budget_expenses"] = wave.budget_expenses
        value["budget_rates"] = wave.budget_rates
    else:
        for row in value["forecasts"]:
            row.pop("unit_price_rub", None)
    if wave.kind == "plan":
        value["working_waves"] = [
            {"id": row.id, "number": row.number, "status": row.status, "archived": row.archived}
            for row in db.scalars(select(Wave).where(Wave.plan_id == wave.id).order_by(Wave.created_at, Wave.id))
        ]
    plan = db.get(Wave, wave.plan_id) if wave.plan_id else None
    value["order_lines"] = wave_order_lines(db, wave)
    return {**value, "supplier_name": supplier.name if supplier else None,
            "plan_number": plan.number if plan else None,
            "allocations": [allocation_view(db, row) for row in allocations]}


@router.get("/waves")
def list_waves(db: DB, user: Actor, archived: bool = False, kind: Literal["plan", "work"] | None = None,
               open_only: bool = Query(False, alias="open"), q: str = ""):
    """Waves in use by default; the archive is a separate list and never offered for new work."""
    require_permission(db, user, "requests.read")
    query = select(Wave).where(Wave.archived.is_(archived))
    if kind:
        query = query.where(Wave.kind == kind)
    if open_only:
        query = query.where(Wave.status.in_(OPEN_WAVES))
    if text := q.strip():
        pattern = f"%{text}%"
        suppliers = select(Counterparty.id).where(Counterparty.name.ilike(pattern))
        query = query.where(or_(Wave.number.ilike(pattern), Wave.route.ilike(pattern), Wave.supplier_id.in_(suppliers)))
    return {"items": [wave_view(db, user, row) for row in db.scalars(query.order_by(Wave.departure_date.desc())).all()]}


@router.get("/waves/{wave_id}")
def get_wave(wave_id: str, db: DB, user: Actor):
    require_permission(db, user, "requests.read")
    wave = db.get(Wave, wave_id)
    if not wave:
        error("NOT_FOUND", "Волна не найдена", 404)
    return wave_view(db, user, wave)


@router.post("/waves")
def create_wave(data: WaveIn, db: DB, user: Actor):
    require_permission(db, user, "waves.write")

    def operation():
        supplier = db.get(Counterparty, data.supplier_id)
        if not supplier or supplier.archived or supplier.kind not in ("supplier", "both"):
            error("SUPPLIER_REQUIRED", "Поставщик волны не найден", field="supplier_id")
        schedule = wave_schedule(data)
        owner = db.get(User, data.owner_id)
        if not owner or not owner.active:
            error("OWNER_REQUIRED", "Выберите действующего ответственного")
        advisory(db, f"wave-supplier:{supplier.id}")
        if data.kind == "plan" and (current := active_plan(db, supplier.id)):
            error("WAVE_PLAN_EXISTS", f"У поставщика уже есть плановая волна «{current.number}». "
                  "Уберите её в архив, чтобы завести новую", 409, field="supplier_id")
        number = data.number.strip()
        if not number:
            prefix = supplier.name[:65].strip()
            existing = db.scalars(select(Wave.number).where(Wave.supplier_id == supplier.id)).all()
            sequence = max((int(value[len(prefix) + 1:]) for value in existing
                            if value.startswith(prefix + " ") and value[len(prefix) + 1:].isdigit()), default=0) + 1
            number = f"{prefix} {sequence}"
        advisory(db, f"wave-number:{number}")
        if db.scalar(select(Wave.id).where(Wave.number == number)):
            error("WAVE_NUMBER", "Номер волны уже существует", 409)
        obj = Wave(**{**data.model_dump(exclude={"idempotency_key"}), "number": number, **schedule})
        db.add(obj)
        db.flush()
        audit(db, user, "wave", obj.id, "create", after=serialize(obj))
        return wave_view(db, user, obj)

    return idem(db, user, data.idempotency_key, "create-wave", data.model_dump(mode="json"), operation)


@router.patch("/waves/{wave_id}")
def update_wave(wave_id: str, data: WaveUpdate, db: DB, user: Actor):
    require_permission(db, user, "waves.write")

    def operation():
        advisory(db, f"wave:{wave_id}")
        wave = usable(lock(db, Wave, wave_id))
        check_version(wave, data.version)
        allocations = db.scalars(
            select(WaveAllocation).where(WaveAllocation.wave_id == wave.id, WaveAllocation.active.is_(True))
        ).all()
        for allocation in allocations:
            check_request(db, user, db.get(Execution, allocation.execution_id).request_id, "waves.write")
        before = serialize(wave)
        if data.status and data.status != wave.status:
            # A wave in work keeps its state until its orders move on or a leader moves it.
            if not can(db, user, "approvals.decide"):
                error("WAVE_STATUS_LEADER", "Состояние волны меняет руководитель", 403, field="status")
            if wave.kind == "plan":
                error("WAVE_PLAN_STATUS", "Плановая волна не отправляется: этапы поставки ведутся в рабочих волнах. "
                      "Ненужную плановую волну уберите в архив", field="status")
            if data.status not in WAVE_TRANSITIONS[wave.status]:
                error("WAVE_TRANSITION", "Этот переход состояния волны недопустим")
            if data.status in ("closed", "shipped", "arrived", "completed") and db.scalar(
                select(WaveForecast.id).where(WaveForecast.wave_id == wave.id, WaveForecast.active.is_(True))
            ):
                error("WAVE_FORECAST_ACTIVE", "Отключите плановые позиции перед закрытием состава волны")
            if data.status == "cancelled":
                if any(event_totals(db, row.id)["shipped"] > 0 for row in allocations):
                    error(
                        "WAVE_ALREADY_SHIPPED",
                        "Отправленную волну нельзя отменить без корректировки фактических событий",
                    )
                for allocation in allocations:
                    allocation.active = False
                    allocation.reason = data.reason
                    allocation.version += 1
            if data.status in ("shipped", "arrived", "completed"):
                if not allocations:
                    error("WAVE_EMPTY", "Волна не содержит согласованных позиций")
                event_kind = {"shipped": "shipped", "arrived": "arrived", "completed": "delivered"}[
                    data.status
                ]
                for allocation in allocations:
                    execution = db.get(Execution, allocation.execution_id)
                    check_approval(db, db.get(Approval, allocation.approval_id), execution)
                    totals = event_totals(db, allocation.id)
                    if totals[event_kind] != allocation.quantity or (
                        data.status == "completed" and totals["claim_opened"] != totals["claim_resolved"]
                    ):
                        error(
                            "WAVE_INCOMPLETE",
                            "Не весь объём подтверждён фактическими событиями или есть незакрытая претензия",
                        )
            wave.status = data.status
        for field, value in wave_schedule(data, wave).items():
            setattr(wave, field, value)
        wave.version += 1
        for allocation in allocations:
            execution = db.get(Execution, allocation.execution_id)
            req = db.get(CRMRequest, execution.request_id)
            notify(
                db,
                req.owner_id,
                f"wave-change:{wave.id}:{wave.version}:{req.id}",
                "Изменены сроки или состояние волны поставки",
                "request",
                req.id,
            )
        audit(db, user, "wave", wave.id, "update", before=before, after=serialize(wave), reason=data.reason)
        return wave_view(db, user, wave)

    return idem(
        db, user, data.idempotency_key, f"update-wave:{wave_id}", data.model_dump(mode="json"), operation
    )


@router.post("/waves/{wave_id}/archive")
def archive_wave(wave_id: str, data: WaveArchiveIn, db: DB, user: Actor):
    require_permission(db, user, "waves.write")

    def operation():
        initial = db.get(Wave, wave_id)
        if not initial:
            error("NOT_FOUND", "Волна не найдена", 404)
        advisory(db, f"wave-supplier:{initial.supplier_id}")
        advisory(db, f"wave:{wave_id}")
        wave = lock(db, Wave, wave_id)
        check_version(wave, data.version)
        allocations = db.scalars(
            select(WaveAllocation).where(WaveAllocation.wave_id == wave.id, WaveAllocation.active.is_(True))
        ).all()
        for allocation in allocations:
            check_request(db, user, db.get(Execution, allocation.execution_id).request_id, "waves.write")
        if wave.archived == data.archived:
            error("WAVE_ARCHIVE_STATE", "Волна уже в архиве" if wave.archived else "Волна не в архиве", 409)
        if data.archived and allocations and wave.status != "completed":
            error("WAVE_IN_PROGRESS", "В волне есть заказы в работе. Завершите или отмените волну, "
                  "затем уберите её в архив")
        if not data.archived and wave.kind == "plan" and (current := active_plan(db, wave.supplier_id)):
            error("WAVE_PLAN_EXISTS", f"У поставщика уже есть плановая волна «{current.number}». "
                  "Сначала уберите её в архив", 409)
        before = serialize(wave)
        wave.archived = data.archived
        wave.version += 1
        audit(db, user, "wave", wave.id, "archived" if data.archived else "restored",
              before=before, after=serialize(wave), reason=data.reason)
        return wave_view(db, user, wave)

    return idem(
        db, user, data.idempotency_key, f"archive-wave:{wave_id}", data.model_dump(mode="json"), operation
    )


@router.post("/waves/{wave_id}/complete")
def complete_wave(wave_id: str, data: WaveCompleteIn, db: DB, user: Actor):
    """«Поставка выполнена»: the leader closes the delivery; the wave and, for a plan, its working waves
    are completed and go to the archive."""
    require_permission(db, user, "waves.write")
    require_permission(db, user, "approvals.decide")

    def operation():
        initial = db.get(Wave, wave_id)
        if not initial:
            error("NOT_FOUND", "Волна не найдена", 404)
        advisory(db, f"wave-supplier:{initial.supplier_id}")
        advisory(db, f"wave:{wave_id}")
        wave = lock(db, Wave, wave_id)
        check_version(wave, data.version)
        if wave.archived:
            error("WAVE_ARCHIVE_STATE", "Волна уже в архиве", 409)
        if wave.status == "cancelled":
            error("WAVE_TRANSITION", "Отменённую волну нельзя отметить выполненной", 409)
        finish_wave(db, user, wave, data.reason)
        return wave_view(db, user, wave)

    return idem(db, user, data.idempotency_key, f"complete-wave:{wave_id}", data.model_dump(mode="json"), operation)


@router.post("/waves/{wave_id}/allocations")
def allocate_wave(wave_id: str, data: AllocateWaveIn, db: DB, user: Actor):
    initial = db.get(Execution, data.execution_id)
    if not initial:
        error("NOT_FOUND", "Позиция исполнения не найдена", 404)
    check_request(db, user, initial.request_id, "waves.write")

    def operation():
        commerce_locks(db, [initial])
        advisory(db, f"wave:{wave_id}")
        wave = usable(lock(db, Wave, wave_id))
        if wave.status not in OPEN_WAVES:
            error("WAVE_CLOSED", "Добавлять позиции можно только в открытую волну")
        execution = lock(db, Execution, initial.id)
        if wave.supplier_id:
            supplier_id = (db.get(QuoteItem, execution.quote_item_id).supplier_id
                           if execution.quote_item_id else db.get(Quote, execution.quote_id).supplier_id)
            if supplier_id != wave.supplier_id:
                error("WAVE_SUPPLIER", "Позиция относится к другому поставщику")
        approval = db.get(Approval, data.approval_id)
        if not approval:
            error("APPROVAL_REQUIRED", "Согласование не найдено")
        line = check_approval(db, approval, execution)
        check_execution_quote(db, execution)
        allocations = db.scalars(
            select(WaveAllocation).where(
                WaveAllocation.execution_id == execution.id, WaveAllocation.active.is_(True)
            )
        ).all()
        allocated = sum((row.quantity for row in allocations), Decimal("0"))
        if allocated + data.quantity > execution.quantity - execution.cancelled_quantity:
            error("WAVE_OVERALLOCATED", "Количество превышает нераспределённый согласованный остаток")
        wave = working_wave(db, user, wave, "Позиция добавлена в плановую волну")
        execution.procurement_at = execution.procurement_at or utcnow()
        obj = WaveAllocation(
            wave_id=wave.id, execution_id=execution.id, approval_id=approval.id, quantity=data.quantity
        )
        db.add(obj)
        db.flush()
        execution.financing_deficit = dec(funding_for_execution(db, execution)['ratio']) < dec(line['funding_ratio'])
        audit(db, user, "wave_allocation", obj.id, "allocate", after=serialize(obj))
        return allocation_view(db, obj)

    return idem(
        db, user, data.idempotency_key, f"allocate-wave:{wave_id}", data.model_dump(mode="json"), operation
    )


@router.post("/allocations/{allocation_id}/transfer")
def transfer_allocation(allocation_id: str, data: TransferIn, db: DB, user: Actor):
    initial = db.get(WaveAllocation, allocation_id)
    if not initial:
        error("NOT_FOUND", "Распределение не найдено", 404)
    execution = db.get(Execution, initial.execution_id)
    check_request(db, user, execution.request_id, "waves.write")

    def operation():
        commerce_locks(db, [execution])
        for wave_id in sorted({initial.wave_id, data.target_wave_id}):
            advisory(db, f"wave:{wave_id}")
        current = lock(db, WaveAllocation, allocation_id)
        check_version(current, data.version)
        target = lock(db, Wave, data.target_wave_id)
        if target.archived or target.kind != "work" or target.status not in OPEN_WAVES or target.id == current.wave_id:
            error("TARGET_WAVE", "Выберите другую открытую рабочую волну")
        if not current.active or event_totals(db, current.id)["shipped"] > 0:
            error("TRANSFER_SHIPPED", "Перенос отправленного или неактивного распределения запрещён")
        if data.quantity > current.quantity:
            error("TRANSFER_QUANTITY", "Переносимый объём превышает распределение")
        if any(amount != 0 for amount in event_totals(db, current.id).values()):
            error("TRANSFER_HAS_EVENTS", "До переноса скорректируйте зафиксированные события распределения")
        current.active = False
        current.reason = data.reason
        current.version += 1
        result = []
        for wave_id, quantity in (
            (target.id, data.quantity),
            (current.wave_id, current.quantity - data.quantity),
        ):
            if quantity > 0:
                obj = WaveAllocation(
                    wave_id=wave_id,
                    execution_id=current.execution_id,
                    approval_id=current.approval_id,
                    quantity=quantity,
                    previous_id=current.id,
                    reason=data.reason,
                )
                db.add(obj)
                db.flush()
                result.append(allocation_view(db, obj))
        audit(
            db,
            user,
            "wave_allocation",
            current.id,
            "transfer",
            after={"allocations": result},
            reason=data.reason,
        )
        return {"items": result}

    return idem(
        db,
        user,
        data.idempotency_key,
        f"transfer-allocation:{allocation_id}",
        data.model_dump(mode="json"),
        operation,
    )


@router.post("/allocations/{allocation_id}/events")
def record_event(allocation_id: str, data: FulfillmentIn, db: DB, user: Actor):
    initial = db.get(WaveAllocation, allocation_id)
    if not initial:
        error("NOT_FOUND", "Распределение не найдено", 404)
    initial_execution = db.get(Execution, initial.execution_id)
    check_request(db, user, initial_execution.request_id, "waves.write")

    def operation():
        commerce_locks(db, [initial_execution])
        advisory(db, f"wave:{initial.wave_id}")
        allocation = lock(db, WaveAllocation, allocation_id)
        execution = lock(db, Execution, allocation.execution_id)
        if not allocation.active:
            error("ALLOCATION_INACTIVE", "Распределение отменено или перенесено")
        approval = db.get(Approval, allocation.approval_id)
        line = check_approval(db, approval, execution)
        totals = event_totals(db, allocation.id)
        if data.kind in ("ordered", "shipped"):
            if data.kind == 'shipped' and dec(funding_for_execution(db, execution)["ratio"]) < dec(line["funding_ratio"]):
                error(
                    "FUNDING_DEFICIT",
                    "Отправка требует достаточного подтверждённого финансирования по профилю",
                )
            if data.kind == "ordered" or totals["ordered"] == 0:
                check_execution_quote(db, execution)
        if data.kind == "correction":
            original = db.get(FulfillmentEvent, data.correction_of) if data.correction_of else None
            if not original or original.allocation_id != allocation.id or original.kind == "correction":
                error("CORRECTION_SOURCE", "Выберите исходное событие этого распределения")
            corrections = db.scalars(
                select(FulfillmentEvent).where(FulfillmentEvent.correction_of == original.id)
            ).all()
            if data.quantity > original.quantity - sum((row.quantity for row in corrections), Decimal("0")):
                error("CORRECTION_EXCEEDED", "Корректировка превышает оставшийся объём события")
            totals[original.kind] -= data.quantity
        else:
            if data.correction_of:
                error(
                    "CORRECTION_KIND", "Ссылка на корректируемое событие допустима только для корректировки"
                )
            totals[data.kind] += data.quantity
        if any(
            totals[kind] < 0 or totals[kind] > allocation.quantity
            for kind in ("ordered", "ready", "shipped", "arrived", "delivered")
        ):
            error("EVENT_QUANTITY", "Объём события превышает согласованное распределение")
        if totals["delivered"] > totals["arrived"] or totals["arrived"] > totals["shipped"]:
            error("DELIVERY_EXCEEDED", "Нельзя передать больше прибывшего или принять больше отправленного")
        if totals["claim_resolved"] > totals["claim_opened"]:
            error("CLAIM_EXCEEDED", "Нельзя закрыть больше заявленных претензий")
        obj = FulfillmentEvent(
            allocation_id=allocation.id, author_id=user.id, **data.model_dump(exclude={"idempotency_key"})
        )
        db.add(obj)
        db.flush()
        audit(db, user, "fulfillment_event", obj.id, data.kind, after=serialize(obj), reason=data.reason)
        recalculate_fulfillment(db, execution.request_id, wave_id=allocation.wave_id)
        return allocation_view(db, allocation)

    return idem(
        db,
        user,
        data.idempotency_key,
        f"fulfillment-event:{allocation_id}",
        data.model_dump(mode="json"),
        operation,
    )


def check_wave_finances(db, user, wave):
    require_permission(db, user, "waves.write")
    for permission in ("finance.purchase.read", "finance.calculations.read"):
        require_permission(db, user, permission)
        for request_id in db.scalars(select(Execution.request_id).join(
            WaveAllocation, WaveAllocation.execution_id == Execution.id).where(
            WaveAllocation.wave_id == wave.id, WaveAllocation.active.is_(True))).unique():
            check_request(db, user, request_id, permission)


@router.put("/waves/{wave_id}/forecasts")
def save_wave_forecast(wave_id: str, data: WaveForecastIn, db: DB, user: Actor):
    def operation():
        advisory(db, f"wave:{wave_id}")
        wave = usable(lock(db, Wave, wave_id))
        check_wave_finances(db, user, wave)
        check_version(wave, data.version)
        if wave.status not in OPEN_WAVES:
            error("WAVE_CLOSED", "Прогноз можно изменить только в открытой волне")
        group = db.get(ProductGroup, data.product_group_id)
        if not group or not group.active:
            error("PRODUCT_GROUP_REQUIRED", "Выберите действующую товарную группу")
        row = db.scalar(select(WaveForecast).where(WaveForecast.wave_id == wave.id,
                                                   WaveForecast.product_group_id == group.id))
        # A working wave holds real orders only; a forecast left from before can just be switched off.
        if wave.kind == "work" and (data.active or not row):
            error("WAVE_FORECAST_KIND", "Плановый объём задаётся в плановой волне поставщика. "
                  "В рабочей волне только реальные заказы", field="product_group_id")
        before = serialize(row) if row else None
        values = data.model_dump(exclude={"idempotency_key", "version"})
        if row:
            for field, value in values.items():
                setattr(row, field, value)
            row.author_id = user.id
            row.version += 1
        else:
            row = WaveForecast(wave_id=wave.id, author_id=user.id, **values)
            db.add(row)
        wave.version += 1
        db.flush()
        from .financial import wave_financial_summary
        wave_financial_summary(db, wave.id)
        audit(db, user, "wave_forecast", row.id, "update" if before else "create", before=before,
              after=serialize(row), reason=data.reason)
        return wave_view(db, user, wave)
    return idem(db, user, data.idempotency_key, f"wave-forecast:{wave_id}", data.model_dump(mode="json"), operation)


def plan_requests(db, user, wave: Wave) -> list[CRMRequest]:
    """Requests still planned in the plan of a working wave: candidates to gather into the working wave."""
    if wave.kind != "work" or not wave.plan_id:
        return []
    return list(db.scalars(select(CRMRequest).where(
        CRMRequest.wave_id == wave.plan_id, CRMRequest.archived.is_(False), CRMRequest.is_test.is_(False),
        CRMRequest.commercial_stage != "closed_lost", request_predicate(db, user))
        .order_by(CRMRequest.created_at, CRMRequest.id)))


@router.get("/waves/{wave_id}/plan-requests")
def wave_plan_requests(wave_id: str, db: DB, user: Actor):
    """Business remark of 22 October 2026: when the first order of a plan goes to work, the other requests of
    the plan are gathered into the working wave so that their calculations share its costs with its orders."""
    require_permission(db, user, "requests.read")
    wave = db.get(Wave, wave_id)
    if not wave:
        error("NOT_FOUND", "Волна не найдена", 404)
    rows = plan_requests(db, user, wave)
    clients = {row.id: row for row in db.scalars(select(Counterparty).where(
        Counterparty.id.in_([request.client_id for request in rows])))}
    owners = {row.id: row for row in db.scalars(select(User).where(User.id.in_([request.owner_id for request in rows])))}
    items = []
    for request in rows:
        latest = db.scalar(select(Calculation).where(Calculation.request_id == request.id)
                           .order_by(Calculation.created_at.desc(), Calculation.id.desc()).limit(1))
        proposals = db.scalar(select(func.count(CommercialDocument.id)).where(
            CommercialDocument.request_id == request.id, CommercialDocument.kind == "proposal",
            CommercialDocument.status.in_(("issued", "sent", "accepted")))) or 0
        orders = db.scalar(select(func.count(WaveAllocation.id)).join(
            Execution, Execution.id == WaveAllocation.execution_id).where(
            WaveAllocation.wave_id == wave.id, WaveAllocation.active.is_(True),
            Execution.request_id == request.id)) or 0
        items.append({"id": request.id, "number": request.number, "title": request.title, "orders": orders,
                      "client_name": clients[request.client_id].name if request.client_id in clients else None,
                      "owner_name": owners[request.owner_id].name if request.owner_id in owners else None,
                      "commercial_stage": request.commercial_stage, "version": request.version,
                      "calculation_number": latest.snapshot.get("calculation_number") if latest else None,
                      "proposals": proposals})
    return {"items": items, "plan_id": wave.plan_id}


@router.post("/waves/{wave_id}/take-plan-requests")
def take_plan_requests(wave_id: str, data: WaveTakeRequestsIn, db: DB, user: Actor):
    """Moves chosen requests of the plan into its open working wave; their next calculation shares the costs of
    the working wave with its orders and with the rest of the plan, as the wave itself counts them."""
    def operation():
        advisory(db, f"wave:{wave_id}")
        wave = usable(lock(db, Wave, wave_id))
        check_version(wave, data.version)
        if wave.kind != "work" or not wave.plan_id:
            error("WAVE_KIND", "Заявки плановой волны переносятся в её рабочую волну", field="wave_id")
        if wave.status not in OPEN_WAVES:
            error("WAVE_CLOSED", "Перенести заявки можно в рабочую волну, которая ещё комплектуется")
        moved = []
        for request_id in sorted(set(data.request_ids)):
            require_permission(db, user, "waves.write", request_id)
            advisory(db, f"request-commerce:{request_id}")
            request = lock(db, CRMRequest, request_id)
            check_request(db, user, request_id)
            if request.wave_id != wave.plan_id:
                error("REQUEST_WAVE", f"Заявка №{request.number} уже не в плановой волне этой рабочей волны",
                      field="request_ids")
            before = {"wave_id": request.wave_id}
            request.wave_id = wave.id
            request.version += 1
            audit(db, user, "request", request.id, "assign_wave", before=before, after={"wave_id": wave.id},
                  reason=data.reason)
            moved.append(request.number)
        wave.version += 1
        db.flush()
        return {"moved": moved, "wave": wave_view(db, user, wave)}
    return idem(db, user, data.idempotency_key, f"wave-take-requests:{wave_id}", data.model_dump(mode="json"),
                operation)


@router.post("/waves/{wave_id}/forecasts/{forecast_id}/remove")
def remove_wave_forecast(wave_id: str, forecast_id: str, data: WaveForecastRemoveIn, db: DB, user: Actor):
    """Business remark of 22 October 2026: the planned volume of a group is taken off the wave.

    The row stays in the history switched off; calculations by the wave no longer share costs with it.
    """
    def operation():
        advisory(db, f"wave:{wave_id}")
        wave = usable(lock(db, Wave, wave_id))
        check_wave_finances(db, user, wave)
        check_version(wave, data.version)
        row = db.get(WaveForecast, forecast_id)
        if not row or row.wave_id != wave.id:
            error("NOT_FOUND", "Плановый объём не найден", 404)
        if not row.active:
            error("WAVE_FORECAST_REMOVED", "Плановый объём уже убран", 409)
        row = lock(db, WaveForecast, row.id)
        before = serialize(row)
        row.active = False
        row.author_id = user.id
        row.reason = data.reason
        row.version += 1
        wave.version += 1
        db.flush()
        audit(db, user, "wave_forecast", row.id, "removed", before=before, after=serialize(row), reason=data.reason)
        return wave_view(db, user, wave)
    return idem(db, user, data.idempotency_key, f"wave-forecast-remove:{forecast_id}", data.model_dump(mode="json"),
                operation)


@router.put("/waves/{wave_id}/budget")
def save_wave_budget(wave_id: str, data: WaveBudgetIn, db: DB, user: Actor):
    def operation():
        advisory(db, f"wave:{wave_id}")
        wave = usable(lock(db, Wave, wave_id))
        check_wave_finances(db, user, wave)
        check_version(wave, data.version)
        if wave.status not in OPEN_WAVES:
            error("WAVE_CLOSED", "Бюджет можно изменить только в открытой волне")
        from .models import CalculationProfile
        profile = db.get(CalculationProfile, data.profile_id)
        if not profile or profile.status != "published" or profile.definition.get("methodology") != "itemized_v2":
            error("PROFILE_REQUIRED", "Выберите опубликованный профиль новой методики")
        if any(row.scope != "WAVE" for row in data.expenses):
            error("WAVE_BUDGET_SCOPE", "Бюджет волны содержит только общие расходы")
        before = {"expenses": wave.budget_expenses, "rates": wave.budget_rates, "profile_id": wave.budget_profile_id}
        wave.budget_expenses = [row.model_dump(mode="json") for row in data.expenses]
        wave.budget_rates = [row.model_dump(mode="json") for row in data.rates] or profile.definition.get("exchange_rates", [])
        wave.budget_profile_id = profile.id
        wave.version += 1
        from .financial import wave_financial_summary
        wave_financial_summary(db, wave.id)  # Validate allocation, FX and forecast bases before committing.
        audit(db, user, "wave", wave.id, "budget", before=before,
              after={"expenses": wave.budget_expenses, "rates": wave.budget_rates, "profile_id": profile.id}, reason=data.reason)
        return wave_view(db, user, wave)
    return idem(db, user, data.idempotency_key, f"wave-budget:{wave_id}", data.model_dump(mode="json"), operation)
