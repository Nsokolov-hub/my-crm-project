"""Acceptance examples from the final shipment A specification and workbook."""

from decimal import Decimal

import pytest
from test_itemized_wave_calculation import _selection

from app.commerce.calculator import profitability_metrics
from app.commerce.itemized import calculate_itemized, itemized_profile


def shipment_a(*, deduct_vat=True, round_up=False, all_selected=False):
    profile = itemized_profile()
    profile.update(import_country_id="IN", vat_deduction_mode=deduct_vat, round_sale_up_to_ruble=round_up)
    first = _selection("product-1", "reference_standards", "1", "11000", "1")
    # The other eleven units provide the full recorded wave basis; their unit
    # price is a test assumption, not an inferred price of the user's product.
    peers = _selection("other-products", "reference_standards", "11", "17000", "1")
    for row in (first, peers):
        row["currency_code"] = "INR"
    peers["markup_coefficient"] = "2"
    expenses = [
        {"name": name, "amount": amount, "currency": currency, "method": "BY_QUANTITY",
         "scope": "WAVE", "stage": stage, "include_in_cost": True, "include_in_cash": True}
        for name, amount, currency, stage in (
            ("Международная логистика", "60000", "INR", "INTERNATIONAL_LOGISTICS"),
            ("Брокер", "22000", "RUB", "GENERAL"),
            ("Логистика РФ", "14962.50", "RUB", "GENERAL"),
            ("Терминальная обработка", "2000", "RUB", "GENERAL"),
            # Стандарты!T2 and U2: their numerators are paid amounts;
            # distribute them over the current twelve units, not the old 34.
            ("Валютный контроль", "963.80", "RUB", "GENERAL"),
            ("Комиссия за перевод", "3160", "RUB", "GENERAL"),
        )
    ]
    return calculate_itemized(
        profile, [first, peers] if all_selected else [first], expenses,
        [{"currency": "INR", "management_per_unit": "0.94", "quoted_units": "1",
          "date": "2026-10-05", "source": "Поставка А"}],
        payment_terms={"prepayment_percent": "100", "deferred_percent": "0", "deferred_days": 0},
        wave_existing_selections=[] if all_selected else [peers],
    )


def test_shipment_a_import_vat_is_excluded_from_markup_and_recharged_expenses():
    result = shipment_a()
    detail = result["lines"][0]["detail"]
    expected = {
        "purchase_rub": "10340.00", "international_logistics": "4700.00",
        "customs_base": "15040.00", "duty": "752.00", "import_vat_base": "15792.00",
        "import_vat": "3474.24", "import_cost_with_vat": "19266.24", "clean_cost": "15792.00",
        "general_expenses": "3590.52", "customs_fee": "205.17", "cost": "19587.69",
        "pre_bonus_sale_net": "27483.69", "sale_net": "27483.69",
        "sale_tax": "6046.41", "sale_total": "33530.10", "profit": "7896.00",
        "markup_amount": "7896.000", "financed_amount": "0.00", "financing_cost": "0.00",
        "vat_payable": "2572.17", "cash_need": "23061.93",
    }
    for key, amount in expected.items():
        assert Decimal(detail[key]) == Decimal(amount), key
    assert Decimal(detail["profitability_percent"]).quantize(Decimal(".01")) == Decimal("28.73")
    assert Decimal(detail["cost_profitability_percent"]).quantize(Decimal(".01")) == Decimal("40.31")
    assert result["lines"][0]["expense_details"]["Валютный контроль"] == "80.32"
    assert result["lines"][0]["expense_details"]["Комиссия за перевод"] == "263.33"
    assert result["wave_distribution"]["total_quantity"] == "12"
    assert result["wave_distribution"]["customs_fee_1"] == "2462.00"
    for expense in result["expense_allocations"]:
        assert sum(Decimal(value) for value in expense["wave_parts"].values()) == Decimal(expense["amount"])


def test_shipment_a_totals_use_revenue_weighting_and_only_selected_lines():
    single, full = shipment_a(), shipment_a(all_selected=True)
    assert Decimal(single["totals"]["profit"]) == Decimal("7896")
    for key in ("sale_net", "cost", "profit", "import_vat", "import_vat_base", "import_cost_with_vat"):
        assert Decimal(full["totals"][key]) == sum(Decimal(line["detail"][key]) for line in full["lines"])
    expected = Decimal(full["totals"]["profit"]) / Decimal(full["totals"]["sale_net"]) * 100
    assert abs(Decimal(full["totals"]["profitability_percent"]) - expected) < Decimal("1e-25")
    average = sum(Decimal(line["detail"]["profitability_percent"]) for line in full["lines"]) / 2
    assert abs(Decimal(full["totals"]["profitability_percent"]) - average) > 1


def test_explicit_legacy_rounding_and_disabled_vat_deduction_are_preserved():
    rounded = shipment_a(round_up=True)["lines"][0]["detail"]
    assert Decimal(rounded["sale_total"]) == Decimal("33531")
    assert Decimal(rounded["sale_net"]) == Decimal("27483.69")
    without_deduction = shipment_a(deduct_vat=False)["lines"][0]["detail"]
    assert Decimal(without_deduction["clean_cost"]) == Decimal("19266.24")
    assert Decimal(without_deduction["vat_payable"]) == Decimal(without_deduction["sale_tax"])
    assert Decimal(without_deduction["markup_base"]) == Decimal("15792")
    assert Decimal(without_deduction["cost_after_markup"]) == Decimal("23688")
    assert Decimal(without_deduction["profit"]) == Decimal("7896")


@pytest.mark.parametrize("profit,sales,cost,expected", [
    ("0", "0", "0", "0"), ("-10", "100", "110", "-10"), ("50", "100", "50", "50"),
])
def test_profitability_zero_and_loss_cases(profit, sales, cost, expected):
    result = profitability_metrics(Decimal(profit), Decimal(sales), Decimal(cost))
    assert result["profitability_percent"] == Decimal(expected)
    assert result["margin_percent"] == result["profitability_percent"]


def test_new_profile_defaults_to_deduction_and_exact_kopeck_invoice():
    profile = itemized_profile()
    assert profile["vat_deduction_mode"] is True
    assert profile["round_sale_up_to_ruble"] is False
