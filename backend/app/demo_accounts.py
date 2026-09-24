"""Create one disposable account per built-in role on an empty demo database.

This module is never called by application startup. Credentials are generated anew
and saved to a private file outside the distributable project archive.
"""

import argparse
import os
import secrets
from pathlib import Path

from sqlalchemy import select

from app.bootstrap import ROLE_GRANTS
from app.core.db import SessionLocal
from app.core.models import AuditEvent, PermissionGrant, Role, User, UserRole
from app.core.security import PERMISSIONS, password_hasher

DEMO_ROLES = (
    ("Администратор", "administrator"),
    ("Руководитель", "director"),
    ("Владелец расчётов", "calculator"),
    ("Менеджер продаж", "sales"),
    ("Закупщик", "purchasing"),
    ("Логист", "logistics"),
    ("Финансовый контролёр", "finance"),
)


def create_demo_accounts(credentials_file: Path, url: str = "http://127.0.0.1:8080/") -> int:
    """Create seven role accounts only on an empty database; never alter existing users."""
    credentials = [
        (name, f"demo-{slug}@example.com", secrets.token_urlsafe(24))
        for name, slug in DEMO_ROLES
    ]
    file_created = False
    try:
        with SessionLocal.begin() as db:
            if db.scalar(select(User.id).limit(1)) is not None:
                raise ValueError("В базе уже есть сотрудники. Создавайте демо-аккаунты только на чистой базе.")
            if db.scalar(select(Role.id).limit(1)) is not None:
                raise ValueError("В базе уже есть роли. Используйте отдельную чистую демо-базу.")

            for name, email, password in credentials:
                codes, scope = ROLE_GRANTS[name]
                role = Role(name=name)
                db.add(role)
                db.flush()
                for code in codes.split():
                    if code not in PERMISSIONS:
                        raise ValueError(f"Неизвестное право в стандартной роли: {code}")
                    db.add(PermissionGrant(role_id=role.id, code=code, scope=scope))

                user = User(email=email, name=f"Демо · {name}", password_hash=password_hasher.hash(password))
                db.add(user)
                db.flush()
                db.add(UserRole(user_id=user.id, role_id=role.id))
                db.add(AuditEvent(
                    actor_id=user.id,
                    entity_type="user",
                    entity_id=user.id,
                    action="demo_bootstrap",
                    after={"email": email, "role": name},
                    reason="Явное создание учебных аккаунтов на чистой базе",
                    request_id="demo-bootstrap",
                ))

            credentials_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(credentials_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            file_created = True
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(f"Демо CRM: {url}\n")
                stream.write("Только для тестового стенда. У каждого сотрудника одна роль.\n\n")
                for name, email, password in credentials:
                    stream.write(f"{name}\nЛогин: {email}\nПароль: {password}\n\n")
                stream.flush()
                os.fsync(stream.fileno())
    except Exception:
        if file_created:
            credentials_file.unlink(missing_ok=True)
        raise
    return len(credentials)


def main() -> None:
    parser = argparse.ArgumentParser(description="Создать семь демонстрационных аккаунтов на чистой базе")
    parser.add_argument("--credentials-file", required=True, type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8080/")
    args = parser.parse_args()
    try:
        count = create_demo_accounts(args.credentials_file, args.url)
    except (ValueError, FileExistsError) as error:
        parser.exit(1, f"Аккаунты не созданы: {error}\n")
    print(f"Создано {count} отдельных демо-аккаунтов. Пароли сохранены в приватном файле.")


if __name__ == "__main__":
    main()
