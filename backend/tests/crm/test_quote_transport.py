"""Required explicit transport information in quote imports, including completion of legacy files."""

import io

import pytest
from openpyxl import load_workbook
from sqlalchemy import func, select

from app.core.models import PermissionGrant
from app.crm.imports import xlsx
from app.crm.models import Nomenclature, QuoteItem
from app.crm.table_imports import QUOTE_COLUMNS
from app.crm.transport import storage_temperature, transport_hazard
from tests.support.business import confirm, preview, quote_file, request
from tests.support.crm import login, post


@pytest.mark.parametrize('raw, expected', [('2-8', '2-8'), ('−20 °C', '-20'), ('-70', '-70'),
                                           ('1 – 25', '1-25'), ('-20--10', '-20--10'), ('0', '0')])
def test_temperature_is_text_and_normalizes_a_value_or_ordered_range(raw, expected):
    assert storage_temperature(raw) == expected


@pytest.mark.parametrize('raw', ['8-2', '2026-02-08', 'комнатная', '2/8', 'NaN', '0.' + '0' * 80 + '1'])
def test_temperature_never_guesses_unknown_or_excel_dates(raw):
    with pytest.raises(ValueError):
        storage_temperature(raw)


@pytest.mark.parametrize('raw, expected', [('UN', 'UN'), ('un 1234', 'UN1234'),
                                           ('неопасный', 'Не опасный'), ('non-hazardous', 'Не опасный')])
def test_hazard_requires_an_explicit_declaration(raw, expected):
    assert transport_hazard(raw) == expected


@pytest.mark.parametrize('raw', ['0', 'нет данных', 'опасный', 'UN123', 'safe'])
def test_hazard_does_not_turn_unknown_into_non_hazardous(raw):
    with pytest.raises(ValueError):
        transport_hazard(raw)


def context(crm):
    login(crm)
    req = request(crm)
    post(crm, f"/requests/{req['id']}/items", {'description': 'Товар для проверки транспорта',
         'article': '0341', 'quantity': '1', 'unit': 'pcs'})
    supplier = post(crm, '/counterparties', {'name': 'Поставщик параметров', 'kind': 'supplier'})
    return req, supplier


def old_file(supplier):
    keys = [key for key in QUOTE_COLUMNS if key not in ('storage_temperature', 'transport_hazard')]
    full = dict(zip(QUOTE_COLUMNS, quote_file(supplier['internal_code']), strict=True))
    return [QUOTE_COLUMNS[key] for key in keys], [[full[key] for key in keys]]


def test_legacy_file_can_be_completed_in_preview_before_any_quote_is_written(crm):
    req, supplier = context(crm)
    columns, rows = old_file(supplier)
    batch = preview(crm, req['id'], columns, rows, 'quotes')
    assert batch['summary']['errors'] == 1
    assert 'температурный режим' in batch['rows'][0]['errors'][0]
    confirm(crm, req['id'], batch['id'], status=422)
    with crm['sessions']() as db:
        assert db.scalar(select(func.count()).select_from(QuoteItem)) == 0
        assert db.scalar(select(func.count()).select_from(Nomenclature)) == 0
    repaired = post(crm, f"/requests/{req['id']}/table-imports/{batch['id']}/transport", {
        'version': batch['version'], 'row_number': 2, 'storage_temperature': '2 – 8 °C',
        'transport_hazard': 'UN'}, status=200)
    assert repaired['summary']['errors'] == 0
    assert repaired['rows'][0]['data']['storage_temperature'] == '2-8'
    with crm['sessions']() as db:
        assert db.scalar(select(func.count()).select_from(Nomenclature)) == 0
    result = confirm(crm, req['id'], batch['id'])
    with crm['sessions']() as db:
        n = db.scalar(select(Nomenclature))
        assert (n.storage_temperature, n.transport_hazard) == ('2-8', 'UN')
        n_id = n.id
        n.storage_temperature = n.transport_hazard = None
        db.commit()
    # Completed imports and quote history are not retrospectively invalidated.
    assert confirm(crm, req['id'], batch['id'])['result'] == result['result']
    assert crm['client'].get(f'/api/v1/nomenclatures/{n_id}').status_code == 200


def test_template_roundtrip_keeps_temperatures_as_text(crm):
    req, supplier = context(crm)
    batch = preview(crm, req['id'], list(QUOTE_COLUMNS.values()), [quote_file(supplier['internal_code'],
        {'storage_temperature': '2-8', 'transport_hazard': 'UN1234'})], 'quotes')
    confirm(crm, req['id'], batch['id'])
    response = crm['client'].get(f"/api/v1/requests/{req['id']}/quote-import/template.xlsx")
    assert response.status_code == 200
    book = load_workbook(io.BytesIO(response.content))
    sheet = book['Квоты']
    for key, expected in [('storage_temperature', '2-8'), ('transport_hazard', 'UN1234')]:
        index = list(QUOTE_COLUMNS).index(key) + 1
        assert sheet.cell(2, index).value == expected
        assert sheet.cell(2, index).data_type == 's'
        assert sheet.cell(2, index).number_format == '@'
        assert sheet.column_dimensions[sheet.cell(1, index).column_letter].number_format == '@'
    assert 'Заполнение' in book.sheetnames
    group_col = sheet.cell(1, list(QUOTE_COLUMNS).index('product_group') + 1).column_letter
    assert any(v.formula1 == 'QuoteProductGroups' and f'{group_col}2' in str(v.sqref)
               for v in sheet.data_validations.dataValidation)
    book.close()


def test_only_the_canonical_columns_group_is_exempt(crm):
    req, supplier = context(crm)
    group = post(crm, '/product-groups', {'name': 'Хроматографические колонки', 'slug': 'columns'})
    blank = {'storage_temperature': '', 'transport_hazard': '', 'product_group': str(group['internal_code'])}
    batch = preview(crm, req['id'], list(QUOTE_COLUMNS.values()),
                    [quote_file(supplier['internal_code'], blank)], 'quotes')
    assert batch['summary']['errors'] == 0
    confirm(crm, req['id'], batch['id'])
    with crm['sessions']() as db:
        n = db.scalar(select(Nomenclature))
        assert n.storage_temperature is None and n.transport_hazard is None
    not_column = preview(crm, req['id'], list(QUOTE_COLUMNS.values()), [quote_file(supplier['internal_code'],
        {**blank, 'article': 'NEW-COLUMN-NAME', 'name': 'Колонка по названию', 'product_group': 'other'})], 'quotes')
    assert not_column['summary']['errors'] == 1
    assert 'температурный режим' in not_column['rows'][0]['errors'][0]


def grant_quote_access(crm):
    with crm['sessions'].begin() as db:
        db.add_all(PermissionGrant(user_id=crm['manager'].id, code=code, scope='all') for code in
                   ('quotes.write', 'finance.purchase.read', 'catalog.read'))
    login(crm, 'manager@example.com')


def test_same_catalog_values_need_no_edit_permission_but_unknown_values_do(crm):
    req, supplier = context(crm)
    known = post(crm, '/nomenclatures', {'name': 'Known', 'article': '0341', 'storage_temperature': '2-8',
        'transport_hazard': 'Не опасный', 'packings': [{'value': '1', 'unit': 'pcs'}]})
    grant_quote_access(crm)
    columns, rows = old_file(supplier)
    same = preview(crm, req['id'], columns, rows, 'quotes')
    assert same['summary']['errors'] == 0
    confirm(crm, req['id'], same['id'])
    changed = preview(crm, req['id'], list(QUOTE_COLUMNS.values()), [quote_file(supplier['internal_code'],
        {'storage_temperature': '-20', 'transport_hazard': 'UN'})], 'quotes')
    assert changed['summary']['errors'] == 1
    assert 'правом «Изменение каталога»' in changed['rows'][0]['errors'][0]
    confirm(crm, req['id'], changed['id'], status=422)
    with crm['sessions']() as db:
        assert db.get(Nomenclature, known['id']).storage_temperature == '2-8'


def test_preview_edit_is_owned_versioned_and_preserves_row_numbers(crm):
    req, supplier = context(crm)
    columns, rows = old_file(supplier)
    batch = preview(crm, req['id'], columns, rows, 'quotes')
    path = f"/requests/{req['id']}/table-imports/{batch['id']}/transport"
    body = {'version': batch['version'], 'row_number': 2, 'storage_temperature': '-20', 'transport_hazard': 'UN'}
    saved = post(crm, path, body, status=200)
    assert saved['rows'][0]['row_number'] == 2
    post(crm, path, body, status=409)
    grant_quote_access(crm)
    post(crm, path, {**body, 'version': saved['version']}, status=404)


def test_transport_edit_after_removal_keeps_the_original_source_row(crm):
    req, supplier = context(crm)
    post(crm, f"/requests/{req['id']}/items", {'description': 'Вторая позиция',
         'article': '0342', 'quantity': '1', 'unit': 'pcs'})
    columns, rows = old_file(supplier)
    second = dict(zip(columns, rows[0], strict=True))
    second.update({'Позиция заявки': '2', 'Артикул': '0342'})
    batch = preview(crm, req['id'], columns, [rows[0], list(second.values())], 'quotes')
    batch = post(crm, f"/requests/{req['id']}/table-imports/{batch['id']}/remove-rows",
                 {'row_numbers': [2]}, status=200)
    assert [row['row_number'] for row in batch['rows']] == [3]
    batch = post(crm, f"/requests/{req['id']}/table-imports/{batch['id']}/transport", {
        'version': batch['version'], 'row_number': 3, 'storage_temperature': '0',
        'transport_hazard': 'Не опасный'}, status=200)
    assert batch['summary']['errors'] == 0
    assert batch['rows'][0]['data']['article'] == '0342'
    confirm(crm, req['id'], batch['id'])
    with crm['sessions']() as db:
        n = db.scalar(select(Nomenclature))
        assert (n.article, n.storage_temperature, n.transport_hazard) == ('0342', '0', 'Не опасный')


@pytest.mark.parametrize('legacy', [False, True])
def test_duplicate_file_can_complete_catalog_metadata_without_duplicate_quotes(crm, legacy):
    req, supplier = context(crm)
    if legacy:
        post(crm, '/nomenclatures', {'name': 'Known', 'article': '0341', 'storage_temperature': '1-25',
            'transport_hazard': 'Не опасный', 'packings': [{'value': '1', 'unit': 'pcs'}]})
        columns, rows = old_file(supplier)
    else:
        columns, rows = list(QUOTE_COLUMNS.values()), [quote_file(supplier['internal_code'])]
    # Reuse identical bytes: XLSX is a ZIP whose timestamps can differ between separate serializations.
    content = xlsx([columns, *rows])

    def upload():
        response = crm['client'].post(f"/api/v1/requests/{req['id']}/table-imports/preview",
            files={'file': ('quote.xlsx', content, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
            data={'kind': 'quotes'})
        assert response.status_code == 200, response.text
        return response.json()

    first = upload()
    confirm(crm, req['id'], first['id'])
    second = upload()
    second = post(crm, f"/requests/{req['id']}/table-imports/{second['id']}/transport", {
        'version': second['version'], 'row_number': 2, 'storage_temperature': '-70', 'transport_hazard': 'UN1234'}, status=200)
    result = confirm(crm, req['id'], second['id'])
    assert result['result']['already_imported']
    with crm['sessions']() as db:
        assert db.scalar(select(func.count()).select_from(QuoteItem)) == 1
        n = db.scalar(select(Nomenclature))
        assert (n.storage_temperature, n.transport_hazard) == ('-70', 'UN1234')
