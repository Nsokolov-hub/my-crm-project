"""Short public forms preserve usable email contacts and file-only purchase lists in F1."""

import hashlib

from tests.support.crm import login
from tests.support.site import PDF, deliver, payload


def test_email_only_contact_and_unknown_attribution(site):
    body = payload(customer={'email': 'buyer@example.ru'}, attribution={'channel': 'unknown'})
    response = deliver(site, body)
    assert response.status_code == 201, response.text
    login(site)
    window = site['client'].get(f'/api/v1/site-requests/{response.json()["id"]}/conversion').json()
    assert window['proposal']['client']['email'] == 'buyer@example.ru'
    assert window['proposal']['client']['name'] == ''
    assert not window['proposal']['title'].endswith(' — ')


def test_file_only_rfq_is_accepted_but_empty_rfq_is_refused(site):
    body = payload(items=[], customer={'email': 'buyer@example.ru'}, attachments=[{
        'file_name': 'list.pdf', 'sha256': hashlib.sha256(PDF).hexdigest(), 'size': len(PDF)}])
    assert deliver(site, body, [('list.pdf', PDF, 'application/pdf')]).status_code == 201
    assert deliver(site, payload(items=[], customer={'email': 'buyer@example.ru'})).status_code == 422
