from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.commerce.models import Approval, Execution, Wave, WaveAllocation
from app.core.service import advisory, lock, notify
from app.crm.models import Request as CRMRequest


def recalculate_funding(db: Session, request_id: str):
    from app.commerce.payments import funding_for_execution

    req = db.get(CRMRequest, request_id)
    if not req:
        return

    executions = db.scalars(select(Execution).where(Execution.request_id == request_id)).all()
    approvals = db.scalars(
        select(Approval).where(Approval.request_id == request_id, Approval.status == "approved")
    ).all()
    for execution in executions:
        applicable = [
            approval
            for approval in approvals
            if any(row["execution_id"] == execution.id for row in approval.snapshot["lines"])
        ]
        deficit = False
        if applicable and execution.cancelled_quantity < execution.quantity:
            threshold = max(
                Decimal(row["funding_ratio"])
                for approval in applicable
                for row in approval.snapshot["lines"]
                if row["execution_id"] == execution.id
            )
            deficit = Decimal(funding_for_execution(db, execution)["ratio"]) < threshold
        if execution.financing_deficit == deficit:
            continue

        execution.financing_deficit = deficit
        execution.version += 1
        event = "funding-deficit" if deficit else "funding-restored"
        title = "Дефицит финансирования" if deficit else "Покрытие восстановлено"
        for target_id in {req.owner_id, *{approval.reviewer_id for approval in applicable}}:
            notify(
                db,
                target_id,
                f"{event}:{execution.id}:{execution.version}",
                f"{title} по исполнению в заявке {req.number}",
                "request",
                request_id,
            )


def recalculate_fulfillment(db: Session, request_id: str, *, wave_id: str | None = None):
    from app.commerce.fulfillment import event_totals

    req = db.get(CRMRequest, request_id)
    if not req:
        return

    wave_ids = (
        {wave_id}
        if wave_id is not None
        else set(
            db.scalars(
                select(WaveAllocation.wave_id)
                .join(Execution, WaveAllocation.execution_id == Execution.id)
                .where(Execution.request_id == request_id, WaveAllocation.active.is_(True))
            ).all()
        )
    )
    for wave_id in sorted(wave_ids):
        advisory(db, f"wave:{wave_id}")
        wave = lock(db, Wave, wave_id)
        # A shared wave must be checked against every request it contains.
        allocations = db.scalars(
            select(WaveAllocation).where(WaveAllocation.wave_id == wave.id, WaveAllocation.active.is_(True))
        ).all()
        totals = [(row, event_totals(db, row.id)) for row in allocations]
        shipped = bool(totals) and all(t["shipped"] == row.quantity for row, t in totals)
        arrived = bool(totals) and all(t["arrived"] == row.quantity for row, t in totals)
        completed = bool(totals) and all(
            t["delivered"] == row.quantity and t["claim_opened"] == t["claim_resolved"] for row, t in totals
        )

        # Progression is an explicit wave command. Corrections and claims can
        # invalidate a reached milestone, but never reopen a closed plan.
        status = wave.status
        if status == "completed" and not completed:
            status = "arrived" if arrived else "shipped" if shipped else "closed"
        elif status == "arrived" and not arrived:
            status = "shipped" if shipped else "closed"
        elif status == "shipped" and not shipped:
            status = "closed"
        if status != wave.status:
            wave.status = status
            wave.version += 1

    # Commercial and delivery states are separate. Delivery/claims are exposed
    # on executions; commercial stages stay within the request workflow.
    if req.sale_confirmed_at is not None and req.commercial_stage != "closed_lost":
        if req.commercial_stage != "sale_confirmed":
            req.commercial_stage = "sale_confirmed"
            req.version += 1
