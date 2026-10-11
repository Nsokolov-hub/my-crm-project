"""Forgiving counterparty search: case, «ё», spaces and punctuation do not matter.

"+7 (495) 900-77-73" is found by "495900" and "АО «МИКРОГЕН»" by "микро ген". The normalized text is
stored with each counterparty, so search runs in SQL instead of reading the whole table into Python.
"""

from collections.abc import Mapping
from typing import Any

# Column filters of the counterparty list, besides the general search box.
FILTER_FIELDS = ('name', 'tax_id', 'profile', 'city', 'phone', 'email')


def normalize(value: Any) -> str:
    return ''.join(char for char in str(value or '').casefold().replace('ё', 'е') if char.isalnum())


def search_parts(value: str) -> list[str]:
    """Words of a query; every one of them has to occur in the normalized text."""
    return [part for part in (normalize(word) for word in value.split()) if part]


def search_values(*, name: Any, tax_id: Any, phone: Any, email: Any, internal_code: Any,
                  details: Any) -> tuple[str, dict[str, str]]:
    """Normalized text for the search box and per-column values for the filters."""
    details = details if isinstance(details, Mapping) else {}
    values = {'name': name, 'tax_id': tax_id, 'phone': phone, 'email': email, 'internal_code': internal_code,
              'profile': details.get('profile'), 'city': details.get('city')}
    text = normalize(' '.join(str(value or '') for value in values.values()))
    return text, {field: normalize(values[field]) for field in FILTER_FIELDS}
