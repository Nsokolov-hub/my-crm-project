"""Client-saving price reductions: two approvals, supplier letters and quote replacement."""

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from html import escape

from fastapi import APIRouter
from pydantic import Field
from sqlalchemy import select

from app.commerce.calculator import dec
from app.commerce.models import Calculation, CommercialDocument
from app.commerce.schemas import Command, Input, VersionCommand
from app.core.config import settings
from app.core.db import DB, utcnow
from app.core.errors import error
from app.core.models import OutboxEvent, User
from app.core.security import (
    Actor,
    check_request,
    has_request_permission,
    require_permission,
    users_with_permission,
)
from app.core.service import audit, check_version, idem, lock, notify, serialize
from app.crm.models import Counterparty, QuoteItem, QuoteSheet, Request
from app.crm.quotes import quote_item_views

from .models import PriceReductionRequest, SupplierMail, WorkflowReview
from .reviews import submit_review
from .supplier_mail import supplier_recipients

router = APIRouter(tags=["Запросы на снижение цены"])

ACTIVE_MAIL_STATUSES = ("approved", "awaiting_price_reduction")


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def _latest_calculation(db, request_id: str) -> Calculation | None:
    return db.scalar(
        select(Calculation)
        .where(Calculation.request_id == request_id)
        .order_by(Calculation.created_at.desc(), Calculation.id.desc())
        .limit(1)
    )


def _selected_quotes(db, request_id: str, quote_item_ids: list[str]) -> list[QuoteItem]:
    ids = list(dict.fromkeys(quote_item_ids))
    rows = db.scalars(
        select(QuoteItem)
        .join(QuoteSheet, QuoteSheet.id == QuoteItem.quote_id)
        .where(
            QuoteItem.id.in_(ids),
            QuoteItem.archived.is_(False),
            QuoteSheet.archived.is_(False),
            QuoteSheet.request_id == request_id,
        )
    ).all()
    by_id = {row.id: row for row in rows}
    if len(by_id) != len(ids):
        error("QUOTE_SELECTION", "Выберите действующие позиции квот этой заявки")
    return [by_id[identifier] for identifier in ids]


def _purchase_hint(line: dict | None, target_sale_price: Decimal | None) -> str | None:
    if not line or target_sale_price is None:
        return None
    quantity = dec(line.get("quantity") or "0")
    detail = line.get("detail") or {}
    purchase = detail.get("purchase_rub") if "purchase_rub" in detail else detail.get("purchase")
    if not quantity or purchase in (None, "") or not line.get("net"):
        return None
    current_net = dec(line["net"]) / quantity
    current_purchase = dec(purchase) / quantity
    total = dec(line.get("total") or line["net"])
    gross_factor = total / dec(line["net"])
    target_net = target_sale_price / gross_factor if gross_factor else target_sale_price
    # Keep the current profit per packing; all non-purchase expenses remain as in the last calculation.
    return _money(current_purchase - (current_net - target_net))


def _item_snapshots(
    db, rows: list[QuoteItem], calculation: Calculation | None, targets: dict[str, Decimal] | None = None
) -> list[dict]:
    views = quote_item_views(db, rows, True)
    lines = {
        line.get("quote_item_id"): line
        for line in ((calculation.snapshot.get("lines") or []) if calculation else [])
    }
    result = []
    for row, view in zip(rows, views, strict=True):
        line = lines.get(row.id)
        target = (targets or {}).get(row.id)
        result.append({
            "quote_item_id": row.id,
            "source_request_item_id": row.source_request_item_id,
            "supplier_id": row.supplier_id,
            "supplier_name": view["supplier_name"],
            "nomenclature_id": row.nomenclature_id,
            "nomenclature_name": view["nomenclature_name"],
            "article": view.get("article"),
            "packing_id": row.packing_id,
            "packing_name": view["packing_name"],
            "quantity": str(row.quantity),
            "unit_price": str(row.unit_price),
            "currency_id": row.currency_id,
            "currency_code": view["currency_code"],
            "delivery_days": row.delivery_days,
            "current_sale_unit_price": line.get("unit_price") if line else None,
            "target_sale_price": str(target) if target is not None else None,
            "required_purchase_rub": _purchase_hint(line, target),
            "calculation_line": bool(line),
        })
    return result


def _person(user: User) -> dict:
    return {"id": user.id, "name": user.name, "email": user.email}


def reduction_view(db, row: PriceReductionRequest) -> dict:
    result = serialize(row)
    replacement_pairs = [
        (old_id, new)
        for old_id, new_id in (row.result_map or {}).items()
        if (new := db.get(QuoteItem, new_id))
    ]
    views = quote_item_views(db, [new for _, new in replacement_pairs], True)
    replacement_views = {
        old_id: view
        for (old_id, _), view in zip(replacement_pairs, views, strict=True)
    }
    items = []
    for original in row.items:
        item = dict(original)
        replacement = replacement_views.get(item["quote_item_id"])
        if replacement:
            item.update(
                previous_unit_price=item["unit_price"],
                unit_price=replacement["unit_price"],
                discount_percent=replacement.get("discount_percent"),
                price_reduction_id=replacement.get("price_reduction_id"),
                replacement_quote_item_id=replacement["id"],
            )
        items.append(item)
    result["items"] = items
    for key, user_id in (("leader", row.leader_id), ("manager", row.manager_id), ("author", row.author_id)):
        person = db.get(User, user_id)
        result[f"{key}_name"] = person.name if person else None
    result["reviews"] = [
        serialize(review)
        for review in db.scalars(
            select(WorkflowReview)
            .where(WorkflowReview.entity_id == row.id)
            .order_by(WorkflowReview.created_at, WorkflowReview.id)
        )
    ]
    result["mails"] = [
        serialize(mail)
        for mail in db.scalars(
            select(SupplierMail)
            .where(SupplierMail.price_reduction_id == row.id)
            .order_by(SupplierMail.created_at, SupplierMail.id)
        )
    ]
    return result


@router.get("/requests/{request_id}/price-reduction-options")
def reduction_options(request_id: str, quote_item_ids: str, db: DB, user: Actor):
    request = check_request(db, user, request_id, "documents.write")
    require_permission(db, user, "finance.purchase.read", request_id)
    ids = [value.strip() for value in quote_item_ids.split(",") if value.strip()]
    rows = _selected_quotes(db, request_id, ids)
    calculation = _latest_calculation(db, request_id)
    leaders = [
        _person(person)
        for person in users_with_permission(db, "approvals.decide")
        if has_request_permission(db, person, request_id, "approvals.decide")
    ]
    sheet_authors = {
        value for value in db.scalars(
            select(QuoteSheet.author_id).where(QuoteSheet.id.in_({row.quote_id for row in rows}))
        )
    }
    managers = []
    for person in db.scalars(
        select(User).where(User.id.in_({*sheet_authors, request.owner_id}), User.active.is_(True)).order_by(User.name)
    ):
        if has_request_permission(db, person, request_id, "requests.read"):
            managers.append(_person(person))
    return {
        "items": _item_snapshots(db, rows, calculation),
        "leaders": leaders,
        "managers": managers,
        "calculation_id": calculation.id if calculation else None,
        "default_manager_id": request.owner_id,
    }


class ReductionTarget(Input):
    quote_item_id: str
    target_sale_price: Decimal = Field(gt=0, max_digits=24, decimal_places=8)


class ReductionCreate(Command):
    request_version: int = Field(ge=1)
    quote_item_ids: list[str] = Field(min_length=1, max_length=100)
    leader_id: str
    manager_id: str
    client_due_date: date
    comment: str = Field(min_length=3, max_length=4000)
    targets: list[ReductionTarget] = Field(min_length=1, max_length=100)


def _validate_people(db, request: Request, rows: list[QuoteItem], leader_id: str, manager_id: str):
    leader = db.get(User, leader_id)
    if not leader or not leader.active or not has_request_permission(db, leader, request.id, "approvals.decide"):
        error("REDUCTION_LEADER", "Выберите руководителя с правом согласования по этой заявке")
    manager = db.get(User, manager_id)
    authors = set(db.scalars(select(QuoteSheet.author_id).where(QuoteSheet.id.in_({row.quote_id for row in rows}))))
    if (
        not manager
        or not manager.active
        or manager.id not in {*authors, request.owner_id}
        or not has_request_permission(db, manager, request.id, "requests.read")
    ):
        error("REDUCTION_MANAGER", "Выберите менеджера, который внёс выбранную квоту, или ответственного заявки")
    return leader, manager


class ReductionHints(Input):
    targets: list[ReductionTarget] = Field(min_length=1, max_length=100)


@router.post("/requests/{request_id}/price-reduction-hints")
def reduction_hints(request_id: str, data: ReductionHints, db: DB, user: Actor):
    check_request(db, user, request_id, "documents.write")
    require_permission(db, user, "finance.purchase.read", request_id)
    targets = {target.quote_item_id: target.target_sale_price for target in data.targets}
    rows = _selected_quotes(db, request_id, list(targets))
    return {"items": _item_snapshots(db, rows, _latest_calculation(db, request_id), targets)}


@router.post("/requests/{request_id}/price-reductions")
def create_reduction(request_id: str, data: ReductionCreate, db: DB, user: Actor):
    check_request(db, user, request_id, "documents.write")
    require_permission(db, user, "finance.purchase.read", request_id)

    def operation():
        parent = lock(db, Request, request_id)
        check_version(parent, data.request_version)
        proposal = db.scalar(
            select(CommercialDocument.id).where(
                CommercialDocument.request_id == request_id,
                CommercialDocument.kind == "proposal",
                CommercialDocument.status.not_in(("cancelled", "rejected")),
            ).limit(1)
        )
        if not proposal:
            error("REDUCTION_PROPOSAL_REQUIRED", "Сначала выпустите КП по этой заявке", 409)
        if parent.closed_at or parent.commercial_stage == "sale_confirmed":
            error("REDUCTION_CLOSED", "По завершённой продаже запрос на снижение не создаётся", 409)
        if data.client_due_date < date.today():
            error("REDUCTION_DUE_DATE", "Дата ожидания клиента не может быть в прошлом", field="client_due_date")
        rows = _selected_quotes(db, request_id, data.quote_item_ids)
        if len({(row.supplier_id, row.source_request_item_id) for row in rows}) != len(rows):
            error("REDUCTION_DUPLICATE", "Выберите одну квоту поставщика для каждой позиции заявки")
        targets = {target.quote_item_id: target.target_sale_price for target in data.targets}
        if set(targets) != set(data.quote_item_ids):
            error("REDUCTION_TARGETS", "Укажите желаемую цену для каждой выбранной позиции")
        _validate_people(db, parent, rows, data.leader_id, data.manager_id)
        calculation = _latest_calculation(db, request_id)
        items = _item_snapshots(db, rows, calculation, targets)
        row = PriceReductionRequest(
            request_id=request_id,
            leader_id=data.leader_id,
            manager_id=data.manager_id,
            author_id=user.id,
            client_due_date=data.client_due_date,
            comment=data.comment.strip(),
            source_calculation_id=calculation.id if calculation else None,
            items=items,
            mail_subject=f"Price reduction request · {parent.number}",
            mail_body=(
                "Dear Colleagues,\n\nPlease consider reducing the purchase prices for the positions "
                f"from your offer below. The client is waiting for our decision until {data.client_due_date.isoformat()}."
            ),
        )
        db.add(row)
        db.flush()
        review = submit_review(
            db,
            user,
            "price_reduction_leader",
            row,
            f"Запрос на снижение · заявка №{parent.number}",
            {"reduction_id": row.id, "items": items, "comment": row.comment,
             "client_due_date": row.client_due_date.isoformat()},
            request_id,
            data.leader_id,
        )
        audit(db, user, "price_reduction", row.id, "created", after=serialize(row), reason=row.comment)
        return {**reduction_view(db, row), "review_id": review.id}

    return idem(db, user, data.idempotency_key, f"price-reduction:{request_id}", data.model_dump(mode="json"), operation)


@router.get("/requests/{request_id}/price-reductions")
def list_reductions(request_id: str, db: DB, user: Actor):
    check_request(db, user, request_id)
    require_permission(db, user, "finance.purchase.read", request_id)
    rows = db.scalars(
        select(PriceReductionRequest)
        .where(PriceReductionRequest.request_id == request_id)
        .order_by(PriceReductionRequest.created_at.desc(), PriceReductionRequest.id.desc())
    ).all()
    return {"items": [reduction_view(db, row) for row in rows]}


def _reduction(db, user, reduction_id: str, permission: str = "requests.read") -> PriceReductionRequest:
    row = db.get(PriceReductionRequest, reduction_id)
    if not row:
        error("NOT_FOUND", "Запрос на снижение не найден", 404)
    check_request(db, user, row.request_id, permission)
    return row


@router.get("/price-reductions/{reduction_id}")
def get_reduction(reduction_id: str, db: DB, user: Actor):
    row = _reduction(db, user, reduction_id)
    require_permission(db, user, "finance.purchase.read", row.request_id)
    return reduction_view(db, row)


def process_reduction_decision(db, user, review: WorkflowReview, decision: str):
    """Advance the two-stage document only from its assigned workflow decision."""
    row = lock(db, PriceReductionRequest, review.entity_id)
    expected = "pending_leader" if review.kind == "price_reduction_leader" else "pending_manager"
    if row.status != expected or row.version != review.source_version:
        error("REVIEW_STALE", "Запрос на снижение изменился. Требуется новое согласование", 409)
    if decision == "rejected":
        row.status = "rejected"
        row.version += 1
        notify(db, row.author_id, f"price-reduction-rejected:{row.id}:{review.kind}",
               "Запрос на снижение отклонён", "price_reduction", row.request_id)
        audit(db, user, "price_reduction", row.id, "rejected", after=serialize(row), reason=review.reason)
        return
    if review.kind == "price_reduction_leader":
        row.status = "pending_manager"
        row.version += 1
        request = db.get(Request, row.request_id)
        submit_review(
            db,
            user,
            "price_reduction_manager",
            row,
            f"Запрос на снижение · заявка №{request.number}",
            {"reduction_id": row.id, "items": row.items, "comment": row.comment,
             "client_due_date": row.client_due_date.isoformat()},
            row.request_id,
            row.manager_id,
        )
    else:
        row.status = "approved"
        row.approved_at = utcnow()
        row.version += 1
        notify(db, row.author_id, f"price-reduction-approved:{row.id}",
               "Запрос на снижение согласован. Сформируйте письмо поставщику", "price_reduction", row.request_id)
    audit(db, user, "price_reduction", row.id, "approved_stage", after=serialize(row), reason=review.reason)


def _letter_parts(items: list[dict]):
    headers = ["Номенклатура", "Артикул", "Цена", "Срок, дней"]
    rows = [[
        item["nomenclature_name"],
        item.get("article") or "—",
        f"{item['unit_price']} {item['currency_code']}",
        item.get("delivery_days") if item.get("delivery_days") is not None else "—",
    ] for item in items]
    plain = "\t".join(headers) + "\n" + "\n".join("\t".join(map(str, row)) for row in rows)
    html = '<table border="1" cellpadding="6"><tr>' + "".join(f"<th>{escape(value)}</th>" for value in headers)
    html += "</tr>" + "".join(
        "<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in row) + "</tr>" for row in rows
    ) + "</table>"
    return plain, html


class ReductionMail(VersionCommand):
    subject: str = Field(min_length=1, max_length=250)
    body: str = Field(min_length=3, max_length=12000)
    send: bool = False


@router.post("/price-reductions/{reduction_id}/mail")
def reduction_mail(reduction_id: str, data: ReductionMail, db: DB, user: Actor):
    row = _reduction(db, user, reduction_id, "quotes.write")
    require_permission(db, user, "finance.purchase.read", row.request_id)
    if data.send and (not settings.smtp_host or not settings.smtp_from):
        error("SMTP_REQUIRED", "Настройте SMTP_HOST и SMTP_FROM на сервере для отправки писем")

    def operation():
        current = lock(db, PriceReductionRequest, reduction_id)
        check_version(current, data.version)
        if current.status not in ACTIVE_MAIL_STATUSES:
            error("REDUCTION_STATE", "Сначала завершите два согласования запроса", 409)
        current.mail_subject = data.subject.strip()
        current.mail_body = data.body.strip()
        if data.send:
            existing = db.scalar(select(SupplierMail.id).where(SupplierMail.price_reduction_id == current.id).limit(1))
            if existing:
                error("REDUCTION_MAIL_SENT", "Письмо поставщику уже сформировано", 409)
            grouped = defaultdict(list)
            for item in current.items:
                grouped[item["supplier_id"]].append(item)
            for supplier_id, items in grouped.items():
                party = db.get(Counterparty, supplier_id)
                recipients = supplier_recipients(party)
                plain, html = _letter_parts(items)
                mail = SupplierMail(
                    request_id=current.request_id,
                    supplier_id=supplier_id,
                    item_ids=list(dict.fromkeys(item["source_request_item_id"] for item in items)),
                    recipient=", ".join(recipients),
                    cc=[],
                    subject=current.mail_subject,
                    body=current.mail_body + "\n\n" + plain,
                    html_body="<p>" + escape(current.mail_body).replace("\n", "<br>") + "</p>" + html,
                    author_id=user.id,
                    kind="price_reduction",
                    price_reduction_id=current.id,
                )
                db.add(mail)
                db.flush()
                db.add(OutboxEvent(
                    event_key=f"supplier-mail:{mail.id}", kind="supplier.mail", payload={"mail_id": mail.id}
                ))
                audit(db, user, "supplier_mail", mail.id, "queued",
                      after={"recipient": mail.recipient, "price_reduction_id": current.id})
            current.status = "awaiting_price_reduction"
            current.queued_at = utcnow()
            request = lock(db, Request, current.request_id)
            if request.commercial_stage != "awaiting_price_reduction":
                before = request.commercial_stage
                request.commercial_stage = "awaiting_price_reduction"
                request.version += 1
                audit(db, user, "request", request.id, "awaiting_price_reduction",
                      before={"commercial_stage": before},
                      after={"commercial_stage": request.commercial_stage},
                      reason=f"Запрос на снижение {current.id}")
        current.version += 1
        audit(db, user, "price_reduction", current.id, "mail_saved" if not data.send else "mail_queued",
              after=serialize(current))
        return reduction_view(db, current)

    return idem(db, user, data.idempotency_key, f"price-reduction-mail:{reduction_id}",
                data.model_dump(mode="json"), operation)


def reduction_for_import(db, user, request_id: str, reduction_id: str | None):
    if not reduction_id:
        return None
    row = _reduction(db, user, reduction_id, "quotes.write")
    if row.id != reduction_id or row.request_id != request_id:
        error("NOT_FOUND", "Запрос на снижение недоступен", 404)
    require_permission(db, user, "finance.purchase.read", request_id)
    if row.status != "awaiting_price_reduction":
        error("REDUCTION_STATE", "Сначала сформируйте письмо и переведите запрос в ожидание снижения", 409)
    return row


def attach_reduction_plan(db, reduction: PriceReductionRequest | None, plan: list[dict]) -> list[dict]:
    """Bind ordinary quote preview rows to selected old quotes without weakening quote_plan checks."""
    if not reduction:
        return plan
    selected = {item["quote_item_id"]: item for item in reduction.items}
    keys = ("supplier_id", "source_request_item_id", "nomenclature_id", "packing_id", "currency_id")
    for entry in plan:
        if entry["errors"]:
            continue
        data = entry["data"]
        matches = [
            item for item in selected.values()
            if all(data.get(key) == item.get(key) for key in keys)
        ]
        if len(matches) != 1:
            entry["errors"].append("Строка не совпадает однозначно с позициями запроса на снижение")
            continue
        item = matches[0]
        old = db.get(QuoteItem, item["quote_item_id"])
        if not old or old.archived:
            entry["errors"].append("Исходная квота уже заменена или архивирована")
            continue
        if dec(data["unit_price"]) >= old.unit_price:
            entry["errors"].append("Новая цена должна быть ниже прежней цены квоты")
            continue
        data["replace_quote_item_id"] = old.id
    return plan


def apply_reduction_quotes(db, user, batch, fresh: list[dict], sheet_ids: list[str]) -> dict[str, str]:
    """Link newly recorded quote rows, supersede old ones and preserve the old→new map."""
    reduction_id = (batch.file_metadata or {}).get("price_reduction_id")
    if not reduction_id:
        return {}
    reduction = db.get(PriceReductionRequest, reduction_id)
    if not reduction:
        return {}
    new_rows = db.scalars(select(QuoteItem).where(QuoteItem.quote_id.in_(sheet_ids))).all()
    result = dict(reduction.result_map or {})
    keys = ("supplier_id", "source_request_item_id", "nomenclature_id", "packing_id", "currency_id")
    for entry in fresh:
        data = entry["data"]
        old_id = data.get("replace_quote_item_id")
        if not old_id:
            continue
        candidates = [
            row for row in new_rows
            if all(getattr(row, key) == data.get(key) for key in keys)
            and row.unit_price == dec(data["unit_price"])
        ]
        if len(candidates) != 1:
            error("REDUCTION_LINK", "Не удалось однозначно связать новую квоту с исходной позицией", 409)
        new = candidates[0]
        old = lock(db, QuoteItem, old_id)
        if old.archived:
            error("REDUCTION_STALE", "Исходная квота уже заменена другим сотрудником", 409)
        new.previous_id = old.id
        new.price_reduction_id = reduction.id
        old.archived = True
        old.archive_status = "superseded"
        old.version += 1
        audit(db, user, "quote_item", old.id, "superseded",
              before={"archived": False, "unit_price": str(old.unit_price)},
              after={"archived": True, "archive_status": "superseded", "replacement_id": new.id},
              reason=f"Запрос на снижение {reduction.id}")
        result[old.id] = new.id
    old_sheet_ids = {
        db.get(QuoteItem, old_id).quote_id
        for old_id in result
        if db.get(QuoteItem, old_id)
    }
    for sheet_id in old_sheet_ids:
        remaining = db.scalar(
            select(QuoteItem.id).where(QuoteItem.quote_id == sheet_id, QuoteItem.archived.is_(False)).limit(1)
        )
        if not remaining:
            sheet = lock(db, QuoteSheet, sheet_id)
            sheet.archived = True
            sheet.version += 1
            audit(db, user, "quote_sheet", sheet.id, "superseded", after={"archived": True})
    reduction.result_map = result
    complete = all(item["quote_item_id"] in result for item in reduction.items)
    reduction.status = "completed" if complete else "awaiting_price_reduction"
    reduction.completed_at = utcnow() if complete else None
    reduction.version += 1
    audit(db, user, "price_reduction", reduction.id, "prices_applied", after=serialize(reduction))
    return result
