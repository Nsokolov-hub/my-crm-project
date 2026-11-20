"""Historical stock belongs to each line and never becomes customer/supplier-facing line text."""

import io
from uuid import uuid4

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.commerce.documents import customer_line
from app.commerce.procurement import rfq_table
from app.crm.models import RequestItem
from app.integrations.models import SiteRequestItem
from tests.integrations.test_site_conversion import command, conversion_body, received
from tests.support.crm import login
from tests.support.site import deliver, payload


def snapshot():
    return {
        'version': 1, 'captured_at': '2026-10-18T17:10:00Z', 'availability': 'in_stock', 'origin': 'supplier',
        'site_product_id': 'p-1', 'site_packaging_id': 'pack-1', 'packaging_match': 'exact',
        'offers': [{'source': 'chimmed', 'source_name': 'Химмед', 'offer_id': 'offer-1',
                    'external_id': 'source-offer-1', 'catalog_number': 'Y0001897', 'pack': '100 mg',
                    'site_packaging_id': 'pack-1', 'match': 'exact_pack', 'seen_at': '2026-10-17T10:00:00Z',
                    'valid_until': '2026-10-27T10:00:00Z', 'quantity': None, 'url': 'https://chimmed.test/offer'}],
    }


def receive_with_stock(site):
    body = payload()
    body['items'][0].update(site_packaging_id='pack-1', stock_snapshot=snapshot())
    answer = deliver(site, body)
    assert answer.status_code == 201, answer.text
    return answer.json()['id'], body


def window(site, request_id):
    return site['client'].get(f'/api/v1/site-requests/{request_id}/conversion').json()


def transfer(site, source, proposal, mode, status=200):
    if mode == 'convert':
        return command(site, f'{source}/convert', conversion_body(proposal), status=status)
    target = received(site)
    created = command(site, f'{target}/convert', conversion_body(window(site, target)))['created']
    return command(site, f'{source}/attach', {'version': 1, 'request_id': created['request_id'],
                                            'items': proposal['proposal']['items']}, status=status)


@pytest.mark.parametrize('mode', ['convert', 'attach'])
def test_stock_survives_transfer_and_ignores_client_supplied_snapshot(site, mode):
    source, body = receive_with_stock(site)
    assert deliver(site, body).status_code == 200
    login(site)
    detail = site['client'].get(f'/api/v1/site-requests/{source}').json()
    original = detail['items'][0]['stock_snapshot']
    assert original['offers'][0]['source_name'] == 'Химмед'
    proposal = window(site, source)
    assert proposal['proposal']['items'][0]['stock_snapshot'] == original
    proposal['proposal']['items'][0]['stock_snapshot'] = {'forged': True}
    # An employee may change quantity; the historical evidence remains the original observation.
    proposal['proposal']['items'][0]['quantity'] = '7'
    result = transfer(site, source, proposal, mode)
    with site['sessions']() as db:
        items = list(db.scalars(select(RequestItem).where(RequestItem.request_id == result['created']['request_id'])))
        copied = next(item for item in items if item.stock_snapshot)
        assert copied.stock_snapshot == original
        assert copied.quantity == 7
        assert copied.source_columns == copied.source_values == []
        columns, lines = rfq_table(db, [copied])
        supplier_text = str((columns, lines))
        assert 'chimmed' not in supplier_text and 'Химмед' not in supplier_text
        assert 'stock_snapshot' not in supplier_text
        assert db.get(SiteRequestItem, detail['items'][0]['id']).stock_snapshot == original
    api_items = site['client'].get(f'/api/v1/requests/{result["created"]["request_id"]}/items').json()['items']
    assert next(item for item in api_items if item['stock_snapshot'])['stock_snapshot'] == original
    exported = site['client'].get(f'/api/v1/site-requests/{source}/export.xlsx')
    book = load_workbook(io.BytesIO(exported.content))
    text = str([list(sheet.values) for sheet in book])
    assert 'chimmed' not in text and 'Химмед' not in text and 'stock_snapshot' not in text


@pytest.mark.parametrize('mode', ['convert', 'attach'])
@pytest.mark.parametrize('bad_id', ['foreign', 'unknown', 'duplicate'])
def test_transfer_checks_line_membership_and_duplicates(site, mode, bad_id):
    source, _ = receive_with_stock(site)
    other = received(site)
    login(site)
    proposal = window(site, source)
    lines = proposal['proposal']['items']
    if bad_id == 'duplicate':
        lines.append(dict(lines[0]))
    else:
        lines[0]['site_item_id'] = window(site, other)['proposal']['items'][0]['site_item_id'] \
            if bad_id == 'foreign' else str(uuid4())
    response = transfer(site, source, proposal, mode, status=422)
    assert response['code'] == ('SITE_ITEM_DUPLICATE' if bad_id == 'duplicate' else 'SITE_ITEM_INVALID')


def test_missing_metadata_and_new_free_lines_stay_unknown(site):
    source = received(site)
    login(site)
    proposal = window(site, source)
    assert proposal['proposal']['items'][0]['stock_snapshot'] is None
    proposal['proposal']['items'][0].update(site_item_id=None, stock_snapshot=snapshot())
    result = transfer(site, source, proposal, 'convert')
    with site['sessions']() as db:
        item = db.scalar(select(RequestItem).where(RequestItem.request_id == result['created']['request_id']))
        assert item.stock_snapshot is None


def test_customer_document_projection_excludes_supplier_stock_evidence():
    line = {
        'line_id': 'line-1', 'item_id': 'item-1', 'product': {'name': 'Oxcarbazepine CRS', 'packaging': '100 mg'},
        'quantity': '2', 'unit': 'pcs', 'unit_price': '100', 'net': '200', 'tax': '0', 'total': '200',
        'tax_category': 'none', 'stock_snapshot': snapshot(), 'supplier_name': 'Химмед',
    }
    customer = customer_line(line, {})
    assert 'stock_snapshot' not in customer and 'supplier_name' not in customer
    assert 'Химмед' not in str(customer) and 'chimmed' not in str(customer)
