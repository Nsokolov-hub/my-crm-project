from decimal import Decimal
from typing import Any, Literal

from pydantic import EmailStr, Field, field_validator, AwareDatetime

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
    description: str = Field(min_length=1, max_length=10000)
    cas: str | None = Field(default=None, max_length=30)
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=24, decimal_places=6)
    unit: str | None = Field(default=None, max_length=30)
    purity: str | None = Field(default=None, max_length=200)
    packaging: str | None = Field(default=None, max_length=200)
    allow_analogue: bool = False
    desired_at: AwareDatetime | None = None
    comment: str | None = Field(default=None, max_length=10000)

    @field_validator('quantity', mode='before')
    @classmethod
    def decimal_string(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError('Количество передаётся десятичной строкой')
        return value


class ItemPatch(Input):
    version: int
    description: str = Field(default=None, min_length=1, max_length=10000)
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


class SellerInput(Input):
    name: str = Field(min_length=1, max_length=250)
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    details: dict[str, Any] = {}


class ShareInput(Input):
    version: int
    member_ids: list[str]
    reason: str = Field(min_length=1)
