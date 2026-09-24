"""Explicit local account provisioning; never runs at application startup."""
import argparse
import getpass
import secrets
from pathlib import Path

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.models import PermissionGrant, Role, User, UserRole
from app.core.security import PERMISSIONS, password_hasher
from app.core.service import audit

ROLE_GRANTS = {
    'Администратор': ('admin.users admin.settings settings.dictionaries.write settings.system.write settings.commerce.write audit.read clients.read catalog.read chats.use files.upload tasks.read tasks.write', 'all'),
    'Руководитель': ('requests.read requests.write requests.assign clients.read clients.write tasks.read tasks.write calls.write catalog.read approvals.submit approvals.decide waves.write analytics.read exports.download chats.use files.upload', 'all'),
    'Владелец расчётов': ('requests.read clients.read catalog.read quotes.write finance.purchase.read finance.calculations.read finance.reward.read finance.profit.read calculations.write profiles.write templates.write exports.download chats.use files.upload', 'all'),
    'Менеджер продаж': ('requests.read requests.write clients.read clients.write calls.write tasks.read tasks.write catalog.read documents.write payments.write approvals.submit exports.download imports.write analytics.read chats.use files.upload', 'own'),
    'Закупщик': ('requests.read clients.read clients.write catalog.read catalog.write quotes.write finance.purchase.read exports.download tasks.read tasks.write chats.use files.upload', 'all'),
    'Логист': ('requests.read clients.read catalog.read waves.write tasks.read tasks.write chats.use files.upload', 'all'),
    'Финансовый контролёр': ('requests.read clients.read payments.write payments.confirm analytics.read exports.download tasks.read tasks.write chats.use files.upload', 'all'),
}


def provision(email: str, name: str, password: str, all_roles: bool = False) -> bool:
    email = str(TypeAdapter(EmailStr).validate_python(email))
    if len(password) < 12:
        raise ValueError('Пароль должен содержать не менее 12 символов')
    with SessionLocal.begin() as db:
        if db.scalar(select(User.id).where(User.email == email.lower())):
            return False
        role_ids = []
        for title, (codes, scope) in ROLE_GRANTS.items():
            role = db.scalar(select(Role).where(Role.name == title))
            if not role:
                role = Role(name=title)
                db.add(role)
                db.flush()
                for code in codes.split():
                    if code not in PERMISSIONS:
                        raise ValueError(f'Unknown permission {code}')
                    db.add(PermissionGrant(role_id=role.id, code=code, scope=scope))
            if all_roles or title == 'Администратор':
                role_ids.append(role.id)
        user = User(email=email.lower(), name=name, password_hash=password_hasher.hash(password))
        db.add(user)
        db.flush()
        for role_id in role_ids:
            db.add(UserRole(user_id=user.id, role_id=role_id))
        audit(db, user, 'user', user.id, 'bootstrap', after={'email': user.email, 'roles': role_ids}, reason='Явное создание владельцем локального проекта')
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description='Создать первого приглашённого пользователя CRM')
    parser.add_argument('--email', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--all-roles', action='store_true', help='Явно совместить семь ролей для локальной проверки')
    parser.add_argument('--credentials-file', type=Path, help='Создать случайный пароль и сохранить доступ в приватном файле')
    args = parser.parse_args()
    if args.credentials_file and args.credentials_file.exists():
        raise SystemExit('Файл доступа уже существует; он не перезаписан')
    password = secrets.token_urlsafe(20) if args.credentials_file else getpass.getpass('Начальный пароль: ')
    created = provision(args.email, args.name, password, args.all_roles)
    if not created:
        raise SystemExit('Пользователь уже существует; пароль и права не изменены')
    if args.credentials_file:
        args.credentials_file.parent.mkdir(parents=True, exist_ok=True)
        args.credentials_file.write_text(f'Локальная CRM: http://127.0.0.1:5173\nПочта: {args.email}\nПароль: {password}\n\nУчётная запись создана явно для владельца проекта. Для локальной проверки назначены: {"семь функциональных ролей" if args.all_roles else "Администратор"}.\n')
        args.credentials_file.chmod(0o600)
    print('Учётная запись создана. Пароль не выводится в журнал.')


if __name__ == '__main__':
    main()
