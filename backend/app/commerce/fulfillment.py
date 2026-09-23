from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter
from sqlalchemy import select

from app.commerce.recalculate import recalculate_fulfillment
from app.core.db import utcnow
from app.core.errors import error
from app.core.models import User
from app.core.security import check_request, request_predicate, require_permission
from app.core.service import advisory, audit, check_version, idem, lock, notify, serialize
from app.crm.models import Request as CRMRequest
from app.crm.models import Task

from .calculator import dec, digest
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
)
from .payments import funding_for_execution
from .procurement import DB, Actor, check_quote
from .recalculate import recalculate_funding
from .schemas import (
    AllocateWaveIn,
    ApprovalIn,
    DecisionIn,
    ExecutionCancelIn,
    ExecutionReviseIn,
    FulfillmentIn,
    TransferIn,
    WaveIn,
    WaveUpdate,
)

router = APIRouter(tags=["Согласования и поставки"])
OPEN_WAVES = {"planned", "assembling"}
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
    value.update(
        allocated=str(allocated),
        unallocated=str(execution.quantity - execution.cancelled_quantity - allocated),
        shipped=str(shipped),
        delivered=str(delivered),
        open_claims=str(claims),
        state="fulfilled"
        if delivered + execution.cancelled_quantity == execution.quantity and claims == 0
        else "partial"
        if delivered > 0
        else "in_progress"
        if shipped > 0
        else "allocated"
        if allocated > 0
        else "not_submitted",
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
        or line["quote_id"] != execution.quote_id
    ):
        error("APPROVAL_STALE", "Состав изменён после согласования. Требуется новое решение", 409)
    return line


def approval_snapshot(db, executions: list[Execution], reviewer_id: str) -> dict:
    lines = []
    for execution in executions:
        if execution.cancelled_quantity == execution.quantity:
            error("EXECUTION_CANCELLED", "Позиция отменена")
        quote = db.get(Quote, execution.quote_id)
        check_quote(db, quote)
        proposal = db.get(CommercialDocument, execution.proposal_id)
        calculation = db.get(Calculation, proposal.calculation_id)
        threshold = dec(calculation.snapshot["profile"]["funding_ratio"])
        funding = funding_for_execution(db, execution)
        if dec(funding["ratio"]) < threshold:
            error("FUNDING_REQUIRED", "Подтверждённой оплаты недостаточно для порога настроенного профиля")
        lines.append(
            {
                "execution_id": execution.id,
                "revision": execution.revision,
                "quote_id": execution.quote_id,
                "quote_revision": quote.revision,
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
                CommercialDocument.status != "cancelled"
            )
        ).all()
        for inv in invoices:
            if any(line.get("execution_id") == current.id for line in inv.snapshot.get("lines", [])):
                error("HAS_INVOICE", "Нельзя отменить или пересмотреть исполнение, по которому выставлен счёт. Сначала отмените счёт.")

        allocs = db.scalars(
            select(WaveAllocation).where(WaveAllocation.execution_id == current.id, WaveAllocation.active == True)
        ).all()
        if allocs:
            error("HAS_ALLOCATION", "Нельзя отменить или пересмотреть исполнение, которое распределено в волны. Сначала отмените распределения.")

            
        current.cancelled_quantity = current.quantity
        current.cancel_reason = data.reason
        current.version += 1
        db.flush()
        
        audit(db, user, "execution", current.id, "cancel", reason=data.reason)
        recalculate_funding(db, current.request_id)
        recalculate_fulfillment(db, current.request_id)
        
        return execution_view(db, current)
        
    return idem(db, user, data.idempotency_key, f"cancel-execution:{execution_id}", data.model_dump(mode="json"), operation)

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
                CommercialDocument.status != "cancelled"
            )
        ).all()
        for inv in invoices:
            if any(line.get("execution_id") == current.id for line in inv.snapshot.get("lines", [])):
                error("HAS_INVOICE", "Нельзя отменить или пересмотреть исполнение, по которому выставлен счёт. Сначала отмените счёт.")

        allocs = db.scalars(
            select(WaveAllocation).where(WaveAllocation.execution_id == current.id, WaveAllocation.active == True)
        ).all()
        if allocs:
            error("HAS_ALLOCATION", "Нельзя отменить или пересмотреть исполнение, которое распределено в волны. Сначала отмените распределения.")

            
        quote = db.get(Quote, current.quote_id)
        product = db.get(Product, quote.product_id) if quote else None
        
        # We need to validate new quantity
        from .documents import partial_amount, validate_quantity
        if quote and product:
            validate_quantity(quote, product, data.quantity, current.unit)
            
        # Revise means creating a NEW execution and cancelling the old one!
        # "новой редакции/отмены принятого исполнения с причиной, версией и ссылкой на исходный состав."
        
        current.cancelled_quantity = current.quantity
        current.cancel_reason = data.reason
        current.version += 1
        
        calc = db.get(Calculation, db.get(CommercialDocument, current.proposal_id).calculation_id)
        source = next((line for line in calc.snapshot["lines"] if line["line_id"] == current.line_id), None)
        
        existing = db.scalars(select(Execution).where(Execution.item_id == current.item_id)).all()
        same_line = [ex.snapshot for ex in existing if ex.proposal_id == current.proposal_id and ex.line_id == current.line_id and ex.id != current.id]
        
        part = partial_amount(source, data.quantity, same_line, calc.snapshot["profile"])
        
        new_execution = Execution(
            request_id=current.request_id,
            item_id=current.item_id,
            proposal_id=current.proposal_id,
            quote_id=current.quote_id,
            line_id=current.line_id,
            quantity=data.quantity,
            unit=current.unit,
            snapshot=part,
            acceptance_reason=current.acceptance_reason,
            accepted_by=current.accepted_by,
            revision=current.revision + 1,
            previous_id=current.id
        )
        db.add(new_execution)
        db.flush()
        
        audit(db, user, "execution", current.id, "cancel", reason=f"Пересмотр: {data.reason}")
        audit(db, user, "execution", new_execution.id, "revise", after=serialize(new_execution), reason=data.reason)
        
        recalculate_funding(db, current.request_id)
        recalculate_fulfillment(db, current.request_id)
        
        return execution_view(db, new_execution)
        
    return idem(db, user, data.idempotency_key, f"revise-execution:{execution_id}", data.model_dump(mode="json"), operation)

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
            after={"decision": data.decision, "digest": approval.digest},
            reason=data.reason,
        )
        return serialize(approval)

    return idem(
        db,
        user,
        data.idempotency_key,
        f"approval-decision:{approval_id}",
        data.model_dump(mode="json"),
        operation,
    )


def wave_view(db, user, wave: Wave) -> dict:
    accessible = select(CRMRequest.id).where(request_predicate(db, user))
    allocations = db.scalars(
        select(WaveAllocation)
        .join(Execution, Execution.id == WaveAllocation.execution_id)
        .where(WaveAllocation.wave_id == wave.id, Execution.request_id.in_(accessible))
    ).all()
    return {**serialize(wave), "allocations": [allocation_view(db, row) for row in allocations]}


@router.get("/waves")
def list_waves(db: DB, user: Actor):
    require_permission(db, user, "requests.read")
    return {
        "items": [
            wave_view(db, user, row)
            for row in db.scalars(select(Wave).order_by(Wave.departure_date.desc())).all()
        ]
    }


@router.post("/waves")
def create_wave(data: WaveIn, db: DB, user: Actor):
    require_permission(db, user, "waves.write")

    def operation():
        if not data.close_date <= data.departure_date <= data.arrival_date:
            error("WAVE_DATES", "Даты закрытия, отправления и прибытия должны идти по порядку")
        owner = db.get(User, data.owner_id)
        if not owner or not owner.active:
            error("OWNER_REQUIRED", "Выберите действующего ответственного")
        advisory(db, f"wave-number:{data.number}")
        if db.scalar(select(Wave.id).where(Wave.number == data.number)):
            error("WAVE_NUMBER", "Номер волны уже существует", 409)
        obj = Wave(**data.model_dump(exclude={"idempotency_key"}))
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
        wave = lock(db, Wave, wave_id)
        check_version(wave, data.version)
        allocations = db.scalars(
            select(WaveAllocation).where(WaveAllocation.wave_id == wave.id, WaveAllocation.active.is_(True))
        ).all()
        for allocation in allocations:
            check_request(db, user, db.get(Execution, allocation.execution_id).request_id, "waves.write")
        before = serialize(wave)
        if data.status and data.status != wave.status:
            if data.status not in WAVE_TRANSITIONS[wave.status]:
                error("WAVE_TRANSITION", "Этот переход состояния волны недопустим")
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
        for field in ("close_date", "departure_date", "arrival_date"):
            if getattr(data, field) is not None:
                setattr(wave, field, getattr(data, field))
        if not wave.close_date <= wave.departure_date <= wave.arrival_date:
            error("WAVE_DATES", "Даты закрытия, отправления и прибытия должны идти по порядку")
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


@router.post("/waves/{wave_id}/allocations")
def allocate_wave(wave_id: str, data: AllocateWaveIn, db: DB, user: Actor):
    initial = db.get(Execution, data.execution_id)
    if not initial:
        error("NOT_FOUND", "Позиция исполнения не найдена", 404)
    check_request(db, user, initial.request_id, "waves.write")

    def operation():
        commerce_locks(db, [initial])
        advisory(db, f"wave:{wave_id}")
        wave = lock(db, Wave, wave_id)
        if wave.status not in OPEN_WAVES:
            error("WAVE_CLOSED", "Добавлять позиции можно только в открытую волну")
        execution = lock(db, Execution, initial.id)
        approval = db.get(Approval, data.approval_id)
        if not approval:
            error("APPROVAL_REQUIRED", "Согласование не найдено")
        line = check_approval(db, approval, execution)
        check_quote(db, db.get(Quote, execution.quote_id))
        if dec(funding_for_execution(db, execution)["ratio"]) < dec(line["funding_ratio"]):
            error("FUNDING_DEFICIT", "Недостаточно подтверждённого финансирования")
        allocations = db.scalars(
            select(WaveAllocation).where(
                WaveAllocation.execution_id == execution.id, WaveAllocation.active.is_(True)
            )
        ).all()
        allocated = sum((row.quantity for row in allocations), Decimal("0"))
        if allocated + data.quantity > execution.quantity - execution.cancelled_quantity:
            error("WAVE_OVERALLOCATED", "Количество превышает нераспределённый согласованный остаток")
        obj = WaveAllocation(
            wave_id=wave.id, execution_id=execution.id, approval_id=approval.id, quantity=data.quantity
        )
        db.add(obj)
        db.flush()
        execution.financing_deficit = False
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
        if target.status not in OPEN_WAVES or target.id == current.wave_id:
            error("TARGET_WAVE", "Выберите другую открытую волну")
        if not current.active or event_totals(db, current.id)["shipped"] > 0:
            error("TRANSFER_SHIPPED", "Перенос отправленного или неактивного распределения запрещён")
        if data.quantity > current.quantity:
            error("TRANSFER_QUANTITY", "Переносимый объём превышает распределение")
        if db.scalar(select(FulfillmentEvent.id).where(FulfillmentEvent.allocation_id == current.id)):
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
            if dec(funding_for_execution(db, execution)["ratio"]) < dec(line["funding_ratio"]):
                error(
                    "FUNDING_DEFICIT",
                    "Заказ или отправка требуют достаточного подтверждённого финансирования",
                )
            if data.kind == "ordered" or totals["ordered"] == 0:
                check_quote(db, db.get(Quote, execution.quote_id))
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
        recalculate_fulfillment(db, execution.request_id)
        return allocation_view(db, allocation)

    return idem(
        db,
        user,
        data.idempotency_key,
        f"fulfillment-event:{allocation_id}",
        data.model_dump(mode="json"),
        operation,
    )
