"""Regression checks for supplier wave cost sharing and shared column duty."""

from decimal import Decimal

from app.commerce.itemized import calculate_itemized, itemized_profile


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
        Decimal(sample["detail"]["cost_before_adjustment"]) * Decimal("0.15")
    ).quantize(Decimal("0.01"))
    assert Decimal(columns["detail"]["internal_bonus"]) == (
        Decimal(columns["detail"]["cost_before_adjustment"]) * Decimal("0.70")
    ).quantize(Decimal("0.01"))
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
