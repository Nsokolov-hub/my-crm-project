"""Site requests (the «Заявки с сайта» section) and the credentials the site calls the CRM with.

A site request is stored exactly as the customer sent it. No client or CRM request is created on arrival,
so spam and duplicates never reach the client base; an employee turns it into a request later.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Entity, utcnow

SITE_REQUEST_STATUSES = ('new', 'in_work', 'converted', 'rejected')
SITE_REQUEST_KINDS = ('product_rfq', 'one_click', 'service', 'general')
ANALOGUE_CHOICES = ('none', 'allowed', 'requested')


class IntegrationToken(Entity):
    """A credential of the site service account; two may be active at once while one is rotated."""

    __tablename__ = 'integration_tokens'
    name: Mapped[str] = mapped_column(String(100))
    # The account recorded as author in the audit trail and of uploaded attachments.
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # The HMAC key must be readable to verify signatures; it is kept encrypted with SECRET_KEY.
    secret_encrypted: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SiteRequest(Entity):
    __tablename__ = 'site_requests'
    __table_args__ = (
        CheckConstraint("status IN ('new', 'in_work', 'converted', 'rejected')"),
        CheckConstraint("kind IN ('product_rfq', 'one_click', 'service', 'general')"),
        CheckConstraint("lang IN ('ru', 'en')"),
    )
    number: Mapped[str] = mapped_column(String(40), unique=True)
    # The site's UUID of the request; it is also the Idempotency-Key of every delivery attempt.
    site_request_id: Mapped[str] = mapped_column(String(36), unique=True)
    site_number: Mapped[str] = mapped_column(String(40), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(20))
    lang: Mapped[str] = mapped_column(String(2), default='ru')
    form_type: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default='new', index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    page_url: Mapped[str | None] = mapped_column(String(2000))
    # Contacts and company as the customer typed them.
    customer_name: Mapped[str] = mapped_column(String(200))
    company: Mapped[str] = mapped_column(String(250), index=True)
    inn: Mapped[str | None] = mapped_column(String(12), index=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    city: Mapped[str | None] = mapped_column(String(200))
    comment: Mapped[str | None] = mapped_column(Text)
    desired_date: Mapped[date | None] = mapped_column(Date)
    marketing_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    # Form of a service request: {slug, name, fields: {label: value}}.
    service: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), index=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The CRM request created from this one.
    request_id: Mapped[str | None] = mapped_column(ForeignKey('requests.id'), index=True)
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(Text)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SiteRequestItem(Entity):
    __tablename__ = 'site_request_items'
    __table_args__ = (
        CheckConstraint('quantity > 0'),
        CheckConstraint("analogue IN ('none', 'allowed', 'requested')"),
    )
    site_request_id: Mapped[str] = mapped_column(ForeignKey('site_requests.id'), index=True)
    position: Mapped[int] = mapped_column(Integer)
    site_product_id: Mapped[str | None] = mapped_column(String(100))
    crm_product_id: Mapped[str | None] = mapped_column(String(36))
    brand: Mapped[str | None] = mapped_column(String(200))
    catalog_number: Mapped[str | None] = mapped_column(String(150))
    cas: Mapped[str | None] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(500))
    packaging: Mapped[str | None] = mapped_column(String(200))
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    unit: Mapped[str] = mapped_column(String(20))
    analogue: Mapped[str] = mapped_column(String(20), default='none')
    # A packaging the customer chose themselves rather than one from the manufacturer's list.
    custom_packaging: Mapped[bool] = mapped_column(Boolean, default=False)
    comment: Mapped[str | None] = mapped_column(Text)
    page_url: Mapped[str | None] = mapped_column(String(2000))


class SiteRequestAttribution(Entity):
    """Where the customer came from: first and last touch, advertising click ids and analytics client ids."""

    __tablename__ = 'site_request_attributions'
    __table_args__ = (
        CheckConstraint("channel IS NULL OR channel IN ('cpc', 'organic', 'referral', 'direct', 'email', 'social')"),
    )
    site_request_id: Mapped[str] = mapped_column(ForeignKey('site_requests.id'), unique=True)
    channel: Mapped[str | None] = mapped_column(String(20), index=True)
    conversion_page_url: Mapped[str | None] = mapped_column(String(2000))
    first_landing_url: Mapped[str | None] = mapped_column(String(2000))
    first_referrer: Mapped[str | None] = mapped_column(String(2000))
    first_utm_source: Mapped[str | None] = mapped_column(String(250))
    first_utm_medium: Mapped[str | None] = mapped_column(String(250))
    first_utm_campaign: Mapped[str | None] = mapped_column(String(250))
    first_utm_content: Mapped[str | None] = mapped_column(String(250))
    first_utm_term: Mapped[str | None] = mapped_column(String(250))
    first_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_landing_url: Mapped[str | None] = mapped_column(String(2000))
    last_referrer: Mapped[str | None] = mapped_column(String(2000))
    last_utm_source: Mapped[str | None] = mapped_column(String(250))
    last_utm_medium: Mapped[str | None] = mapped_column(String(250))
    last_utm_campaign: Mapped[str | None] = mapped_column(String(250))
    last_utm_content: Mapped[str | None] = mapped_column(String(250))
    last_utm_term: Mapped[str | None] = mapped_column(String(250))
    last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    yclid: Mapped[str | None] = mapped_column(String(250))
    gclid: Mapped[str | None] = mapped_column(String(250))
    ym_uid: Mapped[str | None] = mapped_column(String(100))
    ga_client_id: Mapped[str | None] = mapped_column(String(100))


class SiteSubscriber(Entity):
    """A subscriber of the site's mailing (site TZ 11.2): a contact for mailings, with the consent from the site.

    The site is the master: it sends every change (subscribed, unsubscribed) with its own version; an older version
    arriving late never overwrites a newer one.
    """

    __tablename__ = 'site_subscribers'
    site_subscriber_id: Mapped[str] = mapped_column(String(36), unique=True)
    site_version: Mapped[int] = mapped_column(Integer, default=1)
    email: Mapped[str] = mapped_column(String(254), index=True)
    lang: Mapped[str] = mapped_column(String(2), default='ru')
    subscribed: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str | None] = mapped_column(String(40))
    page_url: Mapped[str | None] = mapped_column(String(2000))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
