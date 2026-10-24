"""Contract F1 «site request → CRM» (docs/site-integration.md) and the views of the «Заявки с сайта» section.

Unknown fields are ignored rather than rejected: the site may add a field before the CRM learns to store it,
and a delivery must not fail because of that. The site validates contacts strictly; the CRM checks only what
it relies on, so a request the customer could send is never stuck between the two systems.
"""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.core.schemas import Input

MAX_ITEMS = 200
MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
# Formats the site's forms accept (RFQ-03).
ATTACHMENT_EXTENSIONS = ('.xlsx', '.csv', '.pdf', '.docx')

Channel = Literal['cpc', 'organic', 'referral', 'direct', 'email', 'social']
Kind = Literal['product_rfq', 'one_click', 'service', 'general']
Analogue = Literal['none', 'allowed', 'requested']
Unit = Literal['pack', 'mg', 'g', 'kg', 'ml', 'l', 'pcs']


class Contract(BaseModel):
    model_config = ConfigDict(extra='ignore', str_strip_whitespace=True)


def blank_to_none(value: object) -> object:
    return None if isinstance(value, str) and not value.strip() else value


class SiteCustomer(Contract):
    name: str = Field(min_length=1, max_length=200)
    company: str = Field(min_length=1, max_length=250)
    inn: str | None = Field(default=None, max_length=12)
    phone: str = Field(pattern=r'^\+[1-9][0-9]{6,14}$', description='E.164: +79991234567')
    email: str = Field(min_length=3, max_length=254)
    city: str | None = Field(default=None, max_length=200)
    comment: str | None = Field(default=None, max_length=10000)
    marketing_consent: bool = False

    _blank = field_validator('inn', 'city', 'comment', mode='before')(blank_to_none)

    @field_validator('inn')
    @classmethod
    def inn_digits(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r'[0-9]{10}|[0-9]{12}', value):
            raise ValueError('ИНН — 10 или 12 цифр')
        return value

    @field_validator('email')
    @classmethod
    def email_shape(cls, value: str) -> str:
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ValueError('Некорректный e-mail')
        return value


class SiteItem(Contract):
    site_product_id: str | None = Field(default=None, max_length=100)
    crm_product_id: str | None = Field(default=None, max_length=36)
    brand: str | None = Field(default=None, max_length=200)
    catalog_number: str | None = Field(default=None, max_length=150)
    cas: str | None = Field(default=None, max_length=30)
    name: str = Field(min_length=1, max_length=500)
    packaging: str | None = Field(default=None, max_length=200)
    quantity: Decimal = Field(gt=0, le=Decimal('1000000'), max_digits=18, decimal_places=6)
    unit: Unit = 'pack'
    analogue: Analogue = 'none'
    custom_packaging: bool = False
    comment: str | None = Field(default=None, max_length=5000)
    page_url: str | None = Field(default=None, max_length=2000)

    _blank = field_validator('site_product_id', 'crm_product_id', 'brand', 'catalog_number', 'cas', 'packaging',
                             'comment', 'page_url', mode='before')(blank_to_none)


class SiteService(Contract):
    slug: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=250)
    fields: dict[str, str] = Field(default_factory=dict, max_length=50)


class SiteAttachment(Contract):
    file_name: str = Field(min_length=1, max_length=250)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    size: int = Field(gt=0, le=MAX_ATTACHMENT_BYTES)


class SiteTouch(Contract):
    landing_url: str | None = Field(default=None, max_length=2000)
    referrer: str | None = Field(default=None, max_length=2000)
    utm_source: str | None = Field(default=None, max_length=250)
    utm_medium: str | None = Field(default=None, max_length=250)
    utm_campaign: str | None = Field(default=None, max_length=250)
    utm_content: str | None = Field(default=None, max_length=250)
    utm_term: str | None = Field(default=None, max_length=250)
    yclid: str | None = Field(default=None, max_length=250)
    gclid: str | None = Field(default=None, max_length=250)
    at: datetime | None = None


class SiteClientIds(Contract):
    ym_uid: str | None = Field(default=None, max_length=100)
    ga_client_id: str | None = Field(default=None, max_length=100)


class SiteAttribution(Contract):
    first_touch: SiteTouch | None = None
    last_touch: SiteTouch | None = None
    conversion_page_url: str | None = Field(default=None, max_length=2000)
    channel: Channel | None = None
    client_ids: SiteClientIds | None = None


class SiteRequestIn(Contract):
    """Body of POST /api/v1/integrations/site/requests (the `payload` part of the multipart form)."""

    site_request_id: str = Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    site_number: str = Field(min_length=1, max_length=40)
    kind: Kind
    lang: Literal['ru', 'en'] = 'ru'
    form_type: str | None = Field(default=None, max_length=40)
    created_at: datetime
    page_url: str | None = Field(default=None, max_length=2000)
    desired_date: date | None = None
    customer: SiteCustomer
    items: list[SiteItem] = Field(default_factory=list, max_length=MAX_ITEMS)
    service: SiteService | None = None
    attachments: list[SiteAttachment] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)
    attribution: SiteAttribution | None = None

    @model_validator(mode='after')
    def items_for_kind(self) -> 'SiteRequestIn':
        if self.kind in ('product_rfq', 'one_click') and not self.items:
            raise ValueError('В запросе КП нужна хотя бы одна позиция')
        if self.kind == 'one_click' and len(self.items) != 1:
            raise ValueError('«Заказ в 1 клик» содержит одну позицию')
        if self.kind == 'service' and self.service is None:
            raise ValueError('В заявке на услугу нужен блок service')
        return self


class SiteRequestReceipt(BaseModel):
    """Answer to the site: the record the CRM keeps for this request."""

    id: str
    number: str
    status: str
    received_at: datetime


class AssignInput(Input):
    assignee_id: str = Field(min_length=1, max_length=36)
    version: int = Field(ge=1)
    # Required when the request already has a responsible employee (reassignment).
    reason: str | None = Field(default=None, min_length=1, max_length=2000)


class RejectInput(Input):
    reason: str = Field(min_length=1, max_length=2000)
    version: int = Field(ge=1)


# ── «Создать заявку» (TZ 9.4.1): one window, one command ──

class NewClientInput(Input):
    """A new client card, filled from the site request and checked by the employee."""

    name: str = Field(min_length=1, max_length=250)
    tax_id: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=100)
    email: EmailStr | None = None


class NewContactInput(Input):
    name: str = Field(min_length=1, max_length=250)
    position: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=100)
    email: EmailStr | None = None


class ConvertItemInput(Input):
    """A request line as it will be saved; the employee may correct the proposal before saving."""

    site_item_id: str | None = Field(default=None, max_length=36)
    description: str = Field(min_length=1, max_length=10000)
    article: str | None = Field(default=None, max_length=150)
    cas: str | None = Field(default=None, max_length=30)
    packaging: str | None = Field(default=None, max_length=200)
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=24, decimal_places=6)
    unit: Literal['g', 'kg', 'mg', 'l', 'ml', 'pcs'] | None = None
    allow_analogue: bool = False
    comment: str | None = Field(default=None, max_length=10000)

    @field_validator('quantity', mode='before')
    @classmethod
    def decimal_string(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError('Количество передаётся десятичной строкой')
        return value


class ConvertInput(Input):
    version: int = Field(ge=1)
    # An existing client found by the search, or a new card — exactly one of them.
    client_id: str | None = Field(default=None, max_length=36)
    client: NewClientInput | None = None
    # An existing contact of that client, or a new one — exactly one of them.
    contact_id: str | None = Field(default=None, max_length=36)
    contact: NewContactInput | None = None
    title: str = Field(min_length=1, max_length=250)
    items: list[ConvertItemInput] = Field(default_factory=list, max_length=MAX_ITEMS)

    @model_validator(mode='after')
    def one_client_and_contact(self) -> 'ConvertInput':
        if (self.client_id is None) == (self.client is None):
            raise ValueError('Выберите найденного клиента или заполните карточку нового')
        if (self.contact_id is None) == (self.contact is None):
            raise ValueError('Выберите контакт клиента или заполните новый')
        if self.client is not None and self.contact_id is not None:
            raise ValueError('У нового клиента контакт тоже новый')
        return self


def contract_schemas() -> dict[str, Any]:
    """JSON schemas of contract F1 for docs/openapi.json.

    The endpoint parses its multipart body itself (the signature covers the raw bytes), so FastAPI cannot see the
    payload model; app.main adds these schemas to the document and the site pins them for its contract test.
    """
    schemas: dict[str, Any] = {}
    for model in (SiteRequestIn, SiteRequestReceipt):
        schema = model.model_json_schema(ref_template='#/components/schemas/{model}')
        schemas.update(schema.pop('$defs', {}))
        schemas[model.__name__] = schema
    return schemas


RECEIVE_REQUEST_BODY = {'required': True, 'content': {'multipart/form-data': {
    'schema': {'type': 'object', 'required': ['payload'], 'properties': {
        'payload': {'type': 'string', 'contentMediaType': 'application/json',
                    'contentSchema': {'$ref': '#/components/schemas/SiteRequestIn'},
                    'description': 'JSON заявки SiteRequestIn'},
        'files': {'type': 'array', 'maxItems': MAX_ATTACHMENTS, 'items': {'type': 'string', 'format': 'binary'},
                  'description': 'Вложения в порядке attachments'},
    }},
    'encoding': {'payload': {'contentType': 'application/json'}},
}}}
