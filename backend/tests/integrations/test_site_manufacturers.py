"""Flow F5: the site reads the manufacturer registry with its spellings, page by page, signed like F1."""

import time
from urllib.parse import urlencode

from sqlalchemy import select

from app.commerce.models import Manufacturer, ManufacturerAlias
from app.integrations.site_auth import sign

URL = '/api/v1/integrations/site/manufacturers'


def call(env, params=None, *, secret=None, target=None):
    query = urlencode(params or {})
    path = URL + (f'?{query}' if query else '')
    stamp = str(int(time.time()))
    signature = sign(secret or env['secret'], stamp, f'GET {target or path}'.encode())
    return env['client'].get(path, headers={'Authorization': f'Bearer {env["token"]}', 'X-Site-Timestamp': stamp,
                                            'X-Site-Signature': signature})


def maker(env, name, code, *spellings):
    with env['sessions'].begin() as db:
        row = Manufacturer(name=name, normalized_name=name.casefold(), internal_code=code)
        db.add(row)
        db.flush()
        for spelling in (name, *spellings):
            db.add(ManufacturerAlias(manufacturer_id=row.id, name=spelling, normalized_name=spelling.casefold()))
        return row.id


def test_registry_with_spellings_page_by_page(site):
    first = maker(site, 'Sigma-Aldrich', 901, 'Sigma Aldrich', 'SIAL')
    second = maker(site, 'Merck', 902)
    third = maker(site, 'LGC Standards', 903, 'LGC Dr. Ehrenstorfer')
    page = call(site, {'limit': 2})
    assert page.status_code == 200, page.text
    body = page.json()
    assert [item['id'] for item in body['items']] == [first, second]
    assert body['items'][0]['aliases'] == ['SIAL', 'Sigma Aldrich', 'Sigma-Aldrich']
    rest = call(site, {'limit': 2, 'cursor': body['next_cursor']}).json()
    assert [item['id'] for item in rest['items']] == [third] and rest['next_cursor'] is None


def test_a_new_spelling_changes_the_entry_for_the_site(site):
    entry = maker(site, 'Waters', 904)
    with site['sessions']() as db:
        before = db.get(Manufacturer, entry).updated_at
    time.sleep(0.01)
    with site['sessions'].begin() as db:
        db.add(ManufacturerAlias(manufacturer_id=entry, name='Waters Corporation',
                                 normalized_name='waters corporation'))
    with site['sessions']() as db:
        after = db.get(Manufacturer, entry).updated_at
    assert after > before
    since = call(site, {'updated_since': after.isoformat()}).json()
    assert [item['name'] for item in since['items']] == ['Waters']
    assert 'Waters Corporation' in since['items'][0]['aliases']


def test_merged_entry_points_to_the_one_that_took_it(site):
    target = maker(site, 'Honeywell', 905)
    merged = maker(site, 'Riedel-de Haën', 906)
    with site['sessions'].begin() as db:
        db.get(Manufacturer, merged).merged_into_id = target
    items = {item['id']: item for item in call(site, {}).json()['items']}
    assert items[merged]['merged_into_id'] == target


def test_signature_covers_the_query(site):
    maker(site, 'USP', 907)
    forged = call(site, {'limit': 1}, target=URL + '?limit=1000')
    assert forged.status_code == 401
    assert forged.json()['code'] == 'INTEGRATION_SIGNATURE_INVALID'
    assert call(site, {'limit': 1}, secret='wrong').status_code == 401
    with site['sessions']() as db:
        assert db.scalar(select(Manufacturer).where(Manufacturer.name == 'USP'))
