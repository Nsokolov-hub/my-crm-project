import copy
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import func, select

from app.core.errors import error
from app.core.security import check_request, require_permission
from app.core.service import advisory, audit, check_version, idem, lock, serialize
from app.crm.models import Counterparty, Seller
from app.crm.models import Request as CRMRequest

from .calculator import ROUNDING, convert, dec
from .files import document_files, read_file
from .models import Calculation, CommercialDocument, Execution, Product, Quote
from .procurement import DB, Actor, check_quote, validate_quantity
from .schemas import AcceptanceIn, InvoiceIn, ProposalIn, SentIn

router = APIRouter(tags=["Коммерческие документы"])


def document_view(document: CommercialDocument) -> dict:
    value = serialize(document)
    value["files"] = {
        name: {"sha256": data["sha256"], "size": data["size"]} for name, data in document.files.items()
    }
    return value


def next_number(db, seller_id: str, kind: str) -> str:
    year = date.today().year
    advisory(db, f"document-number:{seller_id}:{kind}:{year}")
    prefix = f"{year}-{'KP' if kind == 'proposal' else 'INV'}-"
    count = (
        db.scalar(
            select(func.count())
            .select_from(CommercialDocument)
            .where(
                CommercialDocument.seller_id == seller_id,
                CommercialDocument.kind == kind,
                CommercialDocument.number.startswith(prefix),
            )
        )
        or 0
    )
    return f"{prefix}{count + 1:06d}"


def partial_amount(original: dict, quantity: Decimal, previous: list[dict], profile: dict) -> dict:
    """Final partial issuance absorbs rounding so all pieces exactly match the source."""
    previous_quantity = sum((dec(row["quantity"]) for row in previous), Decimal("0"))
    original_quantity = dec(original["quantity"])
    if quantity <= 0 or previous_quantity + quantity > original_quantity:
        error("QUANTITY_EXCEEDED", "Количество превышает оставшийся согласованный объём")
    quantum = Decimal(1).scaleb(-profile["currency_precision"][profile["sale_currency"]])
    result = copy.deepcopy(original)
    result["quantity"] = str(quantity)
    for column in ("net", "tax"):
        remaining = dec(original[column]) - sum((dec(row[column]) for row in previous), Decimal("0"))
        if previous_quantity + quantity == original_quantity:
            amount = remaining
        else:
            amount = (dec(original[column]) * quantity / original_quantity).quantize(
                quantum, rounding=ROUNDING[profile["rounding"]]
            )
            # Cannot issue more than remaining
            amount = min(amount, remaining)
        # Cannot issue negative amounts
        amount = max(amount, Decimal("0"))
        result[column] = str(amount)

    result["total"] = str(dec(result["net"]) + dec(result["tax"]))
    if dec(result["total"]) < 0:
        error("NEGATIVE_TOTAL", "Сумма не может быть отрицательной")
    return result


def issued_invoice_lines(db, execution_id: str) -> list[dict]:
    execution = db.get(Execution, execution_id)
    invoices = db.scalars(
        select(CommercialDocument).where(
            CommercialDocument.request_id == execution.request_id,
            CommercialDocument.kind == "invoice",
            CommercialDocument.status != "cancelled",
        )
    ).all()
    return [
        line
        for invoice in invoices
        for line in invoice.snapshot["lines"]
        if line["execution_id"] == execution_id
    ]


def customer_line(line: dict, template: dict) -> dict:
    product = line["product"]
    description = " · ".join(
        str(value)
        for value in (
            product["name"],
            product.get("purity"),
            product.get("packaging"),
            product.get("manufacturer") if template.get("show_manufacturer") else None,
        )
        if value
    )
    return {
        "line_id": line["line_id"],
        "item_id": line["item_id"],
        "description": description,
        "cas": product.get("cas") if template.get("show_cas") else None,
        **{
            field: line[field]
            for field in ("quantity", "unit", "unit_price", "net", "tax", "total", "tax_category")
        },
    }


def base_snapshot(
    db,
    request: CRMRequest,
    calculation: Calculation,
    kind: str,
    number: str,
    lines: list[dict],
    valid_until: date,
    terms: str,
) -> dict:
    seller = db.get(Seller, request.seller_id) if request.seller_id else None
    client = db.get(Counterparty, request.client_id)
    if not seller or seller.archived:
        error("SELLER_REQUIRED", "Укажите действующую организацию продавца в заявке")
    profile = calculation.snapshot["profile"]
    title = (
        profile["template"].get("title", "Коммерческое предложение")
        if kind == "proposal"
        else profile["template"].get("invoice_title", "Счёт на оплату")
    )
    return {
        "title": title,
        "number": number,
        "date": date.today().isoformat(),
        "seller": {"id": seller.id, "name": seller.name, "details": seller.details},
        "client": {"id": client.id, "name": client.name, "details": client.details, "tax_id": client.tax_id},
        "currency": profile["sale_currency"],
        "lines": lines,
        "totals": {
            key: str(sum((dec(row[key]) for row in lines), Decimal("0"))) for key in ("net", "tax", "total")
        },
        "valid_until": valid_until.isoformat(),
        "terms": terms,
        "template": profile["template"],
        "template_profile_id": calculation.profile_id,
    }


@router.post("/requests/{request_id}/proposals")
def issue_proposal(request_id: str, data: ProposalIn, db: DB, user: Actor):
    check_request(db, user, request_id, "documents.write")

    def operation():
        advisory(db, f"request-commerce:{request_id}")
        req = lock(db, CRMRequest, request_id)
        calc = db.get(Calculation, data.calculation_id)
        if not calc or calc.request_id != request_id:
            error("CALCULATION_REQUIRED", "Для выпуска КП нужен сохранённый расчёт этой заявки")
        if data.valid_until < date.today():
            error("DOCUMENT_EXPIRED", "Срок действия документа уже истёк")
        if data.previous_id:
            prior = db.get(CommercialDocument, data.previous_id)
            if not prior or prior.kind != "proposal" or prior.request_id != request_id:
                error("PREVIOUS_DOCUMENT", "Предыдущая версия КП не найдена", 404)
            if db.scalar(select(Execution.id).where(Execution.proposal_id == prior.id)):
                error(
                    "ACCEPTED_DOCUMENT",
                    "Принятый состав сохраняется; исправление требует отдельного решения по исполнению",
                )
            prior.status = "replaced"
            prior.version += 1
        for line in calc.snapshot["lines"]:
            quote = db.get(Quote, line["quote_id"])
            check_quote(db, quote)
        number = next_number(db, req.seller_id, "proposal")
        snapshot = base_snapshot(
            db,
            req,
            calc,
            "proposal",
            number,
            [customer_line(row, calc.snapshot["profile"]["template"]) for row in calc.snapshot["lines"]],
            data.valid_until,
            data.terms,
        )
        obj = CommercialDocument(
            request_id=request_id,
            seller_id=req.seller_id,
            client_id=req.client_id,
            kind="proposal",
            number=number,
            calculation_id=calc.id,
            previous_id=data.previous_id,
            currency=snapshot["currency"],
            total=dec(snapshot["totals"]["total"]),
            valid_until=data.valid_until,
            snapshot=snapshot,
            files=document_files(snapshot),
            author_id=user.id,
        )
        db.add(obj)
        db.flush()
        audit(
            db,
            user,
            "document",
            obj.id,
            "issue",
            after={"number": obj.number, "kind": obj.kind, "files": obj.files},
        )
        return document_view(obj)

    return idem(
        db, user, data.idempotency_key, f"proposal:{request_id}", data.model_dump(mode="json"), operation
    )


@router.post("/proposals/{proposal_id}/accept")
def accept_proposal(proposal_id: str, data: AcceptanceIn, db: DB, user: Actor):
    proposal = db.get(CommercialDocument, proposal_id)
    if not proposal or proposal.kind != "proposal":
        error("NOT_FOUND", "КП не найдено", 404)
    check_request(db, user, proposal.request_id, "documents.write")

    def operation():
        advisory(db, f"request-commerce:{proposal.request_id}")
        doc = lock(db, CommercialDocument, proposal_id)
        check_version(doc, data.version)
        if doc.status not in ("issued", "sent", "accepted") or doc.valid_until < date.today():
            error("PROPOSAL_STATE", "КП недоступно для принятия; выпустите актуальную версию")
        calc = db.get(Calculation, doc.calculation_id)
        profile = calc.snapshot["profile"]
        if len({row.line_id for row in data.lines}) != len(data.lines):
            error("DUPLICATE_LINE", "Строка указана повторно")
        result = []
        for selected in data.lines:
            source = next(
                (line for line in calc.snapshot["lines"] if line["line_id"] == selected.line_id), None
            )
            if source is None:
                error("PROPOSAL_LINE", "Строка не найдена в выбранном КП")
            quote = db.get(Quote, source["quote_id"])
            item = check_quote(db, quote)
            if not item.quantity or not item.unit:
                error("DEMAND_INCOMPLETE", "Укажите согласованный объём потребности")
            if quote.is_analogue and (not item.allow_analogue or len(selected.analogue_reason.strip()) < 3):
                error(
                    "ANALOGUE_APPROVAL",
                    "Для аналога нужны разрешение в потребности и основание согласия клиента",
                )
            product = db.get(Product, quote.product_id)
            validate_quantity(quote, product, selected.quantity, source["unit"])

            existing = db.scalars(select(Execution).where(Execution.item_id == item.id)).all()
            accepted = sum(
                (convert(ex.quantity - ex.cancelled_quantity, ex.unit, item.unit) for ex in existing),
                Decimal("0"),
            )
            requested = convert(selected.quantity, source["unit"], item.unit)
            if accepted + requested > item.quantity:
                error("ACCEPTANCE_EXCEEDED", "Сумма принятых количеств превышает потребность")
            if not profile["allow_multiple_suppliers"] and any(
                db.get(Quote, ex.quote_id).supplier_id != quote.supplier_id
                for ex in existing
                if ex.cancelled_quantity < ex.quantity
            ):
                error("MULTIPLE_SUPPLIERS", "Профиль запрещает несколько поставщиков по одной потребности")
            if not profile["allow_partial_acceptance"] and requested != item.quantity - accepted:
                error("PARTIAL_ACCEPTANCE", "Профиль требует принятия полного остатка потребности")
            same_line = [
                ex.snapshot for ex in existing if ex.proposal_id == doc.id and ex.line_id == selected.line_id
            ]
            # Historical cancellation does not silently permit reuse of the same released line.
            part = partial_amount(source, selected.quantity, same_line, profile)
            obj = Execution(
                request_id=doc.request_id,
                item_id=item.id,
                proposal_id=doc.id,
                quote_id=quote.id,
                line_id=selected.line_id,
                quantity=selected.quantity,
                unit=source["unit"],
                snapshot=part,
                acceptance_reason=data.reason
                + ("; " + selected.analogue_reason if selected.analogue_reason else ""),
                accepted_by=user.id,
            )
            db.add(obj)
            db.flush()
            audit(
                db,
                user,
                "execution",
                obj.id,
                "accept",
                after={"proposal_id": doc.id, "quantity": obj.quantity, "unit": obj.unit},
                reason=obj.acceptance_reason,
            )
            result.append({key: value for key, value in serialize(obj).items() if key != "snapshot"})
        doc.status = "accepted"
        doc.version += 1
        return {"proposal": document_view(doc), "executions": result}

    return idem(
        db, user, data.idempotency_key, f"accept:{proposal_id}", data.model_dump(mode="json"), operation
    )


@router.post("/requests/{request_id}/invoices")
def issue_invoice(request_id: str, data: InvoiceIn, db: DB, user: Actor):
    check_request(db, user, request_id, "documents.write")

    def operation():
        advisory(db, f"request-commerce:{request_id}")
        req = lock(db, CRMRequest, request_id)
        proposal = db.get(CommercialDocument, data.proposal_id)
        if (
            not proposal
            or proposal.kind != "proposal"
            or proposal.request_id != request_id
            or proposal.status != "accepted"
        ):
            error("ACCEPTED_PROPOSAL_REQUIRED", "Счёт создаётся только для принятого КП этой заявки")
        if req.seller_id != proposal.seller_id or req.client_id != proposal.client_id:
            error("DOCUMENT_PARTIES_CHANGED", "Стороны заявки изменились. Согласуйте новое КП")
        if len({line.execution_id for line in data.lines}) != len(data.lines):
            error("DUPLICATE_LINE", "Позиция исполнения указана повторно")
        calc = db.get(Calculation, proposal.calculation_id)
        lines = []
        for selected in data.lines:
            execution = lock(db, Execution, selected.execution_id)
            if execution.request_id != request_id or execution.proposal_id != proposal.id:
                error("INVOICE_COMPOSITION", "Все позиции счёта должны относиться к одному принятому КП")
            if execution.cancelled_quantity:
                error(
                    "EXECUTION_CANCELLED", "По позиции есть отменённый объём; требуется исправляющий состав"
                )
            part = partial_amount(
                execution.snapshot,
                selected.quantity,
                issued_invoice_lines(db, execution.id),
                calc.snapshot["profile"],
            )
            line = customer_line(part, calc.snapshot["profile"]["template"])
            line["execution_id"] = execution.id
            lines.append(line)
        number = next_number(db, req.seller_id, "invoice")
        snapshot = base_snapshot(db, req, calc, "invoice", number, lines, data.due_date, data.terms)
        # Parties remain bound to the accepted proposal, including requisites.
        snapshot["seller"] = copy.deepcopy(proposal.snapshot["seller"])
        snapshot["client"] = copy.deepcopy(proposal.snapshot["client"])
        obj = CommercialDocument(
            request_id=request_id,
            seller_id=proposal.seller_id,
            client_id=proposal.client_id,
            kind="invoice",
            number=number,
            calculation_id=calc.id,
            proposal_id=proposal.id,
            currency=proposal.currency,
            total=dec(snapshot["totals"]["total"]),
            valid_until=data.due_date,
            snapshot=snapshot,
            files=document_files(snapshot),
            author_id=user.id,
        )
        db.add(obj)
        db.flush()
        audit(
            db,
            user,
            "document",
            obj.id,
            "issue",
            after={"number": obj.number, "kind": obj.kind, "files": obj.files},
        )
        return document_view(obj)

    return idem(
        db, user, data.idempotency_key, f"invoice:{request_id}", data.model_dump(mode="json"), operation
    )


@router.get("/requests/{request_id}/proposals")
def list_proposals(request_id: str, db: DB, user: Actor):
    check_request(db, user, request_id)
    return {
        "items": [
            document_view(row)
            for row in db.scalars(
                select(CommercialDocument)
                .where(CommercialDocument.request_id == request_id, CommercialDocument.kind == "proposal")
                .order_by(CommercialDocument.created_at.desc())
            ).all()
        ]
    }


@router.get("/requests/{request_id}/invoices")
def list_invoices(request_id: str, db: DB, user: Actor):
    from .payments import invoice_balance

    check_request(db, user, request_id)
    return {
        "items": [
            {**document_view(row), **invoice_balance(db, row)}
            for row in db.scalars(
                select(CommercialDocument)
                .where(CommercialDocument.request_id == request_id, CommercialDocument.kind == "invoice")
                .order_by(CommercialDocument.created_at.desc())
            ).all()
        ]
    }


@router.get("/documents/{document_id}/file")
def download_document(
    document_id: str, db: DB, user: Actor, format: str = Query("pdf", pattern="^(pdf|xlsx)$")
):
    obj = db.get(CommercialDocument, document_id)
    if not obj:
        error("NOT_FOUND", "Документ не найден", 404)
    check_request(db, user, obj.request_id)
    require_permission(db, user, "exports.download", obj.request_id)
    audit(db, user, "document", obj.id, "download", after={"format": format})
    media_type = (
        "application/pdf"
        if format == "pdf"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return Response(
        read_file(obj.files[format]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{obj.number}.{format}"'},
    )


@router.post("/proposals/{document_id}/sent")
@router.post("/documents/{document_id}/sent")
def mark_sent(document_id: str, data: SentIn, db: DB, user: Actor):
    obj = db.get(CommercialDocument, document_id)
    if not obj:
        error("NOT_FOUND", "Документ не найден", 404)
    check_request(db, user, obj.request_id, "documents.write")

    def operation():
        doc = lock(db, CommercialDocument, document_id)
        check_version(doc, data.version)
        if doc.status in ("cancelled", "replaced", "rejected"):
            error("DOCUMENT_STATE", "Документ недоступен для отправки")
        doc.sent_at, doc.sent_channel = data.sent_at, data.channel
        if doc.status == "issued":
            doc.status = "sent"
        doc.version += 1
        audit(db, user, "document", doc.id, "sent", after={"channel": data.channel, "sent_at": data.sent_at})
        return document_view(doc)

    return idem(
        db,
        user,
        data.idempotency_key,
        f"document-sent:{document_id}",
        data.model_dump(mode="json"),
        operation,
    )
