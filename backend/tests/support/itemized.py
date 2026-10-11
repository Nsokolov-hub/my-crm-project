"""Quote selections for direct calls of the itemized calculation engine."""


def quote_selection(identifier: str, group: str, quantity: str, price: str, bonus: str) -> dict:
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
