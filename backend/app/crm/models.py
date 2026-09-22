from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Entity


class Seller(Entity):
    __tablename__ = 'sellers'
    name: Mapped[str] = mapped_column(String(250))
    currency: Mapped[str] = mapped_column(String(3))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class Counterparty(Entity):
    __tablename__ = 'counterparties'
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
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(100))
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


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
