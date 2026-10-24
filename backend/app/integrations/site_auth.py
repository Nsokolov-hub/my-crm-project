"""Authentication of calls from the site: address allow-list, service token and HMAC signature of the body.

Headers of every call (docs/site-integration.md):

- ``Authorization: Bearer <token>`` — the database keeps only its SHA-256;
- ``X-Site-Timestamp: <unix seconds>`` — the call is accepted within SITE_INTEGRATION_MAX_SKEW_SECONDS;
- ``X-Site-Signature: sha256=<hex>`` — HMAC-SHA256 of ``<timestamp>.<raw body>`` with the token's secret.
"""

import hashlib
import hmac
import ipaddress
import secrets
import time
from collections.abc import Mapping

from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import utcnow
from app.core.errors import DomainError
from app.core.models import User
from app.core.security import cipher, digest, password_hasher
from app.integrations.models import IntegrationToken

SITE_USER_EMAIL = 'site-integration@system.invalid'
SITE_USER_NAME = 'Сайт (интеграция)'
TOKEN_PREFIX = 'ogk_site_'


def address_allowed(host: str | None) -> bool:
    rules = [rule.strip() for rule in settings.site_integration_allowed_ips.split(',') if rule.strip()]
    if not rules:
        # Development only: production must name the site server explicitly.
        return settings.environment != 'production'
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    for rule in rules:
        try:
            if address in ipaddress.ip_network(rule, strict=False):
                return True
        except ValueError:
            continue
    return False


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return 'sha256=' + hmac.new(secret.encode(), timestamp.encode() + b'.' + body, hashlib.sha256).hexdigest()


def authenticate(db: Session, headers: Mapping[str, str], host: str | None, body: bytes) -> IntegrationToken:
    """The active token that signed this call, or a DomainError the site can act on."""
    if not address_allowed(host):
        raise DomainError('INTEGRATION_ADDRESS_FORBIDDEN', 'Адрес сервера сайта не входит в список разрешённых', 403)
    authorization = headers.get('authorization', '')
    if not authorization.startswith('Bearer ') or not authorization[7:].strip():
        raise DomainError('INTEGRATION_UNAUTHENTICATED', 'Передайте сервисный токен сайта', 401)
    token = db.scalar(select(IntegrationToken).where(IntegrationToken.token_hash == digest(authorization[7:].strip()),
                                                     IntegrationToken.active.is_(True)))
    if token is None:
        raise DomainError('INTEGRATION_UNAUTHENTICATED', 'Сервисный токен сайта не найден или отозван', 401)
    stamp = headers.get('x-site-timestamp', '')
    if not stamp.isdigit() or abs(time.time() - int(stamp)) > settings.site_integration_max_skew_seconds:
        raise DomainError('INTEGRATION_SIGNATURE_EXPIRED', 'Подпись устарела: сверьте часы серверов сайта и CRM', 401)
    try:
        secret = cipher().decrypt(token.secret_encrypted.encode()).decode()
    except InvalidToken:
        # SECRET_KEY changed after the token was issued: issue a new one.
        raise DomainError('INTEGRATION_UNAUTHENTICATED', 'Ключ подписи недоступен: выпустите новый токен', 401) from None
    if not secrets.compare_digest(sign(secret, stamp, body), headers.get('x-site-signature', '')):
        raise DomainError('INTEGRATION_SIGNATURE_INVALID', 'Подпись запроса не совпадает', 401)
    token.last_used_at = utcnow()
    return token


def site_user(db: Session) -> User:
    """The service account «Сайт»: author of site requests in the history; it cannot sign in."""
    user = db.scalar(select(User).where(User.email == SITE_USER_EMAIL))
    if user is None:
        user = User(email=SITE_USER_EMAIL, name=SITE_USER_NAME, active=False,
                    password_hash=password_hasher.hash(secrets.token_urlsafe(48)))
        db.add(user)
        db.flush()
    return user


def issue_token(db: Session, name: str) -> tuple[IntegrationToken, str, str]:
    """A new token and its signing secret; both are shown once and only the token's hash is stored."""
    user = site_user(db)
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    secret = secrets.token_urlsafe(32)
    row = IntegrationToken(name=name, user_id=user.id, token_hash=digest(token),
                           secret_encrypted=cipher().encrypt(secret.encode()).decode())
    db.add(row)
    db.flush()
    return row, token, secret
