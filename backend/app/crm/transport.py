"""Explicit transport information supplied by a manager, never inferred from a chemical name."""

import re
from decimal import Decimal


def storage_temperature(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip().replace('−', '-').replace(',', '.')
    text = re.sub(r'\s*(?:°\s*[cс]|[cс])\s*$', '', text, flags=re.I)
    if len(text) > 80:
        raise ValueError('Слишком длинное значение температурного режима')
    number = r'[+-]?\d+(?:\.\d+)?'
    match = re.fullmatch(rf'\s*({number})\s*(?:(?:-|–|—|…|\.\.|\bto\b|\bдо\b)\s*({number}))?\s*', text, re.I)
    if not match:
        raise ValueError('Температурный режим: укажите температуру или диапазон, например 2-8, -20, -70, 1-25')
    values = [Decimal(part) for part in match.groups() if part is not None]
    if len(values) == 2 and values[0] > values[1]:
        raise ValueError('В температурном диапазоне нижняя граница не должна превышать верхнюю')
    if any(len(part.as_tuple().digits) > 10 for part in values):
        raise ValueError('Слишком длинное значение температурного режима')
    result = '-'.join(format(part.normalize(), 'f') if part else '0' for part in values)
    if len(result) > 80:
        raise ValueError('Слишком длинное значение температурного режима')
    return result


def transport_hazard(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    text = ' '.join(str(value).split()).casefold()
    if text in ('не опасный', 'неопасный', 'не опасен', 'неопасен', 'non hazardous', 'non-hazardous',
                'not hazardous', 'non dangerous', 'non-dangerous', 'not dangerous'):
        return 'Не опасный'
    compact = re.sub(r'\s+', '', text).upper()
    if re.fullmatch(r'UN(?:\d{4})?', compact):
        return compact
    raise ValueError('UN (опасность): укажите UN, номер вида UN1234 или явно «Не опасный»')
