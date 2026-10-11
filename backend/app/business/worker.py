import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.config import settings
from app.core.db import utcnow
from app.core.models import User
from app.core.security import request_predicate, task_predicate
from app.core.service import audit, notify, notify_many
from app.crm.models import Request, Task

from .models import CalendarEntry, SupplierMail

# Overdue tasks and payments are escalated in batches; visibility is checked per batch and manager.
ESCALATION_BATCH = 500


def reminders(db, now=None):
    """Tell managers about tasks two days overdue and payments not confirmed after their date."""
    from .routes import local_date, managers

    now = now or utcnow()
    today = local_date(now)
    leaders = managers(db)
    # Due before yesterday began in company time, i.e. overdue by at least two calendar days.
    overdue_before = datetime.combine(today - timedelta(days=1), datetime.min.time(),
                                      ZoneInfo(settings.company_timezone)).astimezone(timezone.utc)
    tasks = db.scalars(
        select(Task)
        .where(Task.status.in_(["assigned", "in_progress", "completion_pending"]), Task.due_at < overdue_before)
        .order_by(Task.due_at, Task.id)
        .execution_options(yield_per=ESCALATION_BATCH)
    )
    for batch in tasks.partitions():
        ids = [task.id for task in batch]
        notices = []
        for leader in leaders:
            visible = set(db.scalars(select(Task.id).where(Task.id.in_(ids), task_predicate(db, leader))))
            notices += [(leader.id, f"task-escalation:{task.id}:{task.version}",
                         f"Задача просрочена на два дня: {task.title}"[:250], "task", task.id)
                        for task in batch if task.id in visible]
        notify_many(db, notices)

    entries = db.scalars(
        select(CalendarEntry)
        .where(CalendarEntry.status.in_(["draft", "pending", "approved"]), CalendarEntry.planned_date < today)
        .order_by(CalendarEntry.planned_date, CalendarEntry.id)
        .execution_options(yield_per=ESCALATION_BATCH)
    )
    for batch in entries.partitions():
        recipients = {leader.id for leader in leaders}
        request_ids = {entry.request_id for entry in batch if entry.request_id}
        # A payment of a request is shown only to those who may read that request.
        readable = {}
        if request_ids:
            people = recipients | {entry.responsible_id for entry in batch if entry.request_id}
            for person in db.scalars(select(User).where(User.id.in_(people))):
                readable[person.id] = set(db.scalars(select(Request.id).where(
                    Request.id.in_(request_ids), request_predicate(db, person, "requests.read"))))
        notify_many(db, [
            (target_id, f"calendar-reminder:{entry.id}:{entry.version}",
             f"Платёж не подтверждён: {entry.purpose}"[:250], "calendar_entry", entry.id)
            for entry in batch for target_id in {entry.responsible_id, *recipients}
            if not entry.request_id or entry.request_id in readable.get(target_id, ())
        ])


def send_mail(db, event):
    mail = db.get(SupplierMail, event.payload["mail_id"])
    if not mail or mail.status == "sent":
        return
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = settings.smtp_from, mail.recipient, mail.subject
    if mail.cc:
        message["Cc"] = ", ".join(mail.cc)
    message["Message-ID"] = f"<crm-supplier-{mail.id}@{settings.smtp_from.rsplit('@', 1)[-1]}>"
    message.set_content(mail.body)
    message.add_alternative(mail.html_body, subtype="html")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)
    mail.status, mail.sent_at = "sent", utcnow()
    mail.version += 1
    notify(
        db,
        mail.author_id,
        f"supplier-mail-sent:{mail.id}",
        "Запрос поставщику отправлен",
        "request",
        mail.request_id,
    )
    audit(db, None, "supplier_mail", mail.id, "sent", after={"recipient": mail.recipient, "cc": mail.cc})
