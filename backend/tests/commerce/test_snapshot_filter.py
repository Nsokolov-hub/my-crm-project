"""What a saved calculation snapshot shows to people with partial finance rights."""

from copy import deepcopy

import pytest

from app.commerce.financial import filter_calculation_snapshot

FINANCE = ("finance.purchase.read", "finance.calculations.read", "finance.reward.read", "finance.profit.read")


class Rights:
    def __init__(self, *granted):
        self.granted = set(granted)

    def allows(self, request_id, permission):
        return permission in self.granted


def snapshot():
    return {
        "algorithm_version": "itemized-v2", "calculation_number": "R-1-V1", "currency": "RUB",
        "lines": [{
            "line_id": "q1", "item_id": "i1", "quote_id": "s1", "quote_item_id": "q1", "description": "Реактив 1 кг",
            "product": {"name": "Реактив"}, "quantity": "2", "unit": "pcs", "net": "100", "tax": "22", "total": "122",
            "unit_price": "61", "supplier_id": "supplier-1", "supplier_name": "Поставщик", "purchase_currency": "USD",
            "quote_item": {"unit_price": "10"}, "customs_rule": {"value": "5"}, "expense_details": {"x": "1"},
            "detail": {"quantity": "2", "sale_net": "100", "purchase": "40", "profit": "30"},
            "future_line_field": "unknown",
        }],
        "totals": {"net": "100", "tax": "22", "total": "122", "purchase_rub": "40", "profit": "30"},
        "wave": {"id": "w1", "number": "W-1", "supplier_id": "supplier-1", "revision_digest": "r",
                 "financial_digest": "f", "existing_quantity": "0"},
        "input": {"selections": []}, "rates": [{"rate": "90"}], "resolved_expenses": [], "payment_terms": {},
        "internal_adjustment": "5", "future_field": {"cost_basis": "40"},
    }


def shown(*granted):
    return filter_calculation_snapshot(None, None, "request-1", snapshot(), Rights(*granted))


def test_without_purchase_rights_the_supplier_is_hidden_everywhere():
    result = shown("finance.calculations.read", "finance.reward.read", "finance.profit.read")
    line = result["lines"][0]
    assert not {"supplier_id", "supplier_name", "purchase_currency", "quote_item"} & set(line)
    assert "supplier_id" not in result["wave"]
    assert "purchase" not in line["detail"] and "purchase_rub" not in result["totals"]


@pytest.mark.parametrize("missing", FINANCE)
def test_fields_not_listed_for_partial_rights_stay_hidden(missing):
    result = shown(*(code for code in FINANCE if code != missing))
    assert "future_field" not in result
    assert "future_line_field" not in result["lines"][0]
    assert not {"input", "rates", "resolved_expenses", "payment_terms"} & set(result)


def test_full_rights_see_the_snapshot_as_saved():
    assert shown(*FINANCE) == snapshot()


def test_without_calculation_rights_only_the_sale_remains():
    result = shown("finance.purchase.read", "finance.reward.read", "finance.profit.read")
    line = result["lines"][0]
    assert "detail" not in line and "profile" not in result
    assert line["net"] == "100" and line["supplier_name"] == "Поставщик"
    assert deepcopy(result["totals"]) == snapshot()["totals"]
