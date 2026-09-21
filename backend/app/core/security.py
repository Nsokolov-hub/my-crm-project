import hashlib
import secrets
from datetime import timezone
from typing import Any

from argon2 import PasswordHasher
from cryptography.fernet import Fernet
from fastapi import Depends, Request
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db, utcnow
from app.core.errors import DomainError
from app.core.models import AuthSession, PermissionGrant, User, UserRole

password_hasher = PasswordHasher()

PERMISSIONS = {
    'requests.read': 'Просмотр заявок', 'requests.write': 'Изменение заявок', 'requests.assign': 'Переназначение заявок',
    'clients.read': 'Просмотр контрагентов', 'clients.write': 'Изменение контрагентов', 'calls.write': 'Обзвон',
    'tasks.read': 'Просмотр задач', 'tasks.write': 'Изменение задач', 'catalog.read': 'Просмотр каталога', 'catalog.write': 'Изменение каталога',
    'quotes.write': 'Работа с квотами', 'finance.purchase.read': 'Просмотр закупочных цен',
    'finance.calculations.read': 'Детальный расчёт', 'finance.reward.read': 'Вознаграждения', 'finance.profit.read': 'Плановая доходность',
    'calculations.write': 'Запись расчёта', 'profiles.write': 'Утверждение финансовых профилей',
    'documents.write': 'Выпуск документов', 'templates.write': 'Настройка шаблонов',
    'payments.write': 'Заявление об оплате', 'payments.confirm': 'Подтверждение и распределение оплат',
    'approvals.submit': 'Передача на согласование', 'approvals.decide': 'Решения руководителя', 'waves.write': 'Изменение волн',
    'exports.download': 'Экспорт и скачивание', 'imports.write': 'Импорт клиентской базы', 'analytics.read': 'Аналитика',
    'chats.use': 'Внутреннее общение', 'files.upload': 'Загрузка вложений',
    'admin.users': 'Управление пользователями и ролями', 'admin.settings': 'Настройки системы', 'audit.read': 'Просмотр аудита',
}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def cipher() -> Fernet:
    import base64
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest()))


def scope_for(db: Session, user: User, code: str) -> str | None:
    role_ids = select(UserRole.role_id).where(UserRole.user_id == user.id)
    grants = db.scalars(select(PermissionGrant).where(PermissionGrant.code == code, or_(PermissionGrant.user_id == user.id, PermissionGrant.role_id.in_(role_ids)))).all()
    if any(g.user_id == user.id and not g.allow for g in grants):
        return None
    scopes = {g.scope for g in grants if g.allow}
    return next((s for s in ('all', 'shared', 'own') if s in scopes), None)


def can(db: Session, user: User, code: str) -> bool:
    return scope_for(db, user, code) is not None


def require_permission(db: Session, user: User, code: str, request_id: str | None = None) -> None:
    if not can(db, user, code):
        raise DomainError('FORBIDDEN', 'Недостаточно прав для этого действия', 403)
    if request_id:
        check_request(db, user, request_id, code)


def request_predicate(db: Session, user: User, permission: str = 'requests.read') -> Any:
    from app.crm.models import Request as CRMRequest
    from app.crm.models import RequestMember
    scope = scope_for(db, user, permission)
    if scope == 'all':
        return True
    if scope in ('own', 'shared'):
        # The own scope includes explicitly shared objects, as specified for sales managers.
        return or_(CRMRequest.owner_id == user.id, CRMRequest.id.in_(select(RequestMember.request_id).where(RequestMember.user_id == user.id)))
    return False


def check_request(db: Session, user: User, entity_id: str, permission: str = 'requests.read') -> Any:
    from app.crm.models import Request as CRMRequest
    row = db.scalar(select(CRMRequest).where(CRMRequest.id == entity_id, request_predicate(db, user, permission)))
    if row is None:
        raise DomainError('NOT_FOUND', 'Объект не найден или недоступен', 404)
    return row

def has_request_permission(db: Session, user: User, entity_id: str, permission: str) -> bool:
    from app.crm.models import Request as CRMRequest
    return db.scalar(select(CRMRequest).where(CRMRequest.id == entity_id, request_predicate(db, user, permission))) is not None


def client_predicate(db: Session, user: User, permission: str = 'clients.read') -> Any:
    from app.crm.models import Counterparty
    from app.crm.models import Request as CRMRequest
    scope = scope_for(db, user, permission)
    if scope == 'all':
        return True
    if scope in ('own', 'shared'):
        return or_(Counterparty.owner_id == user.id, Counterparty.id.in_(select(CRMRequest.client_id).where(request_predicate(db, user))))
    return False


def check_client(db: Session, user: User, entity_id: str, permission: str = 'clients.read') -> Any:
    from app.crm.models import Counterparty
    row = db.scalar(select(Counterparty).where(Counterparty.id == entity_id, client_predicate(db, user, permission)))
    if row is None:
        raise DomainError('NOT_FOUND', 'Объект не найден или недоступен', 404)
    return row

def wave_predicate(db: Session, user: User, permission: str = 'requests.read') -> Any:
    from app.commerce.models import Wave
    scope = scope_for(db, user, permission)
    if scope == 'all':
        return True
    if scope in ('own', 'shared'):
        return Wave.owner_id == user.id
    return False

def task_predicate(db: Session, user: User) -> Any:
    from app.crm.models import Task, Request as CRMRequest, Counterparty
    from app.commerce.models import Wave
    
    scope = scope_for(db, user, 'tasks.read')
    if not scope:
        return False
        
    base = True if scope == 'all' else or_(Task.assignee_id == user.id, Task.author_id == user.id)
    
    req_pred = request_predicate(db, user)
    cli_pred = client_predicate(db, user)
    wav_pred = wave_predicate(db, user)
    
    return and_(
        base,
        or_(
            Task.entity_type.is_(None),
            and_(Task.entity_type == 'request', Task.entity_id.in_(select(CRMRequest.id).where(req_pred))),
            and_(Task.entity_type == 'counterparty', Task.entity_id.in_(select(Counterparty.id).where(cli_pred))),
            and_(Task.entity_type == 'wave', Task.entity_id.in_(select(Wave.id).where(wav_pred))),
            Task.entity_type.not_in(['request', 'counterparty', 'wave'])
        )
    )

def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get('crm_session')
    if not token:
        raise DomainError('UNAUTHENTICATED', 'Войдите в систему', 401)
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == digest(token), AuthSession.revoked.is_(False)))
    if session is None or session.expires_at.replace(tzinfo=timezone.utc) <= utcnow():
        raise DomainError('SESSION_EXPIRED', 'Сеанс завершён. Войдите снова.', 401)
    user = db.get(User, session.user_id)
    if user is None or not user.active:
        raise DomainError('SESSION_REVOKED', 'Доступ к системе отозван', 401)
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        csrf = request.headers.get('X-CSRF-Token', '')
        if not secrets.compare_digest(session.csrf_token, csrf):
            raise DomainError('CSRF_INVALID', 'Обновите страницу и повторите действие', 403)
        origin = request.headers.get('origin')
        if origin and origin not in settings.allowed_origins.split(','):
            raise DomainError('ORIGIN_FORBIDDEN', 'Источник запроса не разрешён', 403)
    request.state.auth_session = session
    privileged = any(can(db, user, p) for p in ('admin.users', 'profiles.write', 'payments.confirm'))
    if settings.require_mfa and privileged and not user.mfa_enabled and not request.url.path.startswith('/api/v1/auth/'):
        raise DomainError('MFA_REQUIRED', 'Настройте двухфакторную защиту в профиле', 403)
    return user
