from decimal import Decimal

import pytest

from app.commerce.financial import common_calculation_quantity
from app.commerce.itemized import calculate_itemized, itemized_profile
from tests.support.business import cmd, structured
from tests.support.itemized import quote_selection


@pytest.mark.parametrize('target', ['1', '10', '13', '20', '100'])
def test_single_quantity_distributes_expenses_across_mixed_product_groups(target):
    rows = [quote_selection('standard', 'reference_standards', '12', '100', '1'),
            quote_selection('reagent', 'reagents', '1', '100', '1')]
    for row in rows:
        row.update(currency_code='RUB', weight=str(Decimal(row['quote_item']['quantity']) * 2))
    peers = []
    share, record = common_calculation_quantity(rows, peers, Decimal('0'), Decimal(target), 'Общая поставка')
    profile = itemized_profile()
    profile['import_country_id'] = 'RU'
    result = calculate_itemized(profile, rows, [
        {'name': 'Общая логистика', 'amount': '1000', 'scope': 'WAVE', 'method': 'BY_QUANTITY'},
        {'name': 'Доставка клиенту', 'amount': '260', 'scope': 'REQUEST', 'method': 'BY_QUANTITY'},
    ], [], wave_existing_selections=peers, wave_existing_share=share)
    assert Decimal(result['wave_distribution']['total_quantity']) == Decimal(target)
    assert [Decimal(line['quantity']) for line in result['lines']] == [12, 1]
    for line in result['lines']:
        quantity = Decimal(line['quantity'])
        expected = (Decimal(1000) * quantity / Decimal(target)).quantize(Decimal('.01'))
        assert Decimal(line['expense_details']['Общая логистика']) == expected
        assert Decimal(line['expense_details']['Доставка клиенту']) == 20 * quantity
    assert record['quantity'] == target


def test_single_quantity_includes_aggregate_existing_orders():
    rows = [quote_selection('standard', 'reference_standards', '3', '100', '1'),
            quote_selection('second-standard', 'reference_standards', '1', '100', '1')]
    for row in rows:
        row['currency_code'] = 'RUB'
    share, _ = common_calculation_quantity(rows, [], Decimal('45'), Decimal('9'), 'Повторный заказ')
    profile = itemized_profile()
    profile['import_country_id'] = 'RU'
    profile['customs_fee_overrides'] = []
    result = calculate_itemized(profile, rows,
        [{'name': 'Общая логистика', 'amount': '900', 'scope': 'WAVE', 'method': 'BY_QUANTITY'}], [],
        wave_existing_quantity=Decimal('45'), wave_existing_share=share)
    assert Decimal(result['wave_distribution']['total_quantity']) == 9
    assert Decimal(result['wave_distribution']['existing_quantity']) == 45


def test_single_quantity_is_saved_and_preview_matches_calculation(crm):
    req, data, _, _ = structured(crm, mode='IMPORT')
    data.update(calculation_quantity='10', planned_volume_comment='Поставка на десять штук')
    url = f"/requests/{req['id']}/calculations"
    preview = cmd(crm, url + '/preview', data)['snapshot']
    saved = cmd(crm, url, {**data, 'expected_wave_digest': preview['wave_distribution']['digest']})
    assert saved['snapshot']['wave']['calculation_quantity']['quantity'] == '10'
    assert saved['snapshot']['input']['calculation_quantity'] == '10'
    assert saved['snapshot']['totals'] == preview['totals']
