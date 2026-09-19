from fastapi import APIRouter, Query
from sqlalchemy import select

from app.core.security import request_predicate, require_permission
from app.core.service import serialize
from app.crm.models import Request as CRMRequest

from . import documents, financial, fulfillment, payments, procurement
from .models import Approval, CommercialDocument, Execution, Payment
from .procurement import DB, Actor

router = APIRouter()
for domain in (procurement, financial, documents, payments, fulfillment):
    router.include_router(domain.router)


@router.get("/documents", tags=["Реестры"])
def document_registry(
    db: DB,
    user: Actor,
    kind: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    require_permission(db, user, "requests.read")
    query = select(CommercialDocument).where(
        CommercialDocument.request_id.in_(select(CRMRequest.id).where(request_predicate(db, user)))
    )
    if kind:
        query = query.where(CommercialDocument.kind == kind)
    items = db.scalars(
        query.order_by(CommercialDocument.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [
            {
                **documents.document_view(row),
                **(payments.invoice_balance(db, row) if row.kind == "invoice" else {}),
            }
            for row in items
        ],
        "page": page,
        "page_size": page_size,
    }


@router.get("/payments", tags=["Реестры"])
def payment_registry(
    db: DB, user: Actor, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100)
):
    require_permission(db, user, "requests.read")
    rows = db.scalars(
        select(Payment)
        .where(Payment.request_id.in_(select(CRMRequest.id).where(request_predicate(db, user))))
        .order_by(Payment.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"items": [payments.payment_view(db, row) for row in rows], "page": page, "page_size": page_size}


@router.get("/approvals", tags=["Реестры"])
def approval_registry(
    db: DB, user: Actor, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100)
):
    require_permission(db, user, "requests.read")
    rows = db.scalars(
        select(Approval)
        .where(Approval.request_id.in_(select(CRMRequest.id).where(request_predicate(db, user))))
        .order_by(Approval.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"items": [serialize(row) for row in rows], "page": page, "page_size": page_size}


@router.get("/executions", tags=["Реестры"])
def execution_registry(
    db: DB, user: Actor, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100)
):
    require_permission(db, user, "requests.read")
    rows = db.scalars(
        select(Execution)
        .where(Execution.request_id.in_(select(CRMRequest.id).where(request_predicate(db, user))))
        .order_by(Execution.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [fulfillment.execution_view(db, row) for row in rows],
        "page": page,
        "page_size": page_size,
    }
