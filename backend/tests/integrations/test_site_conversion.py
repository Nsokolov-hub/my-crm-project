"""«Создать заявку»: the client is found or created, lines move one to one, one command does it all."""

import hashlib
from uuid import uuid4

from sqlalchemy import select

from app.communication.models import FileRecord
from app.core.models import PermissionGrant
from app.crm.models import Contact, Counterparty, Request, RequestItem, Task
from app.integrations.models import SiteRequest
from tests.support.crm import login
from tests.support.site import PDF, deliver, payload


def received(site, **changes) -> str:
    response = deliver(site, payload(**changes))
    assert response.status_code == 201, response.text
    return response.json()['id']


def command(site, path, body, status=200):
    response = site['client'].post(f'/api/v1/site-requests/{path}', json=body, headers={'Idempotency-Key': str(uuid4())})
    assert response.status_code == status, response.text
    return response.json()


def party(site, **values) -> str:
    with site['sessions'].begin() as db:
        row = Counterparty(owner_id=values.pop('owner_id', site['admin'].id), **values)
        db.add(row)
        db.flush()
        return row.id


def conversion_body(window, **changes):
    proposal = window['proposal']
    body = {'version': 1, 'client': proposal['client'], 'contact': proposal['contact'], 'title': proposal['title'],
            'items': proposal['items']}
    body.update(changes)
    return {key: value for key, value in body.items() if value is not None}


def test_matches_are_ranked_by_inn_phone_email_and_name(site):
    by_inn = party(site, name='Пример-Фарма (головной офис)', tax_id='7701234567')
    by_phone = party(site, name='Другая лаборатория', phone='+7 (999) 123-45-67')
    by_name = party(site, name='АО "Пример Фарма"')
    by_contact = party(site, name='Холдинг')
    with site['sessions'].begin() as db:
        db.add(Contact(client_id=by_contact, name='Закупки', email='Zakupki@Example.ru'))
    party(site, name='Совсем не тот клиент', tax_id='7707083893')
    site_request = received(site)
    login(site)
    window = site['client'].get(f'/api/v1/site-requests/{site_request}/conversion').json()
    found = {row['id']: row['reasons'] for row in window['matches']}
    assert list(found)[0] == by_inn and found[by_inn] == ['ИНН', 'Название']
    assert found[by_phone] == ['Телефон'] and found[by_name] == ['Название'] and found[by_contact] == ['Контакт']
    assert len(found) == 4
    assert window['proposal']['client'] == {'name': 'ООО «Пример-Фарма»', 'tax_id': '7701234567', 'country': None,
                                            'city': 'Москва', 'phone': '+79991234567', 'email': 'zakupki@example.ru'}
    line = window['proposal']['items'][0]
    assert (line['description'], line['article'], line['quantity'], line['unit'], line['allow_analogue']) == (
        'Oxcarbazepine CRS — EDQM', 'Y0001897', '2', 'pcs', True)


def test_new_client_contact_and_request_in_one_command(site):
    body = payload(attachments=[{'file_name': 'spec.pdf', 'sha256': hashlib.sha256(PDF).hexdigest(),
                                 'size': len(PDF)}])
    body['items'][0]['analogue'] = 'requested'
    body['items'][0]['custom_packaging'] = True
    response = deliver(site, body, [('spec.pdf', PDF, 'application/pdf')])
    site_request = response.json()['id']
    login(site)
    command(site, f'{site_request}/assign', {'assignee_id': site['admin'].id, 'version': 1})
    window = site['client'].get(f'/api/v1/site-requests/{site_request}/conversion').json()
    assert window['matches'] == [] and window['owner']['id'] == site['admin'].id
    result = command(site, f'{site_request}/convert', conversion_body(window, version=2))
    created = result['created']
    assert result['status'] == 'converted' and result['request_number'] == created['request_number']
    with site['sessions']() as db:
        client = db.get(Counterparty, created['client_id'])
        assert (client.name, client.tax_id, client.source, client.details, client.client_base) == (
            'ООО «Пример-Фарма»', '7701234567', 'Сайт', {'city': 'Москва'}, 'working')
        contact = db.get(Contact, created['contact_id'])
        assert (contact.name, contact.phone, contact.email, contact.client_id) == (
            'Иван Петров', '+79991234567', 'zakupki@example.ru', client.id)
        request = db.get(Request, created['request_id'])
        assert (request.source, request.owner_id, request.contact_id) == ('Сайт', site['admin'].id, contact.id)
        item = db.scalar(select(RequestItem).where(RequestItem.request_id == request.id))
        assert (item.description, item.article, item.cas, item.packaging, item.unit) == (
            'Oxcarbazepine CRS — EDQM', 'Y0001897', '28721-07-5', '100 mg', 'pcs')
        assert item.allow_analogue is True and item.desired_at is not None
        assert item.comment == 'Клиент просит подобрать аналог\nФасовка указана клиентом'
        assert item.source_values == ['EDQM', 'Подберите аналог']
        copy = db.scalar(select(FileRecord).where(FileRecord.request_id == request.id))
        assert copy.name == 'spec.pdf' and copy.status == 'quarantined'
        assert db.scalar(select(FileRecord).where(FileRecord.site_request_id == site_request))
        task = db.scalar(select(Task).where(Task.entity_type == 'site_request', Task.entity_id == site_request))
        assert task.status == 'completed'
    detail = site['client'].get(f'/api/v1/requests/{created["request_id"]}').json()
    assert detail['source'] == 'Сайт'
    assert detail['site_request']['number'] == 'W-1' and detail['site_request']['channel'] == 'cpc'


def test_found_client_and_its_contact_are_reused(site):
    client = party(site, name='Пример-Фарма', tax_id='7701234567', client_base='cold')
    with site['sessions'].begin() as db:
        contact = Contact(client_id=client, name='Иван Петров', phone='8 999 123 45 67')
        db.add(contact)
        db.flush()
        contact_id = contact.id
    site_request = received(site)
    login(site)
    window = site['client'].get(f'/api/v1/site-requests/{site_request}/conversion').json()
    match = window['matches'][0]
    assert match['id'] == client and match['matched_contact_id'] == contact_id
    result = command(site, f'{site_request}/convert', conversion_body(
        window, client=None, contact=None, client_id=client, contact_id=contact_id))
    with site['sessions']() as db:
        assert len(db.scalars(select(Counterparty)).all()) == 1
        assert db.get(Counterparty, client).client_base == 'working'
        assert db.get(Request, result['created']['request_id']).client_id == client


def test_converted_request_is_not_converted_twice(site):
    site_request = received(site)
    login(site)
    window = site['client'].get(f'/api/v1/site-requests/{site_request}/conversion').json()
    command(site, f'{site_request}/convert', conversion_body(window))
    again = command(site, f'{site_request}/convert', conversion_body(window, version=2), status=409)
    assert again['code'] == 'SITE_REQUEST_CLOSED'
    with site['sessions']() as db:
        assert len(db.scalars(select(Request)).all()) == 1


def test_client_must_be_found_or_new_but_not_both(site):
    site_request = received(site)
    login(site)
    window = site['client'].get(f'/api/v1/site-requests/{site_request}/conversion').json()
    response = site['client'].post(f'/api/v1/site-requests/{site_request}/convert', headers={
        'Idempotency-Key': str(uuid4())}, json=conversion_body(window, client_id=str(uuid4())))
    assert response.status_code == 422
    with site['sessions']() as db:
        assert db.scalar(select(SiteRequest)).status == 'new'


def test_other_managers_client_is_reported_without_its_card(site):
    party(site, name='Пример-Фарма', tax_id='7701234567', owner_id=site['admin'].id)
    site_request = received(site)
    with site['sessions'].begin() as db:
        db.add_all(PermissionGrant(user_id=site['manager'].id, code=code, scope='all')
                   for code in ('site_requests.read', 'site_requests.write'))
    login(site, 'manager@example.com')
    window = site['client'].get(f'/api/v1/site-requests/{site_request}/conversion').json()
    assert window['matches'] == []
    assert window['hidden'] == [{'reasons': ['ИНН', 'Название'], 'owner_name': 'Владелец теста'}]
