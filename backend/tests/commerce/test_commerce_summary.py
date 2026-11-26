"""List-only commercial columns never recalculate the saved calculation."""

from app.commerce.commerce_summary import calculation_commerce_summary


def test_calculation_summary_aggregates_lines_without_averaging_percentages():
    snapshot = {
        "currency": "RUB",
        "management_currency": "RUB",
        "totals": {},
        "lines": [
            {
                "supplier_name": "Aozeal",
                "delivery_days": 7,
                "net": "100",
                "detail": {"purchase_rub": "40", "profit": "25"},
            },
            {
                "supplier_name": "Aozeal",
                "delivery_days": 14,
                "net": "300",
                "detail": {"purchase_rub": "60", "profit": "75"},
            },
            {
                "supplier_name": "Chimmed",
                "delivery_days": None,
                "net": "100",
                "detail": {"purchase_rub": "50", "cost": "90"},
            },
        ],
    }

    result = calculation_commerce_summary(snapshot)

    assert result == {
        "supplier_names": ["Aozeal", "Chimmed"],
        "delivery_days": 14,
        "profitability_percent": "22.00",
        "purchase_amount": "150.00",
        "purchase_currency": "RUB",
        "sale_amount": None,
        "sale_currency": None,
    }


def test_calculation_summary_uses_filtered_totals_and_keeps_denied_values_empty():
    snapshot = {
        "currency": "RUB",
        "management_currency": "RUB",
        "delivery_days": 21,
        "totals": {"sale_total": "1234.50", "sale_net": "1028.75"},
        "lines": [{"net": "1028.75", "detail": {}}],
    }

    result = calculation_commerce_summary(snapshot)

    assert result["supplier_names"] == []
    assert result["delivery_days"] == 21
    assert result["purchase_amount"] is None
    assert result["purchase_currency"] is None
    assert result["profitability_percent"] is None
    assert result["sale_amount"] == "1234.50"
    assert result["sale_currency"] == "RUB"


def test_calculation_summary_reads_totals_and_legacy_purchase_key():
    snapshot = {
        "currency": "USD",
        "management_currency": "RUB",
        "totals": {
            "purchase_rub": "9000",
            "sale_total": "120",
            "sale_net": "100",
            "profit": "10",
        },
        "lines": [{"quote": {"supplier_name": "Legacy supplier"}, "net": "100", "detail": {}}],
    }

    result = calculation_commerce_summary(snapshot)

    assert result["supplier_names"] == ["Legacy supplier"]
    assert result["purchase_amount"] == "9000.00"
    assert result["sale_amount"] == "120"
    assert result["profitability_percent"] == "10.00"
