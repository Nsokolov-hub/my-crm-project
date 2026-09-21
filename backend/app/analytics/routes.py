from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db, utcnow
from app.core.models import User
from app.core.security import current_user, request_predicate, require_permission, scope_for, task_predicate
from app.core.service import audit, plain, serialize
from app.crm.models import Call, Counterparty, Request, Task

router = APIRouter(tags=['Аналитика'])


def report_data(db: Session, user: User, date_from: date | None, date_to: date | None, owner_id: str | None, include_test: bool) -> dict[str, Any]:
    from app.commerce.models import CommercialDocument, Payment, PaymentAllocation, PaymentReversal
    require_permission(db, user, 'analytics.read')
    start = datetime.combine(date_from or (date.today() - timedelta(days=30)), time.min, ZoneInfo(settings.company_timezone)).astimezone(timezone.utc)
    finish = datetime.combine((date_to or date.today()) + timedelta(days=1), time.min, ZoneInfo(settings.company_timezone)).astimezone(timezone.utc)
    allowed = select(Request.id).where(request_predicate(db, user))
    if not include_test:
        allowed = allowed.where(Request.is_test.is_(False))
    if owner_id:
        allowed = allowed.where(Request.owner_id == owner_id)
    visible = Request.id.in_(allowed)
    cohort = select(Request).where(visible, Request.created_at >= start, Request.created_at < finish)
    new_count = db.scalar(select(func.count()).select_from(cohort.subquery())) or 0
    stage_counts = db.execute(select(Request.commercial_stage, func.count()).where(visible, Request.created_at >= start, Request.created_at < finish).group_by(Request.commercial_stage)).all()
    closed_filter = [visible, Request.closed_at >= start, Request.closed_at < finish]
    closed = db.scalar(select(func.count()).select_from(Request).where(*closed_filter)) or 0
    won_closed = db.scalar(select(func.count()).select_from(Request).where(*closed_filter, Request.sale_confirmed_at.is_not(None), Request.commercial_stage != 'closed_lost')) or 0
    sales = db.scalar(select(func.count()).select_from(Request).where(visible, Request.sale_confirmed_at >= start, Request.sale_confirmed_at < finish)) or 0
    active = db.scalar(select(func.count()).select_from(Request).where(visible, Request.closed_at.is_(None), Request.archived.is_(False))) or 0
    tasks = db.scalars(select(Task).where(task_predicate(db, user), Task.status.in_(['assigned', 'in_progress'])).order_by(Task.due_at).limit(8)).all()
    overdue = db.scalar(select(func.count()).select_from(Task).where(task_predicate(db, user), Task.status.in_(['assigned', 'in_progress']), Task.due_at < utcnow())) or 0
    with_tasks = select(Task.entity_id).where(Task.entity_type == 'request', Task.status.in_(['assigned', 'in_progress']))
    missing = db.scalar(select(func.count()).select_from(Request).where(visible, Request.closed_at.is_(None), Request.id.not_in(with_tasks))) or 0
    invoice_ids = select(CommercialDocument.id).where(CommercialDocument.request_id.in_(allowed), CommercialDocument.kind == 'invoice', CommercialDocument.status != 'cancelled')
    totals = dict(db.execute(select(CommercialDocument.currency, func.sum(CommercialDocument.total)).where(CommercialDocument.id.in_(invoice_ids)).group_by(CommercialDocument.currency)).all())
    paid = dict(db.execute(select(CommercialDocument.currency, func.sum(PaymentAllocation.amount)).join(PaymentAllocation, PaymentAllocation.invoice_id == CommercialDocument.id).join(Payment, Payment.id == PaymentAllocation.payment_id).where(CommercialDocument.id.in_(invoice_ids), Payment.status == 'confirmed').group_by(CommercialDocument.currency)).all())
    reversed_totals = dict(db.execute(select(CommercialDocument.currency, func.sum(PaymentReversal.amount)).join(PaymentAllocation, PaymentAllocation.invoice_id == CommercialDocument.id).join(PaymentReversal, PaymentReversal.allocation_id == PaymentAllocation.id).where(CommercialDocument.id.in_(invoice_ids)).group_by(CommercialDocument.currency)).all())
    receivables = [{'currency': currency, 'total': total, 'paid': paid.get(currency, Decimal(0)) - reversed_totals.get(currency, Decimal(0)), 'balance': total - paid.get(currency, Decimal(0)) + reversed_totals.get(currency, Decimal(0))} for currency, total in totals.items()]
    recent = db.scalars(cohort.order_by(Request.created_at.desc()).limit(8)).all()
    recent_view = [{**serialize(r), 'client_name': db.get(Counterparty, r.client_id).name, 'owner_name': db.get(User, r.owner_id).name} for r in recent]
    clients = select(Request.client_id).where(visible)
    calls = db.execute(select(Call.author_id, Call.result, func.count()).where(Call.client_id.in_(clients), Call.cancelled.is_(False), Call.occurred_at >= start, Call.occurred_at < finish).group_by(Call.author_id, Call.result)).all()
    manager_activity = [{'user_id': actor, 'name': db.get(User, actor).name, 'result': result, 'count': count} for actor, result, count in calls]
    return plain({'period': {'from': start, 'to': finish - timedelta(microseconds=1), 'basis': 'Новые заявки — дата создания; продажи — первое согласование; конверсия — дата закрытия; задолженность — текущие остатки всех счетов', 'timezone': settings.company_timezone, 'generated_at': utcnow(), 'include_test': include_test, 'owner_id': owner_id}, 'new_requests': new_count, 'active_requests': active, 'closed_requests': closed, 'sales': sales, 'conversion': str((Decimal(won_closed) / Decimal(closed) * 100).quantize(Decimal('.01'))) if closed else None, 'conversion_numerator': won_closed, 'conversion_denominator': closed, 'overdue_tasks': overdue, 'without_next_action': missing, 'stages': [{'stage': stage, 'count': count} for stage, count in stage_counts], 'receivables': receivables, 'recent_requests': recent_view, 'tasks': [{**serialize(t), 'assignee_name': db.get(User, t.assignee_id).name} for t in tasks], 'manager_activity': manager_activity, 'drilldown': {'new_requests': f'/analytics/requests?metric=new&date_from={start.date()}&date_to={(finish-timedelta(days=1)).date()}&include_test={str(include_test).lower()}', 'sales': '/analytics/requests?metric=sales', 'closed_requests': '/analytics/requests?metric=closed', 'overdue_tasks': '/tasks?overdue=true', 'receivables': '/analytics/invoices'}})


@router.get('/analytics/dashboard')
def dashboard(date_from: date | None = None, date_to: date | None = None, owner_id: str | None = None, include_test: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return report_data(db, user, date_from, date_to, owner_id, include_test)


@router.get('/analytics/requests')
def drilldown(metric: str = 'new', date_from: date | None = None, date_to: date | None = None, owner_id: str | None = None, include_test: bool = False, page: int = 1, page_size: int = 25, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.core.service import page as paginate
    require_permission(db, user, 'analytics.read')
    start = datetime.combine(date_from or (date.today() - timedelta(days=30)), time.min, ZoneInfo(settings.company_timezone)).astimezone(timezone.utc)
    finish = datetime.combine((date_to or date.today()) + timedelta(days=1), time.min, ZoneInfo(settings.company_timezone)).astimezone(timezone.utc)
    col = {'new': Request.created_at, 'sales': Request.sale_confirmed_at, 'closed': Request.closed_at}.get(metric, Request.created_at)
    stmt = select(Request).where(request_predicate(db, user), col >= start, col < finish)
    if not include_test:
        stmt = stmt.where(Request.is_test.is_(False))
    if owner_id:
        stmt = stmt.where(Request.owner_id == owner_id)
    return paginate(db, stmt.order_by(col.desc()), page, page_size)


@router.get('/analytics/invoices')
def invoices(page: int = 1, page_size: int = 25, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.commerce.models import CommercialDocument
    from app.commerce.payments import invoice_balance
    from app.core.service import page as paginate
    require_permission(db, user, 'analytics.read')
    result = paginate(db, select(CommercialDocument).where(CommercialDocument.kind == 'invoice', CommercialDocument.status != 'cancelled', CommercialDocument.request_id.in_(select(Request.id).where(request_predicate(db, user)))).order_by(CommercialDocument.created_at.desc()), page, page_size)
    for row in result['items']:
        row.update(invoice_balance(db, db.get(CommercialDocument, row['id'])))
        row.pop('snapshot', None)
        row.pop('files', None)
    return result


@router.get('/analytics/export.xlsx')
def export(date_from: date | None = None, date_to: date | None = None, owner_id: str | None = None, include_test: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    from app.crm.imports import xlsx
    require_permission(db, user, 'exports.download')
    data = report_data(db, user, date_from, date_to, owner_id, include_test)
    rows = [['Показатель', 'Значение'], ['Период с', data['period']['from']], ['Период по', data['period']['to']], ['Основа периода', data['period']['basis']], ['Часовой пояс', data['period']['timezone']], ['Новые заявки', data['new_requests']], ['Продажи', data['sales']], ['Закрытые заявки', data['closed_requests']], ['Продаж среди закрытых', data['conversion_numerator']], ['Конверсия, %', data['conversion']], ['Просроченные задачи', data['overdue_tasks']]]
    rows += [[f'К оплате, {r["currency"]}', r['balance']] for r in data['receivables']]
    audit(db, user, 'analytics', user.id, 'exported', after=data['period'])
    return Response(xlsx(rows), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': 'attachment; filename="crm-analytics.xlsx"'})
