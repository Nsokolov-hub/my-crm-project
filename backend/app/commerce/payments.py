from decimal import Decimal

from fastapi import APIRouter
from sqlalchemy import select

from app.core.db import utcnow
from app.core.errors import error
from app.core.security import check_request
from app.core.service import advisory, audit, check_version, idem, lock, notify, serialize

from .calculator import dec
from .models import Approval, CommercialDocument, Execution, Payment, PaymentAllocation, PaymentReversal
from .procurement import DB, Actor
from .schemas import PaymentAllocateIn, PaymentIn, PaymentReverseIn, VersionCommand

router = APIRouter(tags=["Оплаты и распределения"])


def allocation_balance(db, allocation: PaymentAllocation) -> Decimal:
    reversed_amount = sum(
        (
            row.amount
            for row in db.scalars(
                select(PaymentReversal).where(PaymentReversal.allocation_id == allocation.id)
            ).all()
        ),
        Decimal("0"),
    )
    return allocation.amount - reversed_amount


def payment_balance(db, payment: Payment) -> dict:
    allocations = db.scalars(
        select(PaymentAllocation).where(PaymentAllocation.payment_id == payment.id)
    ).all()
    returns = db.scalars(
        select(PaymentReversal).where(
            PaymentReversal.payment_id == payment.id, PaymentReversal.kind.in_(("refund", "correction"))
        )
    ).all()
    refunded = sum((row.amount for row in returns), Decimal("0"))
    allocated = sum((allocation_balance(db, row) for row in allocations), Decimal("0"))
    return {
        "confirmed_amount": str(payment.amount - refunded if payment.status == "confirmed" else Decimal("0")),
        "allocated": str(allocated),
        "refunded": str(refunded),
        "unallocated": str(
            payment.amount - refunded - allocated if payment.status == "confirmed" else Decimal("0")
        ),
    }


def payment_view(db, payment: Payment) -> dict:
    return {
        **serialize(payment),
        **payment_balance(db, payment),
        "allocations": [
            {**serialize(row), "remaining": str(allocation_balance(db, row))}
            for row in db.scalars(
                select(PaymentAllocation).where(PaymentAllocation.payment_id == payment.id)
            ).all()
        ],
        "reversals": [
            serialize(row)
            for row in db.scalars(
                select(PaymentReversal).where(PaymentReversal.payment_id == payment.id)
            ).all()
        ],
    }


def invoice_balance(db, invoice: CommercialDocument) -> dict:
    rows = db.scalars(
        select(PaymentAllocation)
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .where(PaymentAllocation.invoice_id == invoice.id, Payment.status == "confirmed")
    ).all()
    paid = sum((allocation_balance(db, row) for row in rows), Decimal("0"))
    return {
        "paid": str(paid),
        "remaining": str(invoice.total - paid),
        "payment_status": "paid" if paid >= invoice.total else "partial" if paid > 0 else "unpaid",
    }


def funding_for_execution(db, execution: Execution) -> dict:
    invoices = db.scalars(
        select(CommercialDocument).where(
            CommercialDocument.request_id == execution.request_id,
            CommercialDocument.kind == "invoice",
            CommercialDocument.status != "cancelled",
        )
    ).all()
    funded = Decimal("0")
    sources = []
    for invoice in invoices:
        lines = [row for row in invoice.snapshot["lines"] if row["execution_id"] == execution.id]
        if not lines:
            continue
        paid = dec(invoice_balance(db, invoice)["paid"])
        part = sum((dec(row["total"]) for row in lines), Decimal("0"))
        contribution = part if invoice.total == 0 else part * paid / invoice.total
        funded += contribution
        sources.append(
            {
                "invoice_id": invoice.id,
                "invoice_total": str(invoice.total),
                "paid": str(paid),
                "funded": str(contribution),
            }
        )
    total = dec(execution.snapshot["total"])
    return {
        "amount": str(funded),
        "total": str(total),
        "ratio": str(funded / total if total else Decimal("1")),
        "invoices": sources,
        "method": "proportional_to_invoice_line_total",
    }


def funding_lock(db, payment: Payment) -> None:
    advisory(db, f"funding:{payment.seller_id}:{payment.client_id}:{payment.currency}")


@router.get("/requests/{request_id}/payments")
def list_payments(request_id: str, db: DB, user: Actor):
    check_request(db, user, request_id)
    payments = db.scalars(
        select(Payment).where(Payment.request_id == request_id).order_by(Payment.created_at.desc())
    ).all()
    return {"items": [payment_view(db, row) for row in payments]}


@router.post("/requests/{request_id}/payments")
def declare_payment(request_id: str, data: PaymentIn, db: DB, user: Actor):
    req = check_request(db, user, request_id, "payments.write")

    def operation():
        if not req.seller_id:
            error("SELLER_REQUIRED", "Укажите организацию продавца в заявке")
        advisory(db, f"funding:{req.seller_id}:{req.client_id}:{data.currency}")
        if data.external_id and db.scalar(
            select(Payment.id).where(
                Payment.seller_id == req.seller_id, Payment.external_id == data.external_id
            )
        ):
            error("DUPLICATE_PAYMENT", "Платёж с этим внешним идентификатором уже зарегистрирован", 409)
        if data.invoice_id:
            invoice = db.get(CommercialDocument, data.invoice_id)
            if (
                not invoice
                or invoice.request_id != request_id
                or invoice.kind != "invoice"
                or invoice.currency != data.currency
                or invoice.status == "cancelled"
            ):
                error("PAYMENT_INVOICE", "Счёт должен относиться к заявке и валюте платежа")
        candidates = db.scalars(
            select(Payment).where(
                Payment.seller_id == req.seller_id,
                Payment.client_id == req.client_id,
                Payment.amount == data.amount,
                Payment.currency == data.currency,
                Payment.payment_date == data.payment_date,
                Payment.number == data.number,
            )
        ).all()
        obj = Payment(
            request_id=request_id,
            client_id=req.client_id,
            seller_id=req.seller_id,
            declared_by=user.id,
            **data.model_dump(exclude={"idempotency_key"}),
        )
        db.add(obj)
        db.flush()
        audit(db, user, "payment", obj.id, "declare", after=serialize(obj))
        return {**payment_view(db, obj), "possible_duplicate_ids": [row.id for row in candidates]}

    return idem(
        db,
        user,
        data.idempotency_key,
        f"declare-payment:{request_id}",
        data.model_dump(mode="json"),
        operation,
    )


@router.post("/payments/{payment_id}/confirm")
def confirm_payment(payment_id: str, data: VersionCommand, db: DB, user: Actor):
    payment = db.get(Payment, payment_id)
    if not payment:
        error("NOT_FOUND", "Платёж не найден", 404)
    req = check_request(db, user, payment.request_id, "payments.confirm")

    def operation():
        funding_lock(db, payment)
        current = lock(db, Payment, payment_id)
        check_version(current, data.version)
        if current.status != "declared":
            error("PAYMENT_ALREADY_CONFIRMED", "Платёж уже подтверждён", 409)
        current.status = "confirmed"
        current.confirmed_by, current.confirmed_at = user.id, utcnow()
        current.version += 1
        audit(
            db,
            user,
            "payment",
            current.id,
            "confirm",
            after={"confirmed_by": user.id, "confirmed_at": current.confirmed_at},
        )
        notify(
            db,
            req.owner_id,
            f"payment-confirmed:{current.id}",
            "Поступление оплаты подтверждено",
            "request",
            req.id,
        )
        return payment_view(db, current)

    return idem(
        db,
        user,
        data.idempotency_key,
        f"confirm-payment:{payment_id}",
        data.model_dump(mode="json"),
        operation,
    )


@router.post("/payments/{payment_id}/allocate")
def allocate_payment(payment_id: str, data: PaymentAllocateIn, db: DB, user: Actor):
    payment = db.get(Payment, payment_id)
    if not payment:
        error("NOT_FOUND", "Платёж не найден", 404)
    check_request(db, user, payment.request_id, "payments.confirm")

    def operation():
        funding_lock(db, payment)
        current = lock(db, Payment, payment_id)
        check_version(current, data.version)
        if current.status != "confirmed":
            error("PAYMENT_UNCONFIRMED", "Сначала подтвердите поступление оплаты")
        if len({row.invoice_id for row in data.allocations}) != len(data.allocations):
            error("DUPLICATE_INVOICE", "Счёт указан повторно")
        total = sum((row.amount for row in data.allocations), Decimal("0"))
        if total > dec(payment_balance(db, current)["unallocated"]):
            error("PAYMENT_OVERALLOCATED", "Сумма распределений превышает доступный остаток платежа")
        for selected in sorted(data.allocations, key=lambda row: row.invoice_id):
            invoice = lock(db, CommercialDocument, selected.invoice_id)
            check_request(db, user, invoice.request_id, "payments.confirm")
            if (
                invoice.kind != "invoice"
                or invoice.status == "cancelled"
                or (invoice.seller_id, invoice.client_id, invoice.currency)
                != (current.seller_id, current.client_id, current.currency)
            ):
                error(
                    "PAYMENT_PARTIES", "Распределять можно только на счета того же клиента, продавца и валюты"
                )
            if selected.amount > dec(invoice_balance(db, invoice)["remaining"]):
                error(
                    "INVOICE_OVERPAYMENT",
                    "Зачисление превышает остаток счёта; оставьте излишек нераспределённым",
                )
            obj = PaymentAllocation(
                payment_id=current.id, invoice_id=invoice.id, amount=selected.amount, author_id=user.id
            )
            db.add(obj)
            db.flush()
            audit(db, user, "payment_allocation", obj.id, "allocate", after=serialize(obj))
        current.version += 1
        return payment_view(db, current)

    return idem(
        db,
        user,
        data.idempotency_key,
        f"allocate-payment:{payment_id}",
        data.model_dump(mode="json"),
        operation,
    )


@router.post("/payments/{payment_id}/reverse")
def reverse_payment(payment_id: str, data: PaymentReverseIn, db: DB, user: Actor):
    payment = db.get(Payment, payment_id)
    if not payment:
        error("NOT_FOUND", "Платёж не найден", 404)
    req = check_request(db, user, payment.request_id, "payments.confirm")

    def operation():
        funding_lock(db, payment)
        current = lock(db, Payment, payment_id)
        check_version(current, data.version)
        if current.status != "confirmed":
            error("PAYMENT_UNCONFIRMED", "Обратная операция требует подтверждённого платежа")
        affected_requests = {req.id}
        if data.allocation_id:
            allocation = lock(db, PaymentAllocation, data.allocation_id)
            if allocation.payment_id != current.id:
                error("ALLOCATION_NOT_FOUND", "Распределение платежа не найдено", 404)
            invoice = db.get(CommercialDocument, allocation.invoice_id)
            check_request(db, user, invoice.request_id, "payments.confirm")
            affected_requests.add(invoice.request_id)
            available = allocation_balance(db, allocation)
        else:
            if data.kind == "unallocate":
                error("ALLOCATION_REQUIRED", "Для снятия распределения выберите распределение")
            available = dec(payment_balance(db, current)["unallocated"])
        if data.amount > available:
            error("REVERSAL_EXCEEDED", "Сумма обратной операции превышает остаток")
        obj = PaymentReversal(
            payment_id=current.id,
            allocation_id=data.allocation_id,
            amount=data.amount,
            kind=data.kind,
            reason=data.reason,
            author_id=user.id,
        )
        db.add(obj)
        db.flush()
        current.version += 1
        for request_id in sorted(affected_requests):
            executions = db.scalars(select(Execution).where(Execution.request_id == request_id)).all()
            for execution in executions:
                approvals = db.scalars(
                    select(Approval).where(Approval.request_id == request_id, Approval.status == "approved")
                ).all()
                applicable = [
                    approval
                    for approval in approvals
                    if any(row["execution_id"] == execution.id for row in approval.snapshot["lines"])
                ]
                if applicable:
                    threshold = max(
                        dec(row["funding_ratio"])
                        for approval in applicable
                        for row in approval.snapshot["lines"]
                        if row["execution_id"] == execution.id
                    )
                    execution.financing_deficit = (
                        dec(funding_for_execution(db, execution)["ratio"]) < threshold
                    )
                    if execution.financing_deficit:
                        for reviewer_id in {approval.reviewer_id for approval in applicable} | {user.id}:
                            notify(
                                db,
                                reviewer_id,
                                f"funding-deficit:{obj.id}:{execution.id}",
                                "Обнаружен дефицит финансирования согласованной позиции",
                                "request",
                                request_id,
                            )
            notify(
                db,
                req.owner_id,
                f"payment-reversed:{obj.id}:{request_id}",
                "Выполнена обратная операция оплаты",
                "request",
                request_id,
            )
        audit(db, user, "payment_reversal", obj.id, data.kind, after=serialize(obj), reason=data.reason)
        return payment_view(db, current)

    return idem(
        db,
        user,
        data.idempotency_key,
        f"reverse-payment:{payment_id}",
        data.model_dump(mode="json"),
        operation,
    )
