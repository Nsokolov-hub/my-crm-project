import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import (
    AwareDatetime,
    EmailStr,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from app.core.schemas import Input

# Supplier card details that calculations and supplier mail use. They are checked when the card is
# saved instead of failing later in a calculation; every other key stays free text as entered.
CALCULATION_TYPES = ('IMPORT', 'DAP', 'RUSSIA')


def checked_details(details: dict[str, Any] | None) -> dict[str, Any] | None:
    if not details:
        return details
    if details.get('calculation_type') not in (None, '', *CALCULATION_TYPES):
        raise ValueError('Тип расчёта поставщика: IMPORT, DAP или RUSSIA')
    amount = details.get('Логистика по умолчанию')
    if amount not in (None, ''):
        try:
            value = Decimal(str(amount).replace(',', '.').strip())
        except InvalidOperation:
            raise ValueError('Логистика по умолчанию: укажите сумму числом') from None
        if not value.is_finite() or value < 0:
            raise ValueError('Логистика по умолчанию: укажите неотрицательную сумму')
    currency = details.get('Валюта логистики')
    if currency not in (None, '') and not re.fullmatch(r'[A-Za-z]{3}', str(currency).strip()):
        raise ValueError('Валюта логистики: трёхбуквенный код, например USD')
    # Business remark of 31 October 2026: a supplier's shared address and its manager's, both get the request.
    for key, label in (('rfq_email', 'Общая почта для запросов поставщику'),
                       ('rfq_email_manager', 'Почта менеджера поставщика')):
        email = details.get(key)
        if email not in (None, ''):
            try:
                TypeAdapter(EmailStr).validate_python(str(email).strip())
            except ValidationError:
                raise ValueError(f'{label}: адрес указан неверно') from None
    return details


PHONE_EXTENSION = re.compile(r'\s*(?:доб\.?|вн\.?|ext\.?|x|#)\s*\d{1,6}\s*$', re.IGNORECASE)
PHONE_CHARS = re.compile(r'\+?[\d\s().\-]+')
# Digits of a whole international number for countries with one fixed length (Russia and Kazakhstan, the CIS,
# North America); other countries are checked by the general 10–15 digits only.
PHONE_LENGTHS = {'375': 12, '374': 11, '373': 11, '380': 12, '992': 12, '993': 11, '994': 12, '995': 12, '996': 12,
                 '998': 12, '7': 11, '1': 11}
PHONE_MESSAGE = ('Телефон указан неверно: 10–15 цифр с кодом города или страны, например +7 495 123-45-67. '
                 'Добавочный — «доб. 123», несколько номеров — через запятую')


def checked_phone(value: str | None) -> str | None:
    """Business remark of 31 October 2026: no more words or half numbers in the phone fields.

    Each number of the field (separated by «,» or «;») has 10 to 15 digits with its city or country code and only
    digits, «+», spaces, brackets, dots and dashes; an extension («доб. 12», «ext 12») may follow. The text is
    kept as entered.
    """
    if value is None or not value.strip():
        return None
    value = value.strip()
    for part in re.split(r'[,;]', value):
        number = PHONE_EXTENSION.sub('', part.strip())
        if not number or not PHONE_CHARS.fullmatch(number) or '+' in number[1:]:
            raise ValueError(PHONE_MESSAGE)
        digits = re.sub(r'\D', '', number)
        if not 10 <= len(digits) <= 15 or len(set(digits)) == 1:
            raise ValueError(PHONE_MESSAGE)
        if number.startswith('+'):
            code = next((code for code in PHONE_LENGTHS if digits.startswith(code)), None)
            if code and len(digits) != PHONE_LENGTHS[code]:
                raise ValueError(PHONE_MESSAGE)
    return value


class ClientInput(Input):
    name: str = Field(min_length=1, max_length=250)
    kind: Literal['client', 'supplier', 'both'] = 'client'
    client_base: Literal['cold', 'working'] = 'working'
    country: str | None = Field(default=None, max_length=100)
    tax_id: str | None = Field(default=None, max_length=100)
    in_belarus: bool = False
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=100)
    owner_id: str | None = None
    details: dict[str, Any] = {}
    source: str | None = Field(default=None, max_length=250)

    @field_validator('phone')
    @classmethod
    def phone_format(cls, value):
        return checked_phone(value)

    @field_validator('details')
    @classmethod
    def known_details(cls, value):
        return checked_details(value)

    @model_validator(mode='after')
    def cold_clients_only(self):
        if self.client_base == 'cold' and self.kind != 'client':
            raise ValueError('В базе обзвона могут быть только клиенты')
        return self


class PromoteClientInput(Input):
    version: int = Field(ge=1)


class StartItemsInput(Input):
    item_ids: list[str] = Field(min_length=1, max_length=10000)
    reason: str = Field(default='Передача позиций в работу', min_length=1, max_length=4000)


class ClientPatch(Input):
    version: int
    name: str = Field(default=None, min_length=1, max_length=250)
    kind: Literal['client', 'supplier', 'both'] = Field(default=None)
    country: str | None = None
    tax_id: str | None = None
    in_belarus: bool = Field(default=None)
    email: EmailStr | None = None
    phone: str | None = None
    owner_id: str = Field(default=None)
    details: dict[str, Any] = Field(default=None)
    reason: str | None = None

    @field_validator('phone')
    @classmethod
    def phone_format(cls, value):
        return checked_phone(value)

    @field_validator('details')
    @classmethod
    def known_details(cls, value):
        return checked_details(value)


class CounterpartyArchiveInput(Input):
    version: int = Field(ge=1)
    archived: bool
    reason: str = Field(min_length=3)
    idempotency_key: str = Field(min_length=8, max_length=128)


class CounterpartyMergeInput(Input):
    version: int = Field(ge=1)
    target_id: str
    reason: str = Field(min_length=3)
    idempotency_key: str = Field(min_length=8, max_length=128)


class ContactInput(Input):
    name: str = Field(min_length=1, max_length=250)
    position: str | None = Field(default=None, max_length=200)
    department: str | None = Field(default=None, max_length=200)
    purchase_area: str | None = Field(default=None, max_length=500)
    comment: str | None = Field(default=None, max_length=10000)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=100)

    @field_validator('phone')
    @classmethod
    def phone_format(cls, value):
        return checked_phone(value)


class ContactPatch(Input):
    version: int
    name: str = Field(default=None, min_length=1, max_length=250)
    position: str | None = None
    department: str | None = Field(default=None, max_length=200)
    purchase_area: str | None = Field(default=None, max_length=500)
    comment: str | None = Field(default=None, max_length=10000)
    email: EmailStr | None = None
    phone: str | None = None
    archived: bool = Field(default=None)
    reason: str | None = None

    @field_validator('phone')
    @classmethod
    def phone_format(cls, value):
        return checked_phone(value)


class ContactCreateInput(ContactInput):
    client_id: str


class CounterpartyDocumentPatch(Input):
    version: int = Field(ge=1)
    archived: bool
    reason: str | None = Field(default=None, max_length=4000)


class TaskInput(Input):
    title: str = Field(min_length=1, max_length=250)
    entity_type: Literal['request', 'counterparty', 'wave'] | None = None
    entity_id: str | None = None
    assignee_id: str | None = None
    due_at: AwareDatetime
    priority: Literal['low', 'normal', 'high', 'urgent'] = 'normal'


class BulkCallTaskInput(Input):
    client_ids: list[str] = Field(min_length=1, max_length=500)
    assignee_id: str
    due_at: AwareDatetime
    title: str = Field(default='Обзвонить клиента', min_length=1, max_length=250)
    priority: Literal['low', 'normal', 'high', 'urgent'] = 'normal'


class TaskPatch(Input):
    version: int
    title: str = Field(default=None, min_length=1, max_length=250)
    status: Literal['assigned', 'in_progress', 'completed', 'cancelled'] = Field(default=None)
    due_at: AwareDatetime | None = None
    assignee_id: str = Field(default=None)
    priority: Literal['low', 'normal', 'high', 'urgent'] = Field(default=None)
    result: str | None = Field(default=None, max_length=4000)


class CallInput(Input):
    client_id: str
    contact_id: str | None = None
    result: str = Field(min_length=1, max_length=60)
    comment: str | None = Field(default=None, max_length=10000)
    occurred_at: AwareDatetime | None = None
    next_at: AwareDatetime | None = None
    next_assignee_id: str | None = None
    reason: str | None = Field(default=None, max_length=4000)


class CallPatch(Input):
    version: int
    result: str = Field(default=None)
    comment: str | None = None
    cancelled: bool = Field(default=None)
    reason: str = Field(min_length=1)


class RequestInput(Input):
    title: str = Field(min_length=1, max_length=250)
    client_id: str
    seller_id: str | None = None
    contact_id: str | None = None
    source_call_id: str | None = None
    owner_id: str | None = None
    due_at: AwareDatetime | None = None
    is_test: bool = False


class RequestPatch(Input):
    version: int
    seller_id: str | None = None
    title: str = Field(default=None, min_length=1, max_length=250)
    owner_id: str = Field(default=None)
    due_at: AwareDatetime | None = None
    commercial_stage: str = Field(default=None)
    loss_reason: str | None = None
    archived: bool = Field(default=None)
    reason: str | None = None


class ItemInput(Input):
    description: str | None = Field(default=None, min_length=1, max_length=10000)
    product_group_id: str | None = None
    nomenclature_id: str | None = None
    packing_id: str | None = None
    article: str | None = Field(default=None, max_length=150)
    supplier_id: str | None = None
    supplier_country_id: str | None = None
    purchase_price: Decimal | None = Field(default=None, ge=0, max_digits=24, decimal_places=8)
    purchase_currency_id: str | None = None
    cas: str | None = Field(default=None, max_length=30)
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=24, decimal_places=6)
    unit: str | None = Field(default=None, max_length=30)
    purity: str | None = Field(default=None, max_length=200)
    packaging: str | None = Field(default=None, max_length=200)
    allow_analogue: bool = False
    desired_at: AwareDatetime | None = None
    comment: str | None = Field(default=None, max_length=10000)

    @field_validator('quantity', 'purchase_price', mode='before')
    @classmethod
    def decimal_string(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError('Количество передаётся десятичной строкой')
        return value


class ItemPatch(Input):
    version: int
    description: str = Field(default=None, min_length=1, max_length=10000)
    product_group_id: str | None = None
    nomenclature_id: str | None = None
    packing_id: str | None = None
    article: str | None = Field(default=None, max_length=150)
    supplier_id: str | None = None
    supplier_country_id: str | None = None
    purchase_price: Decimal | None = Field(default=None, ge=0, max_digits=24, decimal_places=8)
    purchase_currency_id: str | None = None
    cas: str | None = None
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=24, decimal_places=6)
    unit: str | None = None
    purity: str | None = None
    packaging: str | None = None
    allow_analogue: bool | None = None
    desired_at: AwareDatetime | None = None
    comment: str | None = None
    archived: bool = Field(default=None)
    reason: str = Field(min_length=1, max_length=4000)

    @field_validator('quantity', 'purchase_price', mode='before')
    @classmethod
    def decimal_string(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError('Количество и цена передаются десятичными строками')
        return value


class SellerInput(Input):
    name: str = Field(min_length=1, max_length=250)
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    details: dict[str, Any] = {}


class SellerUpdate(SellerInput):
    version: int = Field(ge=1)


class ShareInput(Input):
    version: int
    member_ids: list[str]
    reason: str = Field(min_length=1)


class ProductGroupInput(Input):
    name: str = Field(min_length=1, max_length=150)
    slug: str = Field(pattern=r'^[a-z][a-z0-9_]{0,79}$')


class CountryInput(Input):
    name: str = Field(min_length=1, max_length=150)
    iso2: str = Field(pattern=r'^[A-Z]{2}$')


class CurrencyInput(Input):
    code: str = Field(pattern=r'^[A-Z]{3}$')
    name: str = Field(min_length=1, max_length=100)


class PackingInput(Input):
    value: Decimal = Field(gt=0, max_digits=24, decimal_places=6)
    unit: str = Field(min_length=1, max_length=30)
    display_name: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator('value', mode='before')
    @classmethod
    def decimal_string(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError('Значение фасовки передаётся десятичной строкой')
        return value


class PackingPatch(Input):
    version: int = Field(ge=1)
    display_name: str = Field(default=None, min_length=1, max_length=100)
    active: bool = Field(default=None)


class ManufacturerInput(Input):
    name: str = Field(min_length=1, max_length=250)
    aliases: list[str] = Field(default_factory=list, max_length=100)
    umbrella_id: str | None = None


class ManufacturerPatch(Input):
    version: int = Field(ge=1)
    name: str = Field(default=None, min_length=1, max_length=250)
    # Other spellings; the previous name stays among them after a rename.
    aliases: list[str] = Field(default=None, max_length=100)
    umbrella_id: str | None = None


class ManufacturerMergeInput(Input):
    version: int = Field(ge=1)
    target_id: str
    reason: str = Field(min_length=3)
    idempotency_key: str = Field(min_length=8, max_length=128)


class NomenclatureInput(Input):
    name: str = Field(min_length=1, max_length=250)
    article: str | None = Field(default=None, max_length=150)
    # A registry entry, or a spelling: a known one finds its entry, a new one is added to the registry.
    manufacturer_id: str | None = None
    manufacturer: str | None = Field(default=None, max_length=250)
    purity: str | None = Field(default=None, max_length=200)
    product_group_id: str | None = None
    cas: str | None = Field(default=None, max_length=30)
    linear_formula: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    packings: list[PackingInput] = Field(min_length=1, max_length=100)


class NomenclaturePatch(Input):
    version: int = Field(ge=1)
    # Flow F4: the position goes to the site as a draft card (catalog.write only).
    publish_to_site: bool | None = None
    name: str = Field(default=None, min_length=1, max_length=250)
    article: str | None = Field(default=None, max_length=150)
    manufacturer_id: str | None = None
    manufacturer: str | None = Field(default=None, max_length=250)
    purity: str | None = Field(default=None, max_length=200)
    product_group_id: str | None = None
    cas: str | None = Field(default=None, max_length=30)
    linear_formula: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    active: bool | None = None


class QuoteItemInput(Input):
    source_request_item_id: str | None = None
    nomenclature_id: str
    packing_id: str
    quantity: Decimal = Field(gt=0, max_digits=24, decimal_places=6)
    unit_price: Decimal | None = Field(default=None, ge=0, max_digits=24, decimal_places=8)
    currency_id: str | None = None
    delivery_days: int | None = Field(default=None, ge=0)
    quoted_at: AwareDatetime | None = None

    @field_validator('quantity', 'unit_price', mode='before')
    @classmethod
    def decimal_string(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError('Количество и цена передаются десятичными строками')
        return value

    @field_validator('quantity')
    @classmethod
    def whole_packings(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError('Количество фасовок должно быть целым числом')
        return value


class QuoteSheetInput(Input):
    supplier_id: str
    supplier_request_id: str | None = None
    items: list[QuoteItemInput] = Field(min_length=1, max_length=10000)

    @model_validator(mode='after')
    def no_duplicate_source_rows(self):
        identities = [(item.source_request_item_id, item.nomenclature_id, item.packing_id) for item in self.items]
        if len(identities) != len(set(identities)):
            raise ValueError('Одинаковое предложение повторяется в одной квоте')
        return self
