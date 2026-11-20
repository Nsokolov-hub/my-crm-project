"""The private site catalogue, access boundaries and complete, source-free customer exports."""

import hashlib
import hmac
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from openpyxl import load_workbook

from app.core.config import settings
from app.core.errors import DomainError
from app.core.models import PermissionGrant
from app.crm import stock_catalog
from app.crm.stock_catalog_exports import ClientCatalogRow, client_workbook, internal_workbook
from tests.support.crm import login

SECRET = 'catalogue-test-signing-key'
AT = datetime.now(UTC).replace(microsecond=0)
SUPPLIER = 'PRIVATE_SUPPLIER_7B89'
PRIVATE_URL = 'https://supplier-private.example.test/stock/7b89'
PRIVATE_PRICE = '987654321.12345678'


def item(identifier='one', *, packaging='5 g', **fields):
    return {'id': f'product:{identifier}', 'product_id': 'product', 'packaging_id': identifier,
            'name': '=HYPERLINK("https://unsafe.test","Вымышленный реактив")',
            'article': '000123', 'cas': '50-00-0', 'manufacturer': 'Вымышленный производитель',
            'purity': '99%', 'packaging': packaging, 'availability': 'in_stock', 'origin': 'supplier',
            'checked_at': AT.isoformat(), 'product_url': 'https://site.test/product/demo/',
            'purchase_price': PRIVATE_PRICE, 'private_note': SUPPLIER,
            'offers': [{'source': 'private', 'source_name': SUPPLIER, 'offer_id': f'offer-{identifier}',
                        'external_id': 'secret-external-id', 'catalog_number': 'supplier-article',
                        'pack': packaging, 'site_packaging_id': identifier, 'match': 'exact_pack',
                        'quantity': 3, 'seen_at': AT.isoformat(),
                        'valid_until': (AT + timedelta(hours=24)).isoformat(), 'url': PRIVATE_URL,
                        'price': PRIVATE_PRICE}], **fields}


class FakeSite:
    def __init__(self):
        self.bodies = []
        self.answer = None

    def handle(self, request):
        assert str(request.url) == 'https://site.test/api/v1/crm/stock'
        stamp = request.headers['X-CRM-Timestamp']
        signature = 'sha256=' + hmac.new(SECRET.encode(), stamp.encode() + b'.' + request.content,
                                         hashlib.sha256).hexdigest()
        assert request.headers['X-CRM-Signature'] == signature
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.answer is not None:
            return self.answer
        next_page = body.get('cursor') == 'next-token'
        return httpx.Response(200, json={
            'as_of': AT.isoformat(), 'items': [item('two' if next_page else 'one',
                                                  packaging='10 g' if next_page else '5 g')],
            'has_more': not next_page, 'next_cursor': None if next_page else 'next-token',
            'sources': [{'id': 'private', 'name': SUPPLIER, 'status': 'enabled', 'reason': '',
                         'last_check': {'checked_at': AT.isoformat(), 'count': 2}}]})


@pytest.fixture
def fake(monkeypatch):
    fake = FakeSite()
    monkeypatch.setattr(settings, 'site_events_url', 'https://site.test/api/v1/crm/events')
    monkeypatch.setattr(settings, 'site_events_secret', SECRET)
    monkeypatch.setattr(stock_catalog, 'http_client',
                        lambda: httpx.Client(transport=httpx.MockTransport(fake.handle)))
    return fake


def test_signed_catalogue_returns_public_projection_and_exact_pack_evidence(crm, fake):
    login(crm)
    client = crm['client']
    public = client.get('/api/v1/stock-catalog', params={'q': '  000123 ', 'brand': 'Brand'})
    assert public.status_code == 200, public.text
    assert SUPPLIER not in public.text and PRIVATE_URL not in public.text and PRIVATE_PRICE not in public.text
    assert public.json()['items'][0]['offers'] == []
    assert public.json()['sources'] == []
    assert fake.bodies[-1] == {'mode': 'catalog', 'q': '000123', 'source': '', 'brand': 'Brand',
                              'cursor': None, 'limit': 50}
    internal = client.get('/api/v1/stock-catalog', params={'audience': 'internal'})
    assert internal.status_code == 200, internal.text
    assert internal.json()['items'][0]['offers'][0]['quantity'] == 3
    assert internal.json()['items'][0]['offers'][0]['source_name'] == SUPPLIER
    assert PRIVATE_PRICE not in internal.text
    assert internal.json()['sources'][0]['last_check']['count'] == 2


def test_rights_protect_direct_list_review_and_both_exports(crm, fake):
    login(crm, email='manager@example.com')
    client = crm['client']
    assert client.get('/api/v1/stock-catalog').status_code == 403
    with crm['sessions'].begin() as db:
        db.add(PermissionGrant(user_id=crm['manager'].id, code='catalog.read', scope='all'))
        db.add(PermissionGrant(user_id=crm['manager'].id, code='clients.read', scope='all', allow=False))
    assert client.get('/api/v1/stock-catalog').status_code == 200
    calls = len(fake.bodies)
    for params in ({'audience': 'internal'}, {'mode': 'review'}, {'source': 'private'}):
        assert client.get('/api/v1/stock-catalog', params=params).status_code == 403
    assert client.get('/api/v1/stock-catalog/export.xlsx').status_code == 403
    assert client.get('/api/v1/stock-catalog/export.xlsx', params={'audience': 'internal'}).status_code == 403
    assert len(fake.bodies) == calls
    with crm['sessions'].begin() as db:
        db.add(PermissionGrant(user_id=crm['manager'].id, code='exports.download', scope='all'))
    exported = client.get('/api/v1/stock-catalog/export.xlsx')
    assert exported.status_code == 200, exported.text
    assert client.get('/api/v1/stock-catalog/export.xlsx', params={'audience': 'internal'}).status_code == 403


def test_client_export_fetches_all_pages_without_private_data_anywhere(crm, fake):
    login(crm)
    response = crm['client'].get('/api/v1/stock-catalog/export.xlsx',
                                params={'q': '000123', 'brand': 'Demo', 'source': 'private',
                                        'as_of': AT.isoformat()})
    assert response.status_code == 200, response.text
    assert len(fake.bodies) == 2
    assert fake.bodies[0]['cursor'] is None and fake.bodies[1]['cursor'] == 'next-token'
    assert all(body['as_of'] == AT.isoformat() for body in fake.bodies)
    assert all(body['q'] == '000123' and body['brand'] == 'Demo' and body['source'] == 'private'
               for body in fake.bodies)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        for name in archive.namelist():
            content = archive.read(name)
            for secret in (SUPPLIER, PRIVATE_URL, PRIVATE_PRICE, 'secret-external-id', 'supplier-article'):
                assert secret.encode() not in content, (name, secret)
        xml = '\n'.join(archive.read(name).decode() for name in archive.namelist()
                        if name.endswith(('.xml', '.rels')))
        assert 'TargetMode="External"' not in xml
        assert '<f>' not in xml and 'state="hidden"' not in xml and 'hidden="1"' not in xml
        assert not any('comments' in name or 'externalLink' in name or 'connections' in name
                       for name in archive.namelist())
    book = load_workbook(io.BytesIO(response.content))
    assert book.sheetnames == ['Каталог наличия']
    sheet = book.active
    assert sheet['B8'].value.startswith('=HYPERLINK') and sheet['B8'].data_type == 's'
    assert sheet['C8'].value == '000123' and sheet['C8'].data_type == 's'
    assert sheet['G8'].value == '5 g' and sheet['G9'].value == '10 g'
    assert sheet['H8'].value == sheet['H9'].value == 'В наличии'
    assert isinstance(sheet['I8'].value, datetime)
    assert sheet.max_row == 9 and sheet.max_column == 9
    assert sheet.freeze_panes == 'D8' and len(sheet.tables) == 1
    assert all(s.sheet_state == 'visible' for s in book)


def test_internal_export_keeps_source_trace_and_review_never_becomes_customer_file(crm, fake):
    login(crm)
    client = crm['client']
    result = client.get('/api/v1/stock-catalog/export.xlsx', params={'audience': 'internal'})
    assert result.status_code == 200, result.text
    sheet = load_workbook(io.BytesIO(result.content)).active
    assert sheet['I8'].value == SUPPLIER and sheet['L8'].value == 3
    assert sheet['P8'].hyperlink.target == PRIVATE_URL
    assert PRIVATE_PRICE not in json.dumps([[str(cell.value) for cell in row] for row in sheet])
    assert client.get('/api/v1/stock-catalog/export.xlsx', params={'mode': 'review'}).json()[
        'code'] == 'STOCK_REVIEW_INTERNAL'
    assert client.get('/api/v1/stock-catalog', params={'mode': 'review'}).json()[
        'code'] == 'STOCK_REVIEW_INTERNAL'


def test_multiple_suppliers_share_customer_row_and_do_not_sum_quantities(crm, fake, monkeypatch):
    login(crm)
    row = item()
    row['offers'].append({**row['offers'][0], 'offer_id': 'second-offer', 'quantity': 4,
                          'source_name': 'PRIVATE_SECOND_SUPPLIER'})
    fake.answer = httpx.Response(200, json={'as_of': AT.isoformat(), 'items': [row], 'has_more': False})
    client = crm['client']
    customer = client.get('/api/v1/stock-catalog/export.xlsx')
    assert load_workbook(io.BytesIO(customer.content)).active.max_row == 8
    internal = client.get('/api/v1/stock-catalog/export.xlsx', params={'audience': 'internal'})
    sheet = load_workbook(io.BytesIO(internal.content)).active
    assert sheet.max_row == 9 and [sheet['L8'].value, sheet['L9'].value] == [3, 4]
    monkeypatch.setattr(stock_catalog, 'MAX_EXPORT_ROWS', 1)
    overflow = client.get('/api/v1/stock-catalog/export.xlsx', params={'audience': 'internal'})
    assert overflow.status_code == 422 and overflow.json()['code'] == 'STOCK_EXPORT_TOO_LARGE'


def test_pagination_errors_and_limit_are_explicit(monkeypatch):
    first = stock_catalog.SiteStockPage(as_of=AT, items=[item()], has_more=True, next_cursor='repeat')
    monkeypatch.setattr(stock_catalog, 'ask', lambda **fields: first)
    with pytest.raises(DomainError, match='повторил'):
        stock_catalog.all_rows({'mode': 'catalog'})
    monkeypatch.setattr(stock_catalog, 'MAX_EXPORT_ROWS', 1)
    with pytest.raises(DomainError) as error:
        stock_catalog.all_rows({'mode': 'catalog'})
    assert error.value.code == 'STOCK_EXPORT_TOO_LARGE'
    monkeypatch.setattr(stock_catalog, 'MAX_EXPORT_ROWS', 20_000)
    pages = iter([first, first.model_copy(update={'as_of': AT + timedelta(seconds=1), 'items': []})])
    monkeypatch.setattr(stock_catalog, 'ask', lambda **fields: next(pages))
    with pytest.raises(DomainError) as error:
        stock_catalog.all_rows({'mode': 'catalog'})
    assert error.value.code == 'STOCK_VIEW_CHANGED'


def test_empty_catalogue_and_stale_review_are_clear():
    empty = load_workbook(io.BytesIO(client_workbook([], AT))).active
    assert empty['B8'].value == 'По выбранным фильтрам позиций нет'
    review = stock_catalog.StockItem.model_validate(item(availability='unknown', fresh=False,
                                                         reason='Проверьте фасовку'))
    sheet = load_workbook(io.BytesIO(internal_workbook([review.model_dump()], AT, mode='review'))).active
    assert sheet['H8'].value == 'Требует проверки'
    assert sheet['O8'].value.startswith('Устарело. Требует сверки.')
    direct = ClientCatalogRow(name='Товар', article='+00001', cas='-123', packaging='@test')
    sheet = load_workbook(io.BytesIO(client_workbook([direct], AT))).active
    assert all(sheet[key].data_type == 's' for key in ('C8', 'D8', 'G8'))


def test_disabled_and_refused_site_are_explained(crm, fake, monkeypatch):
    login(crm)
    client = crm['client']
    fake.answer = httpx.Response(401, json={'code': 'CRM_SIGNATURE_INVALID'})
    assert client.get('/api/v1/stock-catalog').json()['code'] == 'SITE_LINK_REFUSED'
    fake.answer = httpx.Response(422, json={'code': 'STOCK_VIEW_EXPIRED', 'message': 'Обновите каталог'})
    assert client.get('/api/v1/stock-catalog').json()['code'] == 'STOCK_VIEW_EXPIRED'
    fake.answer = httpx.Response(200, json={'items': []})
    assert client.get('/api/v1/stock-catalog').json()['code'] == 'SITE_STOCK_INVALID'
    monkeypatch.setattr(settings, 'site_events_secret', '')
    assert client.get('/api/v1/stock-catalog').json()['code'] == 'SITE_LINK_DISABLED'
