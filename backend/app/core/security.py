import hashlib
import secrets
from typing import Any

from argon2 import PasswordHasher
from cryptography.fernet import Fernet
from fastapi import Depends, Request
from sqlalchemy import and_, event, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import as_utc, get_db, utcnow
from app.core.errors import DomainError
from app.core.models import AuthSession, PermissionGrant, User, UserRole

password_hasher = PasswordHasher()
# Holders of these rights must enroll a second factor when MFA is required (production).
MFA_PRIVILEGED_PERMISSIONS = ('admin.users', 'profiles.write', 'payments.confirm')

PERMISSIONS = {
    'procurement.write': 'Заказы поставщикам',
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
    'settings.dictionaries.write': 'Настройка рабочих справочников',
    'settings.system.write': 'Настройка системных правил',
    'settings.commerce.write': 'Настройка коммерческих правил',
}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def cipher() -> Fernet:
    import base64
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest()))


SCOPE_ORDER = ('all', 'shared', 'own')
_SCOPES_CACHE = 'crm.permission_scopes'


def effective_scopes(db: Session, user_id: str) -> dict[str, str]:
    """Best allowed scope per permission code: one query per user, cached for the session (request).

    A user-level deny wins over every allow; role-level denies are not part of the model.
    """
    cache = db.info.setdefault(_SCOPES_CACHE, {})
    if user_id not in cache:
        role_ids = select(UserRole.role_id).where(UserRole.user_id == user_id)
        rows = db.execute(select(PermissionGrant.code, PermissionGrant.scope, PermissionGrant.allow,
                                 PermissionGrant.user_id)
                          .where(or_(PermissionGrant.user_id == user_id, PermissionGrant.role_id.in_(role_ids)))).all()
        denied = {code for code, _, allow, owner in rows if owner == user_id and not allow}
        granted: dict[str, set[str]] = {}
        for code, scope, allow, _ in rows:
            if allow and code not in denied:
                granted.setdefault(code, set()).add(scope)
        cache[user_id] = {code: best for code, scopes in granted.items()
                          if (best := next((s for s in SCOPE_ORDER if s in scopes), None))}
    return cache[user_id]


def invalidate_permissions(db: Session) -> None:
    db.info.pop(_SCOPES_CACHE, None)


@event.listens_for(Session, 'after_flush')
def _grants_flushed(session: Session, _context: Any) -> None:
    if any(isinstance(row, (PermissionGrant, UserRole)) for row in (*session.new, *session.dirty, *session.deleted)):
        invalidate_permissions(session)


@event.listens_for(Session, 'do_orm_execute')
def _grants_bulk_changed(state: Any) -> None:
    # Bulk UPDATE/DELETE statements bypass the unit of work and therefore after_flush.
    if (state.is_update or state.is_delete) and state.bind_mapper is not None \
            and state.bind_mapper.class_ in (PermissionGrant, UserRole):
        invalidate_permissions(state.session)


def scope_for(db: Session, user: User, code: str) -> str | None:
    return effective_scopes(db, user.id).get(code)


def users_with_permission(db: Session, code: str) -> list[User]:
    """Active users that hold `code` in any scope, in one query instead of a check per user."""
    # Explicit correlation: a doubly nested subquery would otherwise add its own `users` FROM
    # and match the roles of every employee.
    member_roles = select(UserRole.role_id).where(UserRole.user_id == User.id).correlate(User)
    allowed = select(PermissionGrant.id).where(
        PermissionGrant.code == code, PermissionGrant.allow.is_(True), PermissionGrant.scope.in_(SCOPE_ORDER),
        or_(PermissionGrant.user_id == User.id, PermissionGrant.role_id.in_(member_roles)),
    )
    denied = select(PermissionGrant.id).where(
        PermissionGrant.code == code, PermissionGrant.user_id == User.id, PermissionGrant.allow.is_(False))
    return list(db.scalars(select(User).where(User.active.is_(True), allowed.exists(), ~denied.exists())
                           .order_by(User.name, User.id)))


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
    if user.own_requests_only:
        return CRMRequest.owner_id == user.id if scope else False
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
    from app.commerce.models import Wave
    from app.crm.models import Counterparty, Task
    from app.crm.models import Request as CRMRequest
    
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

def current_user(request: Request, db: Session = Depends(get_db, scope="function")) -> User:
    token = request.cookies.get('crm_session')
    if not token:
        raise DomainError('UNAUTHENTICATED', 'Войдите в систему', 401)
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == digest(token), AuthSession.revoked.is_(False)))
    if session is None or as_utc(session.expires_at) <= utcnow():
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
    # Permission lookups run only when MFA is actually enforced for this user and path.
    if (settings.require_mfa and not user.mfa_enabled and not request.url.path.startswith('/api/v1/auth/')
            and any(can(db, user, p) for p in MFA_PRIVILEGED_PERMISSIONS)):
        raise DomainError('MFA_REQUIRED', 'Настройте двухфакторную защиту в профиле', 403)
    return user
