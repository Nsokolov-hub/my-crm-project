import smtplib
from email.message import EmailMessage

from sqlalchemy import select

from app.core.config import settings
from app.core.db import utcnow
from app.core.service import audit, notify
from app.crm.models import Task

from .models import CalendarEntry, SupplierMail


def reminders(db, now=None):
    from .routes import local_date, managers

    now = now or utcnow()
    today = local_date(now)
    leaders = managers(db)
    for task in db.scalars(
        select(Task).where(Task.status.in_(["assigned", "in_progress", "completion_pending"]))
    ):
        if (today - local_date(task.due_at)).days >= 2:
            for leader in leaders:
                from app.core.security import task_predicate

                if not db.scalar(select(Task.id).where(Task.id == task.id, task_predicate(db, leader))):
                    continue
                notify(
                    db,
                    leader.id,
                    f"task-escalation:{task.id}:{task.version}",
                    f"Задача просрочена на два дня: {task.title}"[:250],
                    "task",
                    task.id,
                )
    for entry in db.scalars(
        select(CalendarEntry).where(CalendarEntry.status.in_(["draft", "pending", "approved"]))
    ):
        if (today - entry.planned_date).days < 1:
            continue
        for target_id in {entry.responsible_id, *(leader.id for leader in leaders)}:
            if entry.request_id:
                from app.core.models import User
                from app.core.security import has_request_permission

                if not has_request_permission(db, db.get(User, target_id), entry.request_id, "requests.read"):
                    continue
            notify(
                db,
                target_id,
                f"calendar-reminder:{entry.id}:{entry.version}",
                f"Платёж не подтверждён: {entry.purpose}"[:250],
                "calendar_entry",
                entry.id,
            )


def send_mail(db, event):
    mail = db.get(SupplierMail, event.payload["mail_id"])
    if not mail or mail.status == "sent":
        return
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = settings.smtp_from, mail.recipient, mail.subject
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
    audit(db, None, "supplier_mail", mail.id, "sent", after={"recipient": mail.recipient})
