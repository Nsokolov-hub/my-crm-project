"""The side history is complete across transfers and page boundaries, without crossing request scopes."""

from datetime import timedelta
from urllib.parse import urlencode
from uuid import uuid4

from sqlalchemy import select

from app.business.models import SupplierOrder, SupplierOrderLine
from app.commerce.models import WaveAllocation
from app.core.db import utcnow
from app.core.models import AuditEvent
from app.crm.models import RequestMember
from tests.business.test_business_nov05 import approval_of
from tests.business.test_business_oct28 import planned
from tests.business.test_wave_kinds import new_wave
from tests.support.business import cmd, wave_row
from tests.support.crm import login


def audit_event(**values):
    return AuditEvent(correlation_id=str(uuid4()), **values)


def working(crm):
    req, data, seller, supplier, plan, _, _ = planned(crm)
    execution, approval = approval_of(crm, req, data)
    cmd(crm, f"/approvals/{approval['id']}/decision", {
        "version": approval["version"], "decision": "approved", "reason": "Передача в закупки",
        "line_waves": {execution["id"]: plan["id"]}})
    [wave] = wave_row(crm, plan["id"])["working_waves"]
    [allocation] = wave_row(crm, wave["id"])["allocations"]
    return req, seller, supplier, execution, wave, allocation


def history(crm, wave_id, **query):
    response = crm['client'].get(f'/api/v1/waves/{wave_id}/history?{urlencode(query)}')
    assert response.status_code == 200, response.text
    return response.json()


def test_legacy_transfer_and_logistics_appear_in_the_correct_wave(crm):
    _, _, supplier, _, source, allocation = working(crm)
    # The manual allocation endpoint records this audit, unlike the automatic procurement handoff.
    with crm['sessions'].begin() as db:
        db.add(audit_event(entity_type='wave_allocation', entity_id=allocation['id'], action='allocate',
                          actor_id=crm['admin'].id, after=allocation, reason='Первое распределение'))
    target = new_wave(crm, supplier, kind='work')
    transferred = cmd(crm, f"/allocations/{allocation['id']}/transfer", {
        'version': allocation['version'], 'target_wave_id': target['id'], 'quantity': '2',
        'reason': 'Весь груз едет следующей поставкой'})
    [new_allocation] = transferred['items']
    cmd(crm, f"/allocations/{new_allocation['id']}/events", {
        'kind': 'ordered', 'quantity': '2', 'occurred_at': utcnow().isoformat(),
        'reason': 'Поставщик подтвердил заказ в новой волне'})
    before = history(crm, source['id'])['items']
    after = history(crm, target['id'])['items']
    [outgoing] = [row for row in before if row['title'] == 'Позиция перенесена из волны']
    [incoming] = [row for row in after if row['title'] == 'Позиция перенесена в волну']
    assert outgoing['comment'] == incoming['comment'] == 'Весь груз едет следующей поставкой'
    assert source['number'] in incoming['details'] and target['number'] in outgoing['details']
    assert '2 pcs' in incoming['details']
    assert any(row['title'] == 'Количество распределено в волну' for row in before)
    assert not any(row['title'] == 'Количество распределено в волну' for row in after)
    assert not any(row['title'] == 'Заказ размещён' for row in before)
    [ordered] = [row for row in after if row['title'] == 'Заказ размещён']
    assert ordered['comment'] == 'Поставщик подтвердил заказ в новой волне'


def test_order_status_history_uses_allocation_at_the_time_not_current_membership(crm):
    _, seller, supplier, execution, source, allocation = working(crm)
    target = new_wave(crm, supplier, kind='work')
    moved = cmd(crm, f"/allocations/{allocation['id']}/transfer", {
        'version': allocation['version'], 'target_wave_id': target['id'], 'quantity': '2',
        'reason': 'Перенос всей позиции'})
    start = utcnow() - timedelta(days=4)
    with crm['sessions'].begin() as db:
        db.get(WaveAllocation, allocation['id']).created_at = start
        db.get(WaveAllocation, moved['items'][0]['id']).created_at = start + timedelta(days=2)
        transfer = db.scalar(select(AuditEvent).where(AuditEvent.entity_type == 'wave_allocation',
                   AuditEvent.entity_id == allocation['id'], AuditEvent.action == 'transfer'))
        transfer.created_at = start + timedelta(days=2)
        order = SupplierOrder(number=f'HISTORY-{uuid4()}', supplier_id=supplier['id'], seller_id=seller['id'],
                              currency='RUB', total=0, expected_date=start.date(), author_id=crm['admin'].id, snapshot={})
        db.add(order)
        db.flush()
        line = SupplierOrderLine(order_id=order.id, execution_id=execution['id'], quantity=2, unit_price=0,
                                 status='arrived_ru', snapshot={})
        db.add(line)
        db.flush()
        for day, old, new, reason in [(1, 'waiting', 'delivery', 'Отправка в первой волне'),
                                      (3, 'delivery', 'arrived_ru', 'Прибытие уже после переноса')]:
            db.add(audit_event(entity_type='supplier_order_line', entity_id=line.id, action='status',
                              created_at=start + timedelta(days=day), actor_id=crm['admin'].id,
                              before={'status': old}, after={'status': new, 'quantity': '2'}, reason=reason))
    before, after = history(crm, source['id'])['items'], history(crm, target['id'])['items']
    assert 'Отправка в первой волне' in [row['comment'] for row in before]
    assert 'Прибытие уже после переноса' not in [row['comment'] for row in before]
    assert 'Прибытие уже после переноса' in [row['comment'] for row in after]
    assert 'Отправка в первой волне' not in [row['comment'] for row in after]
    # Cancelling a wave deactivates its allocations without transferring them. Its earlier history remains.
    target = wave_row(crm, target['id'])
    cmd(crm, f"/waves/{target['id']}", {'version': target['version'], 'status': 'cancelled',
                                     'reason': 'Отмена поставки'}, method='patch')
    with crm['sessions'].begin() as db:
        cancelled = db.scalar(select(AuditEvent).where(AuditEvent.entity_type == 'wave',
                              AuditEvent.entity_id == target['id'], AuditEvent.action == 'update'))
        cancelled.created_at = start + timedelta(days=4)
        db.add(audit_event(entity_type='supplier_order_line', entity_id=line.id, action='status',
                          created_at=start + timedelta(days=5), actor_id=crm['admin'].id,
                          before={'status': 'arrived_ru'}, after={'status': 'delivered'}, reason='После отмены волны'))
    cancelled_history = history(crm, target['id'])['items']
    assert 'Прибытие уже после переноса' in [row['comment'] for row in cancelled_history]
    assert 'После отмены волны' not in [row['comment'] for row in cancelled_history]


def test_pagination_does_not_truncate_or_skip_history_when_new_events_arrive(crm):
    _, _, _, _, wave, _ = working(crm)
    with crm['sessions'].begin() as db:
        stamp = utcnow()
        for n in range(215):
            db.add(audit_event(entity_type='wave', entity_id=wave['id'], action='update', actor_id=crm['admin'].id,
                              created_at=stamp + timedelta(microseconds=n), before={'route': 'Старый маршрут'},
                              after={'route': f'Маршрут {n}'}, reason=f'Комментарий {n}'))
    first = history(crm, wave['id'], limit=73)
    assert len(first['items']) == 73 and first['has_more']
    seen = [row['id'] for row in first['items']]
    with crm['sessions'].begin() as db:
        latest = audit_event(entity_type='wave', entity_id=wave['id'], action='update', actor_id=crm['admin'].id,
                            before={'number': wave['number']}, after={'number': 'Новое имя'}, reason='Новое событие')
        db.add(latest)
        db.flush()
        latest_id = latest.id
    cursor = first['next_cursor']
    while cursor:
        page = history(crm, wave['id'], limit=73, cursor=cursor)
        seen.extend(row['id'] for row in page['items'])
        cursor = page['next_cursor']
    assert len(seen) == len(set(seen)) and len(seen) >= 215
    assert latest_id not in seen
    assert history(crm, wave['id'], limit=1)['items'][0]['id'] == latest_id
    response = crm['client'].get(f"/api/v1/waves/{wave['id']}/history?cursor=not-a-cursor")
    assert response.status_code == 422


def test_request_scope_filters_private_events_before_pagination(crm):
    req, _, _, _, wave, _ = working(crm)
    private_marker = 'Частный комментарий заявки'
    [created] = [row for row in history(crm, wave['id'])['items'] if row['title'] == 'Волна создана']
    assert created['comment'] == f"Подтверждён заказ по заявке №{req['number']}"
    with crm['sessions'].begin() as db:
        db.add(audit_event(entity_type='request', entity_id=req['id'], action='assign_wave', actor_id=crm['admin'].id,
                          before={'wave_id': None}, after={'wave_id': wave['id']}, reason=private_marker))
    login(crm, 'manager@example.com')
    own = history(crm, wave['id'], limit=1)
    assert own['items'] and private_marker not in str(own)
    all_rows = history(crm, wave['id'])['items']
    assert not any('Заявка №' in str(row) for row in all_rows)
    assert not any('заявке №' in str(row) for row in all_rows)
    [shared_wave_creation] = [row for row in all_rows if row['id'] == created['id']]
    assert shared_wave_creation['comment'] is None  # Keep the shared wave event, hide its private generated reference.
    with crm['sessions'].begin() as db:
        db.add(RequestMember(request_id=req['id'], user_id=crm['manager'].id))
    shared = history(crm, wave['id'], limit=1)
    assert shared['items'][0]['comment'] == private_marker
    [visible_creation] = [row for row in history(crm, wave['id'])['items'] if row['id'] == created['id']]
    assert visible_creation['comment'] == created['comment']
