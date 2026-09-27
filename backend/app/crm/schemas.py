from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, EmailStr, Field, field_validator, model_validator

from app.core.routes import Input


class ClientInput(Input):
    name: str = Field(min_length=1, max_length=250)
    kind: Literal['client', 'supplier', 'both'] = 'client'
    country: str | None = Field(default=None, max_length=100)
    tax_id: str | None = Field(default=None, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=100)
    owner_id: str | None = None
    details: dict[str, Any] = {}
    source: str | None = Field(default=None, max_length=250)


class ClientPatch(Input):
    version: int
    name: str = Field(default=None, min_length=1, max_length=250)
    kind: Literal['client', 'supplier', 'both'] = Field(default=None)
    country: str | None = None
    tax_id: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    owner_id: str = Field(default=None)
    details: dict[str, Any] = Field(default=None)
    archived: bool = Field(default=None)
    reason: str | None = None


class ContactInput(Input):
    name: str = Field(min_length=1, max_length=250)
    position: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=100)

class ContactPatch(Input):
    version: int
    name: str = Field(default=None, min_length=1, max_length=250)
    position: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    archived: bool = Field(default=None)
    reason: str | None = None


class TaskInput(Input):
    title: str = Field(min_length=1, max_length=250)
    entity_type: Literal['request', 'counterparty', 'wave'] | None = None
    entity_id: str | None = None
    assignee_id: str | None = None
    due_at: AwareDatetime
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


class NomenclatureInput(Input):
    name: str = Field(min_length=1, max_length=250)
    article: str | None = Field(default=None, max_length=150)
    product_group_id: str | None = None
    cas: str | None = Field(default=None, max_length=30)
    linear_formula: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    packings: list[PackingInput] = Field(min_length=1, max_length=100)


class NomenclaturePatch(Input):
    version: int = Field(ge=1)
    name: str = Field(default=None, min_length=1, max_length=250)
    article: str | None = Field(default=None, max_length=150)
    product_group_id: str | None = None
    cas: str | None = Field(default=None, max_length=30)
    linear_formula: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    active: bool | None = None


class QuoteItemInput(Input):
    source_request_item_id: str
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
    items: list[QuoteItemInput] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def no_duplicate_source_rows(self):
        source_ids = [item.source_request_item_id for item in self.items if item.source_request_item_id]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError('Одна позиция заявки не может повторяться в одной квоте')
        return self
