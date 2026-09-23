from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Entity


class User(Entity):
    __tablename__ = 'users'
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    mfa_secret: Mapped[str | None] = mapped_column(Text)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_last_step: Mapped[int] = mapped_column(Integer, default=-1)


class Role(Entity):
    __tablename__ = 'roles'
    name: Mapped[str] = mapped_column(String(200), unique=True)


class UserRole(Entity):
    __tablename__ = 'user_roles'
    __table_args__ = (UniqueConstraint('user_id', 'role_id'),)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    role_id: Mapped[str] = mapped_column(ForeignKey('roles.id'), index=True)


class PermissionGrant(Entity):
    __tablename__ = 'permission_grants'
    __table_args__ = (CheckConstraint('(user_id IS NULL) <> (role_id IS NULL)'),)
    user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), index=True)
    role_id: Mapped[str | None] = mapped_column(ForeignKey('roles.id'), index=True)
    code: Mapped[str] = mapped_column(String(100), index=True)
    scope: Mapped[str] = mapped_column(String(10), default='own')
    allow: Mapped[bool] = mapped_column(Boolean, default=True)


class AuthSession(Entity):
    __tablename__ = 'auth_sessions'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class LoginAttempt(Entity):
    __tablename__ = 'login_attempts'
    identity: Mapped[str] = mapped_column(String(254), index=True)
    ip: Mapped[str] = mapped_column(String(100), index=True)
    successful: Mapped[bool] = mapped_column(Boolean)


class AuditEvent(Entity):
    __tablename__ = 'audit_events'
    actor_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), index=True)
    entity_type: Mapped[str] = mapped_column(String(80), index=True)
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(100))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str] = mapped_column(String(100), index=True)


class IdempotencyRecord(Entity):
    __tablename__ = 'idempotency_records'
    __table_args__ = (UniqueConstraint('user_id', 'scope', 'key'),)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    scope: Mapped[str] = mapped_column(String(200))
    key: Mapped[str] = mapped_column(String(128))
    payload_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, Any]] = mapped_column(JSON)


class Notification(Entity):
    __tablename__ = 'notifications'
    __table_args__ = (UniqueConstraint('user_id', 'event_key'),)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    event_key: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(250))
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(36))
    read: Mapped[bool] = mapped_column(Boolean, default=False)


class OutboxEvent(Entity):
    __tablename__ = 'outbox_events'
    event_key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default='pending', index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class AppSetting(Entity):
    __tablename__ = 'app_settings'
    key: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    previous_id: Mapped[str | None] = mapped_column(ForeignKey('app_settings.id'))
    effective_from: Mapped[date] = mapped_column(Date, default=date.today)
    effective_until: Mapped[date | None] = mapped_column(Date)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
