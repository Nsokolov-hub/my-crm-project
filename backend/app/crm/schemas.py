from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import EmailStr, Field, field_validator

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
    name: str | None = Field(default=None, min_length=1, max_length=250)
    kind: Literal['client', 'supplier', 'both'] | None = None
    country: str | None = None
    tax_id: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    owner_id: str | None = None
    details: dict[str, Any] | None = None
    archived: bool | None = None
    reason: str | None = None


class ContactInput(Input):
    name: str = Field(min_length=1, max_length=250)
    position: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=100)


class TaskInput(Input):
    title: str = Field(min_length=1, max_length=250)
    entity_type: Literal['request', 'counterparty', 'wave'] | None = None
    entity_id: str | None = None
    assignee_id: str | None = None
    due_at: datetime
    priority: Literal['low', 'normal', 'high', 'urgent'] = 'normal'

    @field_validator('due_at')
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError('Укажите часовой пояс даты')
        return value


class TaskPatch(Input):
    version: int
    title: str | None = Field(default=None, min_length=1, max_length=250)
    status: Literal['assigned', 'in_progress', 'completed', 'cancelled'] | None = None
    due_at: datetime | None = None
    assignee_id: str | None = None
    priority: Literal['low', 'normal', 'high', 'urgent'] | None = None
    result: str | None = Field(default=None, max_length=4000)


class CallInput(Input):
    client_id: str
    contact_id: str | None = None
    result: str = Field(min_length=1, max_length=60)
    comment: str | None = Field(default=None, max_length=10000)
    occurred_at: datetime | None = None
    next_at: datetime | None = None
    next_assignee_id: str | None = None
    reason: str | None = Field(default=None, max_length=4000)


class CallPatch(Input):
    version: int
    result: str | None = None
    comment: str | None = None
    cancelled: bool | None = None
    reason: str = Field(min_length=1)


class RequestInput(Input):
    title: str = Field(min_length=1, max_length=250)
    client_id: str
    seller_id: str | None = None
    contact_id: str | None = None
    source_call_id: str | None = None
    owner_id: str | None = None
    due_at: datetime | None = None
    is_test: bool = False


class RequestPatch(Input):
    version: int
    seller_id: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=250)
    owner_id: str | None = None
    due_at: datetime | None = None
    commercial_stage: str | None = None
    loss_reason: str | None = None
    archived: bool | None = None
    reason: str | None = None


class ItemInput(Input):
    description: str = Field(min_length=1, max_length=10000)
    cas: str | None = Field(default=None, max_length=30)
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=24, decimal_places=6)
    unit: str | None = Field(default=None, max_length=30)
    purity: str | None = Field(default=None, max_length=200)
    packaging: str | None = Field(default=None, max_length=200)
    allow_analogue: bool = False
    desired_at: datetime | None = None
    comment: str | None = Field(default=None, max_length=10000)

    @field_validator('quantity', mode='before')
    @classmethod
    def decimal_string(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError('Количество передаётся десятичной строкой')
        return value


class ItemPatch(Input):
    version: int
    description: str | None = Field(default=None, min_length=1, max_length=10000)
    cas: str | None = None
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=24, decimal_places=6)
    unit: str | None = None
    purity: str | None = None
    packaging: str | None = None
    allow_analogue: bool | None = None
    desired_at: datetime | None = None
    comment: str | None = None
    archived: bool | None = None
    reason: str = Field(min_length=1, max_length=4000)


class SellerInput(Input):
    name: str = Field(min_length=1, max_length=250)
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    details: dict[str, Any] = {}


class ShareInput(Input):
    version: int
    member_ids: list[str]
    reason: str = Field(min_length=1)
