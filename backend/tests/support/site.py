"""Site integration: a signed delivery of a site request, as the site sends it (flow F1)."""

import json
import time
from uuid import uuid4

import httpx
import pytest

from app.integrations.site_auth import issue_token, sign

URL = '/api/v1/integrations/site/requests'
PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'


@pytest.fixture
def site(crm):
    with crm['sessions'].begin() as db:
        _, token, secret = issue_token(db, 'test')
    return {**crm, 'token': token, 'secret': secret}


def payload(**changes):
    body = {
        'site_request_id': str(uuid4()), 'site_number': 'S-2026-000123', 'kind': 'product_rfq', 'lang': 'ru',
        'form_type': 'rfq', 'created_at': '2026-10-18T17:10:00Z',
        'page_url': 'https://www.ogk-chem.ru/rfq/', 'desired_date': '2026-11-20',
        'customer': {'name': 'Иван Петров', 'company': 'ООО «Пример-Фарма»', 'inn': '7701234567',
                     'phone': '+79991234567', 'email': 'zakupki@example.ru', 'city': 'Москва',
                     'comment': 'Нужна холодовая цепь', 'marketing_consent': False},
        'items': [{'site_product_id': 'p-1', 'brand': 'EDQM', 'catalog_number': 'Y0001897', 'cas': '28721-07-5',
                   'name': 'Oxcarbazepine CRS', 'packaging': '100 mg', 'quantity': 2, 'unit': 'pack',
                   'analogue': 'allowed', 'custom_packaging': False,
                   'page_url': 'https://www.ogk-chem.ru/product/edqm-y0001897-oxcarbazepine-crs/'}],
        'attachments': [],
        'attribution': {
            'first_touch': {'landing_url': 'https://www.ogk-chem.ru/articles/edqm-vs-usp/', 'referrer': 'https://ya.ru/',
                            'utm_source': 'yandex', 'utm_medium': 'organic', 'at': '2026-10-15T10:22:11Z'},
            'last_touch': {'utm_source': 'yandex', 'utm_medium': 'cpc', 'utm_campaign': 'edqm_standards_rf',
                           'yclid': '123456', 'at': '2026-10-18T17:00:00Z'},
            'conversion_page_url': 'https://www.ogk-chem.ru/catalog/reference-standards/edqm/', 'channel': 'cpc',
            'client_ids': {'ym_uid': '1700000000123', 'ga_client_id': '123.456'},
        },
        'future_field': 'ignored',
    }
    body.update(changes)
    return body


def deliver(env, body, files=(), *, key=None, token=None, secret=None, stamp=None, signature=None):
    request = httpx.Request('POST', 'http://crm' + URL, data={'payload': json.dumps(body, ensure_ascii=False)},
                            files=[('files', file) for file in files] or None)
    raw = request.read()
    stamp = stamp or str(int(time.time()))
    headers = {
        'Content-Type': request.headers['Content-Type'],
        'Authorization': f'Bearer {token or env["token"]}',
        'X-Site-Timestamp': stamp,
        'X-Site-Signature': signature or sign(secret or env['secret'], stamp, raw),
        'Idempotency-Key': key or body['site_request_id'],
    }
    return env['client'].post(URL, content=raw, headers=headers)
