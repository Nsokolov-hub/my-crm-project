from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Entity


class Manufacturer(Entity, Base):
    __tablename__ = "manufacturers"
    name: Mapped[str] = mapped_column(String(250))
    normalized_name: Mapped[str] = mapped_column(String(250), unique=True)


class Substance(Entity, Base):
    __tablename__ = "substances"
    name: Mapped[str] = mapped_column(String(250))
    cas: Mapped[str | None] = mapped_column(String(20), unique=True)
    no_cas_reason: Mapped[str] = mapped_column(Text, default="")
    synonyms: Mapped[list] = mapped_column(JSON, default=list)


class Product(Entity, Base):
    __tablename__ = "products"
    substance_id: Mapped[str] = mapped_column(ForeignKey("substances.id"))
    manufacturer_id: Mapped[str] = mapped_column(ForeignKey("manufacturers.id"))
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(250))
    article: Mapped[str] = mapped_column(String(150), default="")
    purity: Mapped[str] = mapped_column(String(200), default="")
    packaging: Mapped[str] = mapped_column(String(200), default="")
    unit: Mapped[str] = mapped_column(String(20))
    package_quantity: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_reason: Mapped[str] = mapped_column(Text, default="")
    specification: Mapped[dict] = mapped_column(JSON, default=dict)


class SupplierRequest(Entity, Base):
    __tablename__ = "supplier_requests"
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("counterparties.id"))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("supplier_requests.id"))
    revision: Mapped[int] = mapped_column(default=1)
    snapshot: Mapped[dict] = mapped_column(JSON)
    file_key: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_channel: Mapped[str | None] = mapped_column(String(200))
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class Quote(Entity, Base):
    __tablename__ = "quotes"
    __table_args__ = (
        CheckConstraint("price >= 0 AND available_quantity > 0 AND minimum_quantity >= 0 AND multiple > 0"),
        
    )
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"), index=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("request_items.id"), index=True)
    item_revision: Mapped[int]
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    supplier_id: Mapped[str] = mapped_column(ForeignKey("counterparties.id"))
    supplier_request_id: Mapped[str | None] = mapped_column(ForeignKey("supplier_requests.id"))
    previous_id: Mapped[str | None] = mapped_column(ForeignKey("quotes.id"))
    revision: Mapped[int] = mapped_column(default=1)
    price: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    currency: Mapped[str] = mapped_column(String(3))
    price_unit: Mapped[str] = mapped_column(String(20))
    available_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    minimum_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6), default=0)
    multiple: Mapped[Decimal] = mapped_column(Numeric(24, 6), default=1)
    valid_until: Mapped[date | None] = mapped_column(Date)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)
    is_analogue: Mapped[bool] = mapped_column(Boolean, default=False)
    sample: Mapped[bool] = mapped_column(Boolean, default=False)
    attachments: Mapped[list] = mapped_column(JSON, default=list)
    terms: Mapped[dict] = mapped_column(JSON, default=dict)
    revision_reason: Mapped[str] = mapped_column(Text, default="")
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class CalculationProfile(Entity, Base):
    __tablename__ = "calculation_profiles"
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    previous_id: Mapped[str | None] = mapped_column(ForeignKey("calculation_profiles.id"))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_until: Mapped[date | None] = mapped_column(Date)
    definition: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class Calculation(Entity, Base):
    __tablename__ = "calculations"
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("calculation_profiles.id"))
    previous_id: Mapped[str | None] = mapped_column(ForeignKey("calculations.id"))
    snapshot: Mapped[dict] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class CommercialDocument(Entity, Base):
    __tablename__ = "commercial_documents"
    __table_args__ = (UniqueConstraint("seller_id", "kind", "number"), CheckConstraint("total >= 0"))
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"), index=True)
    seller_id: Mapped[str] = mapped_column(ForeignKey("sellers.id"))
    client_id: Mapped[str] = mapped_column(ForeignKey("counterparties.id"))
    kind: Mapped[str] = mapped_column(String(20))
    number: Mapped[str] = mapped_column(String(80))
    calculation_id: Mapped[str] = mapped_column(ForeignKey("calculations.id"))
    proposal_id: Mapped[str | None] = mapped_column(ForeignKey("commercial_documents.id"))
    previous_id: Mapped[str | None] = mapped_column(ForeignKey("commercial_documents.id"))
    status: Mapped[str] = mapped_column(String(30), default="issued")
    currency: Mapped[str] = mapped_column(String(3))
    total: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    valid_until: Mapped[date] = mapped_column(Date)
    snapshot: Mapped[dict] = mapped_column(JSON)
    files: Mapped[dict] = mapped_column(JSON)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_channel: Mapped[str | None] = mapped_column(String(200))
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class Execution(Entity, Base):
    __tablename__ = "executions"
    __table_args__ = (
        CheckConstraint("quantity > 0 AND cancelled_quantity >= 0 AND cancelled_quantity <= quantity"),
    )
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"), index=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("request_items.id"))
    proposal_id: Mapped[str] = mapped_column(ForeignKey("commercial_documents.id"))
    quote_id: Mapped[str] = mapped_column(ForeignKey("quotes.id"))
    line_id: Mapped[str] = mapped_column(String(80))
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    cancelled_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6), default=0)
    unit: Mapped[str] = mapped_column(String(20))
    snapshot: Mapped[dict] = mapped_column(JSON)
    acceptance_reason: Mapped[str] = mapped_column(Text)
    accepted_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    revision: Mapped[int] = mapped_column(default=1)
    financing_deficit: Mapped[bool] = mapped_column(Boolean, default=False)


class Payment(Entity, Base):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("seller_id", "external_id"), CheckConstraint("amount > 0"))
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("counterparties.id"))
    seller_id: Mapped[str] = mapped_column(ForeignKey("sellers.id"))
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("commercial_documents.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    currency: Mapped[str] = mapped_column(String(3))
    payment_date: Mapped[date] = mapped_column(Date)
    number: Mapped[str] = mapped_column(String(200))
    external_id: Mapped[str | None] = mapped_column(String(250))
    comment: Mapped[str] = mapped_column(Text, default="")
    evidence_file_id: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="declared")
    declared_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentAllocation(Entity, Base):
    __tablename__ = "payment_allocations"
    __table_args__ = (CheckConstraint("amount > 0"),)
    payment_id: Mapped[str] = mapped_column(ForeignKey("payments.id"), index=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("commercial_documents.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class PaymentReversal(Entity, Base):
    __tablename__ = "payment_reversals"
    __table_args__ = (CheckConstraint("amount > 0"),)
    payment_id: Mapped[str] = mapped_column(ForeignKey("payments.id"), index=True)
    allocation_id: Mapped[str | None] = mapped_column(ForeignKey("payment_allocations.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    kind: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class Approval(Entity, Base):
    __tablename__ = "approvals"
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"), index=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    submitted_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text, default="")


class Wave(Entity, Base):
    __tablename__ = "waves"
    number: Mapped[str] = mapped_column(String(80), unique=True)
    route: Mapped[str] = mapped_column(String(300))
    origin_country: Mapped[str] = mapped_column(String(100))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(30), default="planned")
    close_date: Mapped[date] = mapped_column(Date)
    departure_date: Mapped[date] = mapped_column(Date)
    arrival_date: Mapped[date] = mapped_column(Date)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class WaveAllocation(Entity, Base):
    __tablename__ = "wave_allocations"
    __table_args__ = (CheckConstraint("quantity > 0"),)
    wave_id: Mapped[str] = mapped_column(ForeignKey("waves.id"), index=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id"), index=True)
    approval_id: Mapped[str] = mapped_column(ForeignKey("approvals.id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    previous_id: Mapped[str | None] = mapped_column(ForeignKey("wave_allocations.id"))
    reason: Mapped[str] = mapped_column(Text, default="")


class FulfillmentEvent(Entity, Base):
    __tablename__ = "fulfillment_events"
    allocation_id: Mapped[str] = mapped_column(ForeignKey("wave_allocations.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text)
    evidence_file_id: Mapped[str | None] = mapped_column(String(80))
    correction_of: Mapped[str | None] = mapped_column(ForeignKey("fulfillment_events.id"))
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
