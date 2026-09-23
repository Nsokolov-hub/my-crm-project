from decimal import Decimal

from sqlalchemy import select

from app.commerce.models import Approval, Execution, Wave, WaveAllocation
from sqlalchemy.orm import Session
from app.crm.models import Request as CRMRequest


def recalculate_funding(db: Session, request_id: str):
    from app.commerce.payments import funding_for_execution
    from app.core.service import notify
    
    req = db.get(CRMRequest, request_id)
    if not req:
        return
    
    executions = db.scalars(select(Execution).where(Execution.request_id == request_id)).all()
    approvals = db.scalars(
        select(Approval).where(Approval.request_id == request_id, Approval.status == "approved")
    ).all()
    
    for execution in executions:
        applicable = [
            app for app in approvals
            if any(row["execution_id"] == execution.id for row in app.snapshot["lines"])
        ]
        
        if applicable:
            threshold = max(
                Decimal(row["funding_ratio"])
                for app in applicable
                for row in app.snapshot["lines"]
                if row["execution_id"] == execution.id
            )
            ratio = Decimal(funding_for_execution(db, execution)["ratio"])
            deficit = ratio < threshold
            
            if execution.financing_deficit != deficit:
                execution.financing_deficit = deficit
                
                # If deficit appeared, notify
                if deficit:
                    for target_id in {req.owner_id, *{app.reviewer_id for app in applicable}}:
                        notify(
                            db,
                            target_id,
                            f"funding-deficit:{execution.id}",
                            {
                                "message": f"Дефицит финансирования по исполнению в заявке {req.number}",
                                "request_id": request_id,
                                "execution_id": execution.id,
                                "ratio": str(ratio),
                                "threshold": str(threshold)
                            }
                        )
                # If deficit disappeared? The requirement says "Дефицит должен исчезать после восстановления покрытия; уведомления получают фактические ответственные затронутых заявок."
                else:
                    for target_id in {req.owner_id, *{app.reviewer_id for app in applicable}}:
                        notify(
                            db,
                            target_id,
                            f"funding-restored:{execution.id}",
                            {
                                "message": f"Покрытие восстановлено по исполнению в заявке {req.number}",
                                "request_id": request_id,
                                "execution_id": execution.id
                            }
                        )

def recalculate_fulfillment(db: Session, request_id: str):
    from app.commerce.fulfillment import event_totals
    
    req = db.get(CRMRequest, request_id)
    if not req:
        return
    
    allocations = db.scalars(
        select(WaveAllocation)
        .join(Execution, WaveAllocation.execution_id == Execution.id)
        .where(Execution.request_id == request_id, WaveAllocation.active)
    ).all()
    
    waves = db.scalars(select(Wave).where(Wave.id.in_({a.wave_id for a in allocations}))).all()
    
    for wave in waves:
        wave_allocs = [a for a in allocations if a.wave_id == wave.id]
        wave_totals = {"ordered": Decimal("0"), "shipped": Decimal("0"), "arrived": Decimal("0"), "delivered": Decimal("0")}
        total_quantity = sum((a.quantity for a in wave_allocs), Decimal("0"))
        for a in wave_allocs:
            t = event_totals(db, a.id)
            for k in wave_totals:
                wave_totals[k] += t[k]
                
        if wave_totals["delivered"] >= total_quantity and total_quantity > 0:
            wave.status = "delivered"
        elif wave_totals["arrived"] > 0:
            wave.status = "arrived"
        elif wave_totals["shipped"] > 0:
            wave.status = "shipped"
        elif wave_totals["ordered"] > 0:
            wave.status = "ordered"
        else:
            wave.status = "planned"
            
    # Calculate request commercial_stage
    # If there are open claims -> claims
    # If all items delivered -> delivered
    # If partially delivered/shipped -> delivering
    # sale_confirmed is default if no delivery started yet.
    
    if req.commercial_stage in ("new", "clarification", "collecting_quotes", "calculation", "closed_lost"):
        return # Do not touch initial or closed stages
        
    total_claims_open = 0
    total_claims_resolved = 0
    total_ordered = Decimal("0")
    total_delivered = Decimal("0")
    total_quantity = sum((a.quantity for a in allocations), Decimal("0"))
    
    for a in allocations:
        t = event_totals(db, a.id)
        total_claims_open += t.get("claim_opened", Decimal("0"))
        total_claims_resolved += t.get("claim_resolved", Decimal("0"))
        total_ordered += t.get("ordered", Decimal("0"))
        total_delivered += t.get("delivered", Decimal("0"))
        
    if total_claims_open > total_claims_resolved:
        req.commercial_stage = "claims"
    elif total_delivered >= total_quantity and total_quantity > 0:
        req.commercial_stage = "delivered"
    elif total_ordered > 0 or total_delivered > 0:
        req.commercial_stage = "delivering"
    else:
        req.commercial_stage = "sale_confirmed"

