from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Entity, utcnow
from app.crm.codes import next_code


class Seller(Entity):
    __tablename__ = 'sellers'
    name: Mapped[str] = mapped_column(String(250))
    currency: Mapped[str] = mapped_column(String(3))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class Counterparty(Entity):
    __tablename__ = 'counterparties'
    __table_args__ = (CheckConstraint('internal_code > 0'), CheckConstraint("client_base IN ('cold', 'working')"),)
    internal_code: Mapped[int] = mapped_column(Integer, unique=True, default=next_code)
    client_base: Mapped[str] = mapped_column(String(20), default='working', server_default='working', index=True)
    name: Mapped[str] = mapped_column(String(250), index=True)
    kind: Mapped[str] = mapped_column(String(20), default='client')
    country: Mapped[str | None] = mapped_column(String(100))
    tax_id: Mapped[str | None] = mapped_column(String(100), index=True)
    email: Mapped[str | None] = mapped_column(String(254), index=True)
    phone: Mapped[str | None] = mapped_column(String(100), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    external_id: Mapped[str | None] = mapped_column(String(250), unique=True)
    source: Mapped[str | None] = mapped_column(String(250))


class Contact(Entity):
    __tablename__ = 'contacts'
    client_id: Mapped[str] = mapped_column(ForeignKey('counterparties.id'), index=True)
    name: Mapped[str] = mapped_column(String(250))
    position: Mapped[str | None] = mapped_column(String(200))
    department: Mapped[str | None] = mapped_column(String(200))
    purchase_area: Mapped[str | None] = mapped_column(String(500))
    comment: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(100))
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class CounterpartyDocument(Entity):
    __tablename__ = 'counterparty_documents'
    __table_args__ = (CheckConstraint("category IN ('founding', 'contract', 'other')"),)
    counterparty_id: Mapped[str] = mapped_column(ForeignKey('counterparties.id'), index=True)
    file_id: Mapped[str] = mapped_column(ForeignKey('files.id'), unique=True)
    category: Mapped[str] = mapped_column(String(20), default='other', server_default='other')
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')


class NumberCounter(Base):
    __tablename__ = 'number_counters'
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger, nullable=False)


class Call(Entity):
    __tablename__ = 'calls'
    client_id: Mapped[str] = mapped_column(ForeignKey('counterparties.id'), index=True)
    contact_id: Mapped[str | None] = mapped_column(ForeignKey('contacts.id'))
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    result: Mapped[str] = mapped_column(String(60))
    comment: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    task_id: Mapped[str | None] = mapped_column(ForeignKey('tasks.id', use_alter=True))
    reason: Mapped[str | None] = mapped_column(Text)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)


class Task(Entity):
    __tablename__ = 'tasks'
    title: Mapped[str] = mapped_column(String(250))
    entity_type: Mapped[str | None] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(36), index=True)
    assignee_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    priority: Mapped[str] = mapped_column(String(20), default='normal')
    status: Mapped[str] = mapped_column(String(20), default='assigned', index=True)
    result: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_key: Mapped[str | None] = mapped_column(String(200), unique=True)


class Request(Entity):
    __tablename__ = 'requests'
    number: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(250), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey('counterparties.id'), index=True)
    seller_id: Mapped[str | None] = mapped_column(ForeignKey('sellers.id'))
    wave_id: Mapped[str | None] = mapped_column(ForeignKey('waves.id'), index=True)
    contact_id: Mapped[str | None] = mapped_column(ForeignKey('contacts.id'))
    source_call_id: Mapped[str | None] = mapped_column(ForeignKey('calls.id'))
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    commercial_stage: Mapped[str] = mapped_column(String(50), default='new', index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    loss_reason: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    sale_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class RequestMember(Entity):
    __tablename__ = 'request_members'
    __table_args__ = (UniqueConstraint('request_id', 'user_id'),)
    request_id: Mapped[str] = mapped_column(ForeignKey('requests.id'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)


class RequestItem(Entity):
    __tablename__ = 'request_items'
    __table_args__ = (CheckConstraint('quantity IS NULL OR quantity > 0'),)
    request_id: Mapped[str] = mapped_column(ForeignKey('requests.id'), index=True)
    description: Mapped[str] = mapped_column(Text)
    source_columns: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_values: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_format: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default='{}')
    quote_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    work_status: Mapped[str] = mapped_column(String(20), default='requested', server_default='requested')
    product_group_id: Mapped[str | None] = mapped_column(ForeignKey('product_groups.id'), index=True)
    nomenclature_id: Mapped[str | None] = mapped_column(ForeignKey('nomenclatures.id'), index=True)
    packing_id: Mapped[str | None] = mapped_column(ForeignKey('packings.id'), index=True)
    article: Mapped[str | None] = mapped_column(String(150))
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey('counterparties.id'))
    supplier_country_id: Mapped[str | None] = mapped_column(ForeignKey('countries.id'))
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    purchase_currency_id: Mapped[str | None] = mapped_column(ForeignKey('currencies.id'))
    cas: Mapped[str | None] = mapped_column(String(30))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    unit: Mapped[str | None] = mapped_column(String(30))
    purity: Mapped[str | None] = mapped_column(String(200))
    packaging: Mapped[str | None] = mapped_column(String(200))
    allow_analogue: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    desired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(default=1)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class RequestItemRevision(Entity):
    __tablename__ = 'request_item_revisions'
    __table_args__ = (UniqueConstraint('item_id', 'revision'),)
    item_id: Mapped[str] = mapped_column(ForeignKey('request_items.id'), index=True)
    revision: Mapped[int]
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    reason: Mapped[str | None] = mapped_column(Text)


class ProductGroup(Entity):
    __tablename__ = 'product_groups'
    name: Mapped[str] = mapped_column(String(150), unique=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    internal_code: Mapped[int] = mapped_column(Integer, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Nomenclature(Entity):
    __tablename__ = 'nomenclatures'
    name: Mapped[str] = mapped_column(String(250), index=True)
    article: Mapped[str | None] = mapped_column(String(150), index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(250))
    purity: Mapped[str | None] = mapped_column(String(200))
    product_group_id: Mapped[str | None] = mapped_column(ForeignKey('product_groups.id'), index=True)
    cas: Mapped[str | None] = mapped_column(String(30), index=True)
    linear_formula: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Packing(Entity):
    __tablename__ = 'packings'
    __table_args__ = (UniqueConstraint('nomenclature_id', 'value', 'unit'), CheckConstraint('value > 0'),)
    nomenclature_id: Mapped[str] = mapped_column(ForeignKey('nomenclatures.id'), index=True)
    value: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    unit: Mapped[str] = mapped_column(String(30))
    display_name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Currency(Entity):
    __tablename__ = 'currencies'
    code: Mapped[str] = mapped_column(String(3), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Country(Entity):
    __tablename__ = 'countries'
    name: Mapped[str] = mapped_column(String(150), unique=True)
    iso2: Mapped[str] = mapped_column(String(2), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class QuoteSheet(Entity):
    __tablename__ = 'quote_sheets'
    number: Mapped[str] = mapped_column(String(80), unique=True)
    request_id: Mapped[str] = mapped_column(ForeignKey('requests.id'), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey('counterparties.id'), index=True)
    supplier_request_id: Mapped[str | None] = mapped_column(ForeignKey('supplier_requests.id'))
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id'))


class QuoteItem(Entity):
    __tablename__ = 'quote_items'
    __table_args__ = (
        CheckConstraint('quantity > 0 AND unit_price >= 0'),
        CheckConstraint('delivery_days IS NULL OR delivery_days >= 0'),
        Index('ix_quote_items_price_lookup', 'supplier_id', 'nomenclature_id', 'packing_id', 'currency_id', 'quoted_at'),
    )
    quote_id: Mapped[str] = mapped_column(ForeignKey('quote_sheets.id'), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey('counterparties.id'), index=True)
    nomenclature_id: Mapped[str] = mapped_column(ForeignKey('nomenclatures.id'), index=True)
    packing_id: Mapped[str] = mapped_column(ForeignKey('packings.id'), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    currency_id: Mapped[str] = mapped_column(ForeignKey('currencies.id'), index=True)
    delivery_days: Mapped[int | None]
    quoted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_request_item_id: Mapped[str] = mapped_column(ForeignKey('request_items.id'))
    price_source_id: Mapped[str | None] = mapped_column(ForeignKey('quote_items.id'))
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id'))


class ImportBatch(Entity):
    __tablename__ = 'import_batches'
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    source_name: Mapped[str] = mapped_column(String(250))
    file_key: Mapped[str] = mapped_column(String(250))
    file_hash: Mapped[str] = mapped_column(String(64))
    mapping: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default='preview')
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ImportRow(Entity):
    __tablename__ = 'import_rows'
    __table_args__ = (UniqueConstraint('batch_id', 'row_number'),)
    batch_id: Mapped[str] = mapped_column(ForeignKey('import_batches.id'), index=True)
    row_number: Mapped[int]
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    candidate_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    match_id: Mapped[str | None] = mapped_column(ForeignKey('counterparties.id'))
    action: Mapped[str] = mapped_column(String(30), default='create')


class TableImport(Entity):
    __tablename__ = 'table_imports'
    request_id: Mapped[str] = mapped_column(ForeignKey('requests.id'), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    source_name: Mapped[str] = mapped_column(String(250))
    file_metadata: Mapped[dict[str, Any]] = mapped_column(JSON)
    columns: Mapped[list[str]] = mapped_column(JSON)
    rows: Mapped[list[list[str]]] = mapped_column(JSON)
    mapping: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    plan: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default='preview')
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
