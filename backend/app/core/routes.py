import secrets
import time
from datetime import timedelta
from typing import Any, Literal

import pyotp
from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db, utcnow
from app.core.errors import DomainError
from app.core.models import (
    AppSetting,
    AuditEvent,
    AuthSession,
    LoginAttempt,
    PermissionGrant,
    Role,
    User,
    UserRole,
)
from app.core.security import (
    PERMISSIONS,
    cipher,
    current_user,
    digest,
    password_hasher,
    require_permission,
    scope_for,
)
from app.core.service import advisory, audit, check_version, lock, serialize
from app.core.service import page as paginate

router = APIRouter(tags=['Доступ и настройки'])


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class LoginInput(Input):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)
    otp: str | None = None


class OTPInput(Input):
    otp: str = Field(pattern=r'^\d{6}$')


class PasswordInput(Input):
    current_password: str
    new_password: str = Field(min_length=12, max_length=200)


def user_view(db: Session, user: User) -> dict[str, Any]:
    roles = db.scalars(select(Role).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user.id)).all()
    return {'id': user.id, 'email': user.email, 'name': user.name, 'active': user.active, 'version': user.version, 'mfa_enabled': user.mfa_enabled, 'roles': [{'id': r.id, 'name': r.name} for r in roles], 'permissions': {code: scope for code in PERMISSIONS if (scope := scope_for(db, user, code))}}


@router.post('/auth/login')
def login(body: LoginInput, request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    email = str(body.email).lower()
    ip = request.client.host if request.client else 'unknown'
    advisory(db, f'login:{email}')
    since = utcnow() - timedelta(minutes=15)
    failures = db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.created_at >= since, LoginAttempt.successful.is_(False), or_(LoginAttempt.identity == email, LoginAttempt.ip == ip))) or 0
    if failures >= 10:
        raise DomainError('LOGIN_RATE_LIMIT', 'Слишком много попыток входа. Повторите через 15 минут.', 429)
    user = db.scalar(select(User).where(User.email == email).with_for_update())
    valid = False
    try:
        if user:
            valid = password_hasher.verify(user.password_hash, body.password) and user.active
        else:
            password_hasher.verify(password_hasher.hash('nonexistent-account-padding'), body.password)
    except VerificationError:
        pass
    if valid and user and user.mfa_enabled:
        totp = pyotp.TOTP(cipher().decrypt(user.mfa_secret.encode()).decode()) if user.mfa_secret else None
        step = int(time.time()) // 30
        valid = bool(totp and body.otp and totp.verify(body.otp, valid_window=0) and step > user.mfa_last_step)
        if valid:
            user.mfa_last_step = step
    db.add(LoginAttempt(identity=email, ip=ip, successful=valid))
    if not valid or user is None:
        db.commit()  # Failed attempts must survive the rejected authentication transaction.
        raise DomainError('LOGIN_INVALID', 'Неверные данные входа или код второго фактора', 401)
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    db.add(AuthSession(user_id=user.id, token_hash=digest(token), csrf_token=csrf, expires_at=utcnow() + timedelta(hours=settings.session_hours)))
    audit(db, user, 'user', user.id, 'login')
    response.set_cookie('crm_session', token, httponly=True, secure=settings.environment == 'production', samesite='strict', max_age=settings.session_hours * 3600, path='/')
    return {'user': user_view(db, user), 'csrf_token': csrf, 'permissions': user_view(db, user)['permissions']}


@router.get('/auth/me')
def me(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    data = user_view(db, user)
    return {'user': data, 'csrf_token': request.state.auth_session.csrf_token, 'permissions': data['permissions']}


@router.post('/auth/logout')
def logout(request: Request, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, bool]:
    request.state.auth_session.revoked = True
    response.delete_cookie('crm_session', path='/')
    audit(db, user, 'user', user.id, 'logout')
    return {'ok': True}


@router.post('/auth/mfa/setup')
def mfa_setup(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, str]:
    user = lock(db, User, user.id)
    if user.mfa_enabled:
        raise DomainError('MFA_ALREADY_ENABLED', 'Второй фактор уже настроен', 409)
    secret = pyotp.random_base32()
    user.mfa_secret = cipher().encrypt(secret.encode()).decode()
    audit(db, user, 'user', user.id, 'mfa_setup')
    return {'secret': secret, 'uri': pyotp.TOTP(secret).provisioning_uri(user.email, issuer_name='Реактив CRM')}


@router.post('/auth/mfa/enable')
def mfa_enable(body: OTPInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, bool]:
    user = lock(db, User, user.id)
    if not user.mfa_secret or not pyotp.TOTP(cipher().decrypt(user.mfa_secret.encode()).decode()).verify(body.otp):
        raise DomainError('OTP_INVALID', 'Неверный код. Проверьте время в приложении аутентификации.', 422, 'otp')
    user.mfa_enabled = True
    audit(db, user, 'user', user.id, 'mfa_enabled')
    return {'ok': True}


@router.post('/auth/password')
def change_password(body: PasswordInput, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, bool]:
    try:
        password_hasher.verify(user.password_hash, body.current_password)
    except VerificationError:
        raise DomainError('PASSWORD_INVALID', 'Текущий пароль неверен', 422, 'current_password') from None
    user.password_hash = password_hasher.hash(body.new_password)
    db.execute(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.id != request.state.auth_session.id).values(revoked=True))
    audit(db, user, 'user', user.id, 'password_changed')
    return {'ok': True}


class GrantInput(Input):
    code: str
    scope: Literal['own', 'shared', 'all'] = 'own'
    allow: bool = True


class RoleInput(Input):
    name: str = Field(min_length=1, max_length=200)
    grants: list[GrantInput]
    version: int | None = None


class UserInput(Input):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=200)
    role_ids: list[str] = []


class UserPatch(Input):
    version: int
    name: str | None = Field(default=None, min_length=1, max_length=200)
    active: bool | None = None
    role_ids: list[str] | None = None
    grants: list[GrantInput] | None = None
    reassign_to: str | None = None
    reason: str = Field(min_length=1, max_length=2000)


@router.get('/users')
def directory(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(User).where(User.active.is_(True)).order_by(User.name)).all()
    return {'items': [{'id': r.id, 'name': r.name} for r in rows], 'total': len(rows), 'page': 1, 'page_size': len(rows)}


@router.get('/admin/permissions')
def permissions(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'admin.users')
    return {'items': [{'code': k, 'name': v} for k, v in PERMISSIONS.items()]}


@router.get('/admin/users')
def users(page: int = 1, page_size: int = 25, q: str = '', user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'admin.users')
    stmt = select(User).where(or_(User.name.ilike(f'%{q}%'), User.email.ilike(f'%{q}%')))
    result = paginate(db, stmt.order_by(User.name), page, page_size)
    result['items'] = [user_view(db, db.get(User, row['id'])) for row in result['items']]
    return result


def assign_roles(db: Session, target: User, role_ids: list[str]) -> None:
    if len(set(role_ids)) != len(role_ids) or any(db.get(Role, i) is None for i in role_ids):
        raise DomainError('ROLE_INVALID', 'Укажите существующие роли без повторений', 422, 'role_ids')
    db.execute(delete(UserRole).where(UserRole.user_id == target.id))
    for role_id in role_ids:
        db.add(UserRole(user_id=target.id, role_id=role_id))
    db.flush()


def assign_grants(db: Session, grants: list[GrantInput], *, role_id: str | None = None, user_id: str | None = None) -> None:
    if any(g.code not in PERMISSIONS for g in grants) or len({g.code for g in grants}) != len(grants):
        raise DomainError('PERMISSION_INVALID', 'Неизвестное или повторное разрешение', 422, 'grants')
    db.execute(delete(PermissionGrant).where(PermissionGrant.role_id == role_id if role_id else PermissionGrant.user_id == user_id))
    for g in grants:
        db.add(PermissionGrant(role_id=role_id, user_id=user_id, **g.model_dump()))
    db.flush()


@router.post('/admin/users', status_code=201)
def create_user(body: UserInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'admin.users')
    row = User(email=str(body.email).lower(), name=body.name, password_hash=password_hasher.hash(body.password))
    db.add(row)
    db.flush()
    assign_roles(db, row, body.role_ids)
    audit(db, user, 'user', row.id, 'created', after=user_view(db, row))
    return user_view(db, row)


@router.patch('/admin/users/{entity_id}')
def edit_user(entity_id: str, body: UserPatch, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.crm.models import Counterparty, Task
    from app.crm.models import Request as CRMRequest
    require_permission(db, user, 'admin.users')
    row = lock(db, User, entity_id)
    check_version(row, body.version)
    before = user_view(db, row)
    if body.active is False:
        if row.id == user.id:
            raise DomainError('SELF_BLOCK', 'Нельзя заблокировать собственную учётную запись', 422)
        target = db.get(User, body.reassign_to) if body.reassign_to else None
        pending = db.scalar(select(func.count()).select_from(Task).where(Task.assignee_id == row.id, Task.status.in_(['assigned', 'in_progress']))) or 0
        if pending and (not target or not target.active or target.id == row.id):
            raise DomainError('REASSIGN_REQUIRED', 'Выберите сотрудника для незавершённых задач', 422, 'reassign_to')
        if target:
            for model, col in [(Task, Task.assignee_id), (CRMRequest, CRMRequest.owner_id), (Counterparty, Counterparty.owner_id)]:
                records = db.scalars(select(model).where(col == row.id).with_for_update()).all()
                for record in records:
                    if isinstance(record, Task) and record.status not in ('assigned', 'in_progress'):
                        continue
                    attr = 'assignee_id' if isinstance(record, Task) else 'owner_id'
                    setattr(record, attr, target.id)
                    record.version += 1
                    audit(db, user, model.__tablename__, record.id, 'reassigned', before={attr: row.id}, after={attr: target.id}, reason=body.reason)
        db.execute(update(AuthSession).where(AuthSession.user_id == row.id).values(revoked=True))
    if body.name is not None:
        row.name = body.name
    if body.active is not None:
        row.active = body.active
    if body.role_ids is not None:
        assign_roles(db, row, body.role_ids)
    if body.grants is not None:
        assign_grants(db, body.grants, user_id=row.id)
    row.version += 1
    audit(db, user, 'user', row.id, 'updated', before, user_view(db, row), body.reason)
    return user_view(db, row)


@router.get('/admin/roles')
def roles(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'admin.users')
    rows = db.scalars(select(Role).order_by(Role.name)).all()
    return {'items': [{**serialize(r), 'grants': [{'code': g.code, 'scope': g.scope, 'allow': g.allow} for g in db.scalars(select(PermissionGrant).where(PermissionGrant.role_id == r.id))]} for r in rows], 'total': len(rows), 'page': 1, 'page_size': len(rows)}


@router.post('/admin/roles')
def create_role(body: RoleInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'admin.users')
    row = Role(name=body.name)
    db.add(row)
    db.flush()
    assign_grants(db, body.grants, role_id=row.id)
    audit(db, user, 'role', row.id, 'created', after=body.model_dump())
    return serialize(row)


@router.put('/admin/roles/{entity_id}')
def edit_role(entity_id: str, body: RoleInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'admin.users')
    row = lock(db, Role, entity_id)
    check_version(row, body.version or 0)
    row.name = body.name
    assign_grants(db, body.grants, role_id=row.id)
    row.version += 1
    audit(db, user, 'role', row.id, 'updated', after=body.model_dump())
    return serialize(row)


class SettingInput(Input):
    key: str = Field(min_length=1, max_length=100)
    value: dict[str, Any]
    version: int | None = None


@router.get('/settings')
def list_settings(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'admin.settings')
    return paginate(db, select(AppSetting).order_by(AppSetting.key), 1, 100)


@router.post('/settings')
def set_setting(body: SettingInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'admin.settings')
    if any(x in body.key.lower() for x in ('secret', 'password', 'token', 'api_key')):
        raise DomainError('SECRET_SETTING_FORBIDDEN', 'Секреты задаются через защищённую конфигурацию сервера', 422, 'key')
    advisory(db, f'setting:{body.key}')
    row = db.scalar(select(AppSetting).where(AppSetting.key == body.key).with_for_update())
    before = serialize(row) if row else None
    if row:
        check_version(row, body.version or 0)
        row.value = body.value
        row.version += 1
        row.author_id = user.id
    else:
        row = AppSetting(key=body.key, value=body.value, author_id=user.id)
        db.add(row)
    db.flush()
    audit(db, user, 'setting', row.id, 'updated', before, serialize(row))
    return serialize(row)


@router.get('/admin/audit')
def list_audit(entity_id: str | None = None, page: int = 1, page_size: int = 25, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_permission(db, user, 'audit.read')
    stmt = select(AuditEvent)
    if entity_id:
        stmt = stmt.where(AuditEvent.entity_id == entity_id)
    result = paginate(db, stmt.order_by(AuditEvent.created_at.desc()), page, page_size)
    # Audit visibility never bypasses separate financial permissions.
    from app.core.security import can
    if not all(can(db, user, p) for p in ('finance.purchase.read', 'finance.calculations.read', 'finance.reward.read')):
        for row in result['items']:
            row.pop('before', None)
            row.pop('after', None)
    return result


from app.core.jobs import router as jobs_router  # noqa: E402

router.include_router(jobs_router)
