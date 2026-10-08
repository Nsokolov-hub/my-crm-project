"""Calendar, supplier orders and decisions preserve their source snapshots."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Entity


class WorkflowReview(Entity):
    __tablename__ = "workflow_reviews"
    __table_args__ = (UniqueConstraint("kind", "entity_id", "source_version"),)
    kind: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    request_id: Mapped[str | None] = mapped_column(ForeignKey("requests.id"), index=True)
    source_version: Mapped[int]
    title: Mapped[str] = mapped_column(String(250))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    submitted_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text, default="")
    snapshot: Mapped[dict] = mapped_column(JSON)


class EmployeeAbsence(Entity):
    __tablename__ = "employee_absences"
    __table_args__ = (CheckConstraint("ends_at > starts_at"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class CalendarEntry(Entity):
    __tablename__ = "calendar_entries"
    __table_args__ = (CheckConstraint("amount > 0"),)
    direction: Mapped[str] = mapped_column(String(10))
    planned_date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    currency: Mapped[str] = mapped_column(String(3))
    purpose: Mapped[str] = mapped_column(Text)
    counterparty_id: Mapped[str | None] = mapped_column(ForeignKey("counterparties.id"))
    request_id: Mapped[str | None] = mapped_column(ForeignKey("requests.id"), index=True)
    supplier_order_id: Mapped[str | None] = mapped_column(ForeignKey("supplier_orders.id"))
    document_id: Mapped[str | None] = mapped_column(ForeignKey("commercial_documents.id"))
    payment_kind: Mapped[str] = mapped_column(String(20), default="other")
    recurrence: Mapped[str] = mapped_column(String(20), default="none")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    responsible_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class SupplierOrder(Entity):
    __tablename__ = "supplier_orders"
    number: Mapped[str] = mapped_column(String(80), unique=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("counterparties.id"), index=True)
    seller_id: Mapped[str] = mapped_column(ForeignKey("sellers.id"))
    currency: Mapped[str] = mapped_column(String(3))
    total: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    expected_date: Mapped[date] = mapped_column(Date)
    contract: Mapped[str] = mapped_column(Text, default="")
    payment_terms: Mapped[str] = mapped_column(Text, default="")
    delivery_terms: Mapped[str] = mapped_column(Text, default="")
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    snapshot: Mapped[dict] = mapped_column(JSON)


class SupplierOrderLine(Entity):
    __tablename__ = "supplier_order_lines"
    __table_args__ = (
        UniqueConstraint("execution_id"),
        CheckConstraint("quantity > 0 AND unit_price >= 0"),
    )
    order_id: Mapped[str] = mapped_column(ForeignKey("supplier_orders.id"), index=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    status: Mapped[str] = mapped_column(String(20), default="waiting")
    snapshot: Mapped[dict] = mapped_column(JSON)


class SupplierMail(Entity):
    __tablename__ = "supplier_mails"
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("counterparties.id"))
    recipient: Mapped[str] = mapped_column(String(254))
    cc: Mapped[list[str]] = mapped_column(JSON, default=list)
    subject: Mapped[str] = mapped_column(String(250))
    body: Mapped[str] = mapped_column(Text)
    html_body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
