"""Requests for quotes mailed to suppliers."""

from datetime import timedelta
from html import escape

from fastapi import APIRouter
from pydantic import AwareDatetime, EmailStr, Field, TypeAdapter, ValidationError
from sqlalchemy import select

from app.commerce.procurement import rfq_table, supplier
from app.commerce.schemas import Command, VersionCommand
from app.core.config import settings
from app.core.db import DB, as_utc, utcnow
from app.core.errors import error
from app.core.models import OutboxEvent
from app.core.security import Actor, check_request, require_permission
from app.core.service import audit, check_version, idem, lock, page, serialize
from app.crm.models import RequestItem

from .models import SupplierMail

router = APIRouter(tags=["Платёжный календарь и заказы поставщикам"])


@router.get("/requests/{request_id}/supplier-mail/items")
def mail_items(request_id: str, db: DB, user: Actor):
    check_request(db, user, request_id, "quotes.write")
    ids = list(
        db.scalars(
            select(RequestItem.id)
            .where(
                RequestItem.request_id == request_id,
                RequestItem.archived.is_(False),
                RequestItem.quote_only.is_(False),
            )
            .order_by(RequestItem.created_at, RequestItem.id)
        )
    )
    return {"items": [{"id": entity_id} for entity_id in ids], "total": len(ids)}


def supplier_recipients(party) -> list[str]:
    """The supplier's shared address for requests (or its card address) and its manager's, if given.

    Business remark of 31 October 2026: a request for quotes goes to both.
    """
    details = party.details or {}
    found = []
    for raw in (details.get("rfq_email") or party.email, details.get("rfq_email_manager")):
        if raw is None or not str(raw).strip():
            continue
        try:
            address = str(TypeAdapter(EmailStr).validate_python(str(raw).strip()))
        except ValidationError:
            error("SUPPLIER_EMAIL", f"Исправьте почту для запросов в карточке {party.name}")
        if address.casefold() not in {row.casefold() for row in found}:
            found.append(address)
    if not found:
        error("SUPPLIER_EMAIL", f"Укажите почту для запросов в карточке {party.name}")
    return found


class MailPreviewIn(Command):
    supplier_ids: list[str] = Field(min_length=1, max_length=100)
    item_ids: list[str] = Field(min_length=1, max_length=10000)
    introduction: str = Field(default="Dear Colleagues,\n\nPlease send us offer for:", max_length=4000)
    cc: list[EmailStr] = Field(default_factory=list, max_length=100)


@router.post("/requests/{request_id}/supplier-mail/preview")
def mail_preview(request_id: str, data: MailPreviewIn, db: DB, user: Actor):
    request = check_request(db, user, request_id, "quotes.write")
    items = db.scalars(select(RequestItem).where(
        RequestItem.id.in_(set(data.item_ids)), RequestItem.request_id == request.id,
        RequestItem.archived.is_(False)).order_by(RequestItem.created_at, RequestItem.id)).all()
    if len(items) != len(set(data.item_ids)):
        error("ITEM_NOT_FOUND", "Выберите действующие позиции этой заявки")
    headers, lines = rfq_table(db, items)
    table = '<table border="1" cellpadding="6"><tr>' + "".join(f"<th>{escape(h)}</th>" for h in headers) + "</tr>"
    table += (
        "".join("<tr>" + "".join(f"<td>{escape(str(c) if c is not None else '')}</td>" for c in row) + "</tr>" for row in lines)
        + "</table>"
    )
    body = (
        data.introduction
        + "\n\n"
        + "\t".join(headers)
        + "\n"
        + "\n".join("\t".join(str(c) if c is not None else "" for c in row) for row in lines)
    )
    drafts = []
    for supplier_id in dict.fromkeys(data.supplier_ids):
        party = supplier(db, supplier_id)
        recipients = supplier_recipients(party)
        taken = {address.casefold() for address in recipients}
        drafts.append(
            {
                "supplier_id": party.id,
                "supplier_name": party.name,
                "recipient": ", ".join(recipients),
                "recipients": recipients,
                "cc": list(dict.fromkeys(str(address) for address in data.cc if str(address).casefold() not in taken)),
                "subject": f"Request {request.number}",
                "body": body,
                "table_html": table,
                "introduction": data.introduction,
            }
        )
    return {"items": drafts}


class MailSendIn(MailPreviewIn):
    subject: str = Field(min_length=1, max_length=250)


@router.post("/requests/{request_id}/supplier-mail/send")
def mail_send(request_id: str, data: MailSendIn, db: DB, user: Actor):
    if not settings.smtp_host or not settings.smtp_from:
        error("SMTP_REQUIRED", "Настройте SMTP_HOST и SMTP_FROM на сервере для отправки писем")

    def operation():
        drafts = mail_preview(request_id, data, db, user)["items"]
        result = []
        for draft in drafts:
            mail = SupplierMail(
                request_id=request_id,
                supplier_id=draft["supplier_id"],
                item_ids=list(dict.fromkeys(data.item_ids)),
                recipient=draft["recipient"],
                cc=draft["cc"],
                subject=data.subject,
                body=draft["body"],
                html_body="<p>"
                + escape(data.introduction).replace("\n", "<br>")
                + "</p>"
                + draft["table_html"],
                author_id=user.id,
            )
            db.add(mail)
            db.flush()
            db.add(
                OutboxEvent(
                    event_key=f"supplier-mail:{mail.id}", kind="supplier.mail", payload={"mail_id": mail.id}
                )
            )
            audit(
                db,
                user,
                "supplier_mail",
                mail.id,
                "queued",
                after={"recipient": mail.recipient, "cc": mail.cc, "request_id": request_id},
            )
            result.append(serialize(mail))
        return {"items": result}

    return idem(
        db, user, data.idempotency_key, f"supplier-mail:{request_id}", data.model_dump(mode="json"), operation
    )


@router.get("/requests/{request_id}/supplier-mail")
def mail_list(request_id: str, db: DB, user: Actor):
    check_request(db, user, request_id)
    require_permission(db, user, "quotes.write")
    return page(
        db,
        select(SupplierMail)
        .where(SupplierMail.request_id == request_id)
        .order_by(SupplierMail.created_at.desc()),
        1,
        100,
    )


class MailReplyIn(VersionCommand):
    replied_at: AwareDatetime | None = None
    note: str = Field(min_length=3, max_length=2000)


@router.post("/supplier-mails/{mail_id}/reply")
def mail_reply(mail_id: str, data: MailReplyIn, db: DB, user: Actor):
    """A supplier answered without prices: positions of the mail without a quote count as declined."""
    mail = db.get(SupplierMail, mail_id)
    if not mail:
        error("NOT_FOUND", "Письмо не найдено", 404)
    check_request(db, user, mail.request_id, "quotes.write")

    def operation():
        current = lock(db, SupplierMail, mail_id)
        check_version(current, data.version)
        if not current.sent_at:
            error("MAIL_NOT_SENT", "Письмо ещё не отправлено: ответа на него быть не может", 409)
        replied = data.replied_at or utcnow()
        if replied < as_utc(current.sent_at):
            error("REPLY_BEFORE_MAIL", "Ответ не может прийти раньше отправки письма", field="replied_at")
        if replied > utcnow() + timedelta(minutes=1):
            error("REPLY_FUTURE", "Дата ответа не может быть в будущем", field="replied_at")
        before = {"replied_at": current.replied_at, "reply_note": current.reply_note}
        current.replied_at, current.reply_note = replied, data.note
        current.version += 1
        audit(db, user, "supplier_mail", current.id, "reply_recorded", before,
              {"replied_at": replied, "reply_note": data.note})
        return serialize(current)

    return idem(db, user, data.idempotency_key, f"supplier-mail-reply:{mail_id}", data.model_dump(mode="json"),
                operation)
