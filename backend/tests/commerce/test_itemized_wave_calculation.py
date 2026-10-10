"""Regression checks for supplier wave cost sharing and shared column duty."""

from decimal import Decimal

from app.commerce.itemized import calculate_itemized
from app.commerce.itemized import itemized_profile as current_profile


def itemized_profile():
    """Historical profile: reproducibility remains supported after the new defaults."""
    profile = current_profile()
    profile['customs_fee_overrides'] = []
    profile['vat_deduction_mode'] = False
    profile['round_sale_up_to_ruble'] = True
    for rule in profile['customs_rules']:
        if rule['product_group_slug'] in ('other', 'lab_glassware'):
            rule.update(type='NONE', value='0')
        if rule['product_group_slug'] == 'columns':
            rule.update(type='FIXED_GROUP', value='73800')
    return profile

def _selection(identifier: str, group: str, quantity: str, price: str, bonus: str) -> dict:
    return {
        "quote_item": {
            "id": identifier, "quote_id": "quote-1", "supplier_id": "supplier-1",
            "source_request_item_id": identifier, "quantity": quantity,
            "unit_price": price, "delivery_days": 14,
        },
        "nomenclature": {"name": "Стандартный образец" if group == "reference_standards" else "Колонка"},
        "packing": {"display_name": "1 шт."},
        "product_group": {"slug": group},
        "currency_code": "USD", "markup_coefficient": "1.5",
        "bonus_coefficient": bonus, "weight": None,
    }


def test_wave_quantity_shares_fixed_expenses_and_fee_and_columns_share_duty():
    profile = itemized_profile()
    profile["import_country_id"] = "country-1"
    profile["customs_fee_brackets"] = [{"from_amount": "0", "to_amount": None, "fee": "4997"}]
    selections = [
        _selection("sample", "reference_standards", "2", "100", "1.15"),
        _selection("columns", "columns", "2", "100", "1.70"),
    ]
    result = calculate_itemized(
        profile, selections,
        [{"name": "Декларант", "amount": "25000", "currency": "RUB", "method": "BY_QUANTITY",
          "stage": "GENERAL", "calculation_type": "FIXED", "include_in_cost": True,
          "include_in_cash": True}],
        [{"currency": "USD", "management_per_unit": "100", "quoted_units": "1",
          "date": "2026-09-28", "source": "Профиль"}],
        wave_existing_quantity=Decimal("4"),
    )

    sample, columns = result["lines"]
    assert result["wave_distribution"]["total_quantity"] == "8"
    assert result["expense_allocations"][0]["existing_wave_share"] == "12500.00"
    assert sample["expense_details"]["Декларант"] == "6250.00"
    assert columns["expense_details"]["Декларант"] == "6250.00"
    assert Decimal(columns["detail"]["duty"]) == Decimal("73800.00")
    assert Decimal(sample["detail"]["duty"]) == Decimal("1000.00")
    assert Decimal(sample["detail"]["internal_bonus"]) == (
        Decimal(sample["detail"]["pre_bonus_sale_net"]) * Decimal("0.15")
    ).quantize(Decimal("0.01"))
    assert Decimal(columns["detail"]["internal_bonus"]) == (
        Decimal(columns["detail"]["pre_bonus_sale_net"]) * Decimal("0.70")
    ).quantize(Decimal("0.01"))
    assert Decimal(sample["detail"]["service_fee"]) == (
        Decimal(sample["detail"]["internal_bonus"]) * Decimal("0.16")
    ).quantize(Decimal("0.01"))
    assert Decimal(sample["unit_price"]) * Decimal(sample["quantity"]) == Decimal(sample["total"])
    assert Decimal(sample["detail"]["customs_fee"]) + Decimal(columns["detail"]["customs_fee"]) + \
        Decimal(result["wave_distribution"]["existing_customs_fee_share"]) == Decimal("4997.00")


def test_fixed_group_duty_is_split_between_column_rows():
    profile = itemized_profile()
    profile["import_country_id"] = "country-1"
    profile["customs_fee_brackets"] = [{"from_amount": "0", "to_amount": None, "fee": "4997"}]
    result = calculate_itemized(
        profile,
        [
            _selection("columns-a", "columns", "2", "100", "1"),
            _selection("columns-b", "columns", "1", "100", "1"),
        ],
        [],
        [{"currency": "USD", "management_per_unit": "100", "quoted_units": "1",
          "date": "2026-09-28", "source": "Профиль"}],
    )

    first, second = result["lines"]
    assert first["detail"]["duty"] == "49200.00"
    assert second["detail"]["duty"] == "24600.00"
    assert first["detail"]["fixed_group_quantity"] == "3"
    assert second["detail"]["fixed_group_quantity"] == "3"


def test_wave_logistics_and_transfer_fees_include_ruble_minimum():
    profile = itemized_profile()
    profile["import_country_id"] = "country-1"
    profile["customs_fee_brackets"] = [{"from_amount": "0", "to_amount": None, "fee": "0"}]
    selection = _selection("sample", "reference_standards", "2", "1000", "1")
    selection["currency_code"] = "INR"
    expenses = [
        {"name": name, "amount": amount, "currency": currency,
         "stage": stage, "scope": scope, "method": "BY_QUANTITY",
         "calculation_type": kind, **extra}
        for name, amount, currency, stage, scope, kind, extra in (
            ("Международная логистика", "80000", "INR", "INTERNATIONAL_LOGISTICS", "WAVE", "FIXED", {}),
            ("Декларант", "25000", "RUB", "GENERAL", "WAVE", "FIXED", {}),
            ("Терминальная обработка", "10000", "RUB", "GENERAL", "WAVE", "FIXED", {}),
            ("Доставка клиенту в Москве", "5000", "RUB", "GENERAL", "REQUEST", "FIXED", {}),
            ("Валютный контроль", "0.18", "RUB", "GENERAL", "WAVE", "PERCENTAGE",
             {"percent_base": "CUSTOMS_BASE", "minimum_amount": "30", "minimum_currency": "USD"}),
            ("Комиссия за платёж", "0.29", "RUB", "GENERAL", "WAVE", "PERCENTAGE",
             {"percent_base": "CUSTOMS_BASE"}),
        )
    ]
    result = calculate_itemized(profile, [selection], expenses, [
        {"currency": "INR", "management_per_unit": "1", "quoted_units": "1",
         "date": "2026-09-28", "source": "Профиль"},
        {"currency": "USD", "management_per_unit": "100", "quoted_units": "1",
         "date": "2026-09-28", "source": "Профиль"},
    ], wave_existing_quantity=Decimal("2"))
    row = result["lines"][0]
    assert row["expense_details"] == {
        "Международная логистика": "40000.00",
        "Декларант": "12500.00",
        "Терминальная обработка": "5000.00",
        "Доставка клиенту в Москве": "5000.00",
        "Валютный контроль": "1500.00",
        "Комиссия за платёж": "118.90",
    }
    shares = {entry["name"]: entry["existing_wave_share"] for entry in result["expense_allocations"]}
    assert shares["Валютный контроль"] == "1500.00"
    assert shares["Комиссия за платёж"] == "118.90"
    assert Decimal(row["total"]) > Decimal(row["detail"]["cost"])


def test_wave_percentage_uses_existing_purchase_and_current_logistics_budget():
    profile = itemized_profile()
    profile["import_country_id"] = "country-1"
    profile["customs_fee_brackets"] = [{"from_amount": "0", "to_amount": None, "fee": "0"}]
    selection = _selection("sample", "reference_standards", "2", "1000", "1")
    selection["currency_code"] = "INR"
    result = calculate_itemized(profile, [selection], [
        {"name": "Логистика", "amount": "80000", "currency": "INR", "method": "BY_QUANTITY",
         "stage": "INTERNATIONAL_LOGISTICS", "scope": "WAVE", "calculation_type": "FIXED"},
        {"name": "Перевод", "amount": "0.29", "currency": "RUB", "method": "BY_QUANTITY",
         "stage": "GENERAL", "scope": "WAVE", "calculation_type": "PERCENTAGE",
         "percent_base": "CUSTOMS_BASE"},
    ], [{"currency": "INR", "management_per_unit": "1", "quoted_units": "1",
         "date": "2026-09-28", "source": "Профиль"}],
        wave_existing_quantity=Decimal("2"),
        wave_existing_components={"purchase_rub": Decimal("100000")})
    allocation = next(row for row in result["expense_allocations"] if row["name"] == "Перевод")
    # (2 000 + 100 000 закупка + 80 000 логистика) * 0,29% = 527,80 руб.
    assert allocation["amount"] == "527.80"
    assert allocation["calculation_basis"] == "182000.00"
    assert allocation["parts"] == {"sample": "263.90"}
    assert allocation["existing_wave_share"] == "263.90"


def test_request_percentage_minimum_is_not_shared_with_existing_wave_orders():
    profile = itemized_profile()
    profile["import_country_id"] = "country-1"
    profile["customs_fee_brackets"] = [{"from_amount": "0", "to_amount": None, "fee": "0"}]
    result = calculate_itemized(profile, [
        _selection("sample", "reference_standards", "2", "100", "1"),
    ], [{"name": "Отдельный перевод", "amount": "0.18", "currency": "RUB",
         "method": "BY_QUANTITY", "scope": "REQUEST", "stage": "GENERAL",
         "calculation_type": "PERCENTAGE", "percent_base": "PURCHASE",
         "minimum_amount": "30", "minimum_currency": "USD"}], [
        {"currency": "USD", "management_per_unit": "100", "quoted_units": "1",
         "date": "2026-09-28", "source": "Профиль"},
    ], wave_existing_quantity=Decimal("2"),
        wave_existing_components={"purchase_rub": Decimal("100000")})
    allocation = result["expense_allocations"][0]
    assert allocation["amount"] == "3000.00"
    assert allocation["calculation_basis"] == "20000.00"
    assert allocation["parts"] == {"sample": "3000.00"}
    assert allocation["existing_wave_share"] == "0"


def test_purine_rows_apply_markup_before_non_deductible_vat():
    # Считалка 94: Вход-Выход!D14:H16, Колонки!AG6:AL7, AE6:AE7, AS6:AS7.
    profile = itemized_profile()
    profile["import_country_id"] = "country-1"
    profile["vat_deduction_mode"] = False
    profile["customs_fee_brackets"] = [{"from_amount": "0", "to_amount": None, "fee": "0"}]
    first_selection = _selection("sample", "reference_standards", "2", "2500", "1.15")
    second_selection = _selection("sample-small", "reference_standards", "2", "1000", "1.15")
    for selection, markup in ((first_selection, "1.8"), (second_selection, "1.7")):
        selection["currency_code"] = "CNY"
        selection["markup_coefficient"] = markup
    expenses = [
        {"name": name, "amount": amount, "currency": "RUB", "method": "BY_QUANTITY",
         "stage": stage, "calculation_type": "FIXED", "include_in_cost": True, "include_in_cash": True}
        for name, amount, stage in (
            ("Логистика МД", "56250", "INTERNATIONAL_LOGISTICS"),
            ("Логистика РФ", "6000", "GENERAL"),
            ("Терминал", "5000", "GENERAL"),
            ("Подача декларации", "5000", "GENERAL"),
            ("Декларант", "21000", "GENERAL"),
        )
    ]
    expenses.append({
        "name": "Комиссия за перевод", "amount": "380.86", "currency": "RUB",
        "method": "MANUAL", "manual": {"sample": "260.74", "sample-small": "120.12"}, "stage": "GENERAL",
        "calculation_type": "FIXED", "include_in_cost": True, "include_in_cash": True,
    })
    result = calculate_itemized(
        profile, [first_selection, second_selection], expenses,
        [{"currency": "CNY", "management_per_unit": "12.5", "quoted_units": "1",
          "date": "2026-09-28", "source": "Профиль"}],
        wave_existing_quantity=Decimal("4"),
    )
    first, second = result["lines"]
    # The October 5 correction applies markup to customs value plus duty;
    # the old workbook's non-deductible VAT is now recharged without markup.
    assert first["detail"]["markup_base"] == "80390.63"
    assert first["detail"]["sale_unit_gross"] == "123105"
    assert first["total"] == "246210.00"
    assert second["detail"]["sale_unit_gross"] == "63107"
    assert second["total"] == "126214.00"
    # The upward rounding is revenue; VAT is extracted from the new gross price.
    assert Decimal(first["detail"]["profit"]) == Decimal("64313.60")
