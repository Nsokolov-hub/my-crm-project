"""Personal supplier shortcuts respect the directory's access rules and never create mail."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from app.business.models import SupplierMail
from app.core.models import OutboxEvent, PermissionGrant, User
from app.crm.models import Counterparty, SupplierFavorite
from app.main import app
from tests.support.crm import login


def party(env, name='Поставщик', **fields):
    with env['sessions'].begin() as db:
        row = Counterparty(name=name, owner_id=fields.pop('owner_id', env['admin'].id),
                           kind=fields.pop('kind', 'supplier'), **fields)
        db.add(row)
        db.flush()
        return row.id


def favorite(env, supplier_id, value=True, expected=200, **extra):
    response = env['client'].put(f'/api/v1/counterparties/{supplier_id}/favorite',
                                  json={'favorite': value, **extra})
    assert response.status_code == expected, response.text
    return response.json()


def listing(env, **params):
    response = env['client'].get('/api/v1/counterparties', params={'favorite': 'true', **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_favorites_are_personal_idempotent_and_do_not_change_shared_card_or_send_mail(crm):
    supplier_id = party(crm, kind='both')
    with crm['sessions'].begin() as db:
        db.add(PermissionGrant(user_id=crm['manager'].id, code='clients.read', scope='all'))
        original_version = db.get(Counterparty, supplier_id).version
    login(crm)
    assert favorite(crm, supplier_id) == {'id': supplier_id, 'is_favorite': True}
    favorite(crm, supplier_id)
    assert listing(crm)['total'] == 1
    assert crm['client'].get(f'/api/v1/counterparties/{supplier_id}').json()['is_favorite'] is True
    login(crm, 'manager@example.com')
    assert listing(crm)['total'] == 0
    assert crm['client'].get(f'/api/v1/counterparties/{supplier_id}').json()['is_favorite'] is False
    favorite(crm, supplier_id)
    login(crm)
    favorite(crm, supplier_id, False)
    favorite(crm, supplier_id, False)
    assert listing(crm)['total'] == 0
    login(crm, 'manager@example.com')
    assert listing(crm)['items'][0]['id'] == supplier_id
    with crm['sessions']() as db:
        assert db.scalar(select(func.count()).select_from(SupplierFavorite)) == 1
        assert db.get(Counterparty, supplier_id).version == original_version
        assert db.scalar(select(func.count()).select_from(SupplierMail)) == 0
        assert db.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.kind == 'supplier.mail')) == 0


def test_own_scope_cannot_mark_or_discover_a_foreign_supplier_and_write_rights_are_unnecessary(crm):
    hidden = party(crm, 'Чужой')
    own = party(crm, 'Свой', owner_id=crm['manager'].id)
    with crm['sessions'].begin() as db:
        db.add(PermissionGrant(user_id=crm['manager'].id, code='clients.write', scope='all', allow=False))
        # A stale favorite must not circumvent the directory predicate after access is lost.
        db.add(SupplierFavorite(user_id=crm['manager'].id, supplier_id=hidden))
    login(crm, 'manager@example.com')
    assert favorite(crm, hidden, expected=404)['code'] == 'NOT_FOUND'
    favorite(crm, own)
    assert [row['id'] for row in listing(crm)['items']] == [own]
    assert hidden not in str(listing(crm))
    favorite(crm, own, user_id=crm['admin'].id, expected=422)


def test_read_permission_is_required(crm):
    supplier_id = party(crm, owner_id=crm['manager'].id)
    with crm['sessions'].begin() as db:
        db.add(PermissionGrant(user_id=crm['manager'].id, code='clients.read', scope='all', allow=False))
    login(crm, 'manager@example.com')
    favorite(crm, supplier_id, expected=403)


def test_favorites_are_filtered_before_search_sort_and_pagination(crm):
    login(crm)
    for number in range(6):
        party(crm, f'A{number} обычный')
    ids = [party(crm, name, kind=kind) for name, kind in [('Бета', 'supplier'), ('Вектор', 'both'),
                                                       ('Гамма', 'supplier')]]
    for supplier_id in ids:
        favorite(crm, supplier_id)
    first = listing(crm, page=1, page_size=2, kind='supplier')
    assert first['total'] == 3 and [row['id'] for row in first['items']] == ids[:2]
    assert all(row['is_favorite'] is True for row in first['items'])
    assert [row['id'] for row in listing(crm, page=2, page_size=2)['items']] == ids[2:]
    assert [row['id'] for row in listing(crm, q='Вектор')['items']] == [ids[1]]
    assert [row['id'] for row in listing(crm, direction='desc')['items']] == list(reversed(ids))


def test_archived_merged_non_supplier_and_cold_cards_do_not_enter_shortcuts(crm):
    login(crm)
    client = party(crm, kind='client')
    archived = party(crm, archived=True)
    cold = party(crm, client_base='cold')
    merged = party(crm, merged_into_id=archived)
    favorite(crm, client, expected=422)
    favorite(crm, archived, expected=409)
    favorite(crm, cold, expected=409)
    favorite(crm, merged, expected=409)
    changed = party(crm)
    favorite(crm, changed)
    with crm['sessions'].begin() as db:
        db.get(Counterparty, changed).archived = True
    assert listing(crm)['total'] == 0
    favorite(crm, changed, False)
    assert crm['client'].get(f'/api/v1/counterparties/{changed}').json()['is_favorite'] is False


def test_merging_a_duplicate_preserves_personal_favorites_without_duplicates(crm):
    source = party(crm, 'Дубль')
    target = party(crm, 'Основной')
    with crm['sessions'].begin() as db:
        db.add(PermissionGrant(user_id=crm['manager'].id, code='clients.read', scope='all'))
    login(crm, 'manager@example.com')
    favorite(crm, source)
    login(crm)
    favorite(crm, source)
    favorite(crm, target)
    response = crm['client'].post(f'/api/v1/counterparties/{source}/merge', json={
        'version': 1, 'target_id': target, 'reason': 'Дублирующий поставщик', 'idempotency_key': str(uuid4())})
    assert response.status_code == 200, response.text
    assert response.json()['is_favorite'] is True
    assert [row['id'] for row in listing(crm)['items']] == [target]
    login(crm, 'manager@example.com')
    assert [row['id'] for row in listing(crm)['items']] == [target]
    with crm['sessions']() as db:
        assert db.scalar(select(func.count()).select_from(SupplierFavorite)) == 2
        assert not db.scalars(select(SupplierFavorite).where(SupplierFavorite.supplier_id == source)).all()


def test_postgres_favorite_insert_does_not_deadlock_account_deactivation_and_reassignment(crm):
    engine = crm['sessions'].kw['bind']
    if engine.dialect.name != 'postgresql':
        pytest.skip('Row/FK lock compatibility requires PostgreSQL')
    supplier_id = party(crm, owner_id=crm['manager'].id)
    login(crm)
    employee = TestClient(app)
    login({'client': employee}, 'manager@example.com')
    account_locked, insert_started = Event(), Event()

    def after_execute(_connection, _cursor, statement, _parameters, _context, _many):
        if 'FROM users' in statement and ('FOR UPDATE' in statement or 'FOR NO KEY UPDATE' in statement):
            account_locked.set()
            assert insert_started.wait(5), 'Favorite did not reach its FK insert while the account was locked'

    def before_execute(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith('INSERT INTO supplier_favorites'):
            insert_started.set()

    event.listen(engine, 'after_cursor_execute', after_execute)
    event.listen(engine, 'before_cursor_execute', before_execute)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            deactivation = workers.submit(crm['client'].patch,
                f"/api/v1/admin/users/{crm['manager'].id}", json={
                    'version': crm['manager'].version, 'active': False, 'reassign_to': crm['admin'].id,
                    'reason': 'Проверка одновременного сохранения личного избранного'})
            assert account_locked.wait(5), 'Deactivation did not lock the account'
            preference = workers.submit(employee.put, f'/api/v1/counterparties/{supplier_id}/favorite',
                                        json={'favorite': True})
            preferred = preference.result(timeout=15)
            deactivated = deactivation.result(timeout=15)
        assert preferred.status_code == 200, preferred.text
        assert deactivated.status_code == 200, deactivated.text
    finally:
        event.remove(engine, 'after_cursor_execute', after_execute)
        event.remove(engine, 'before_cursor_execute', before_execute)
        employee.close()
    with crm['sessions']() as db:
        assert db.get(User, crm['manager'].id).active is False
        assert db.get(Counterparty, supplier_id).owner_id == crm['admin'].id
        assert db.get(SupplierFavorite, (crm['manager'].id, supplier_id)) is not None
