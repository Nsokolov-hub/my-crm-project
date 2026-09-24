"""The handover demo creates distinct role accounts without changing existing data."""

import re
import stat

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import demo_accounts
from app.core.db import Base
from app.core.models import AuditEvent, PermissionGrant, Role, User, UserRole
from app.core.security import password_hasher, scope_for


@pytest.fixture
def demo_db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        User.__table__, Role.__table__, UserRole.__table__,
        PermissionGrant.__table__, AuditEvent.__table__,
    ])
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(demo_accounts, "SessionLocal", sessions)
    yield sessions
    engine.dispose()


def test_clean_demo_has_one_distinct_role_per_account_and_private_random_passwords(demo_db, tmp_path):
    output = tmp_path / "demo-credentials.txt"
    assert demo_accounts.create_demo_accounts(output) == 7
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    content = output.read_text()
    passwords = re.findall(r"^Пароль: (.+)$", content, flags=re.MULTILINE)
    assert len(passwords) == len(set(passwords)) == 7

    with demo_db() as db:
        users = db.scalars(select(User).order_by(User.email)).all()
        assert len(users) == 7
        assert db.scalar(select(func.count()).select_from(UserRole)) == 7
        for name, slug in demo_accounts.DEMO_ROLES:
            user = next(user for user in users if user.email == f"demo-{slug}@example.com")
            role_name = db.scalar(
                select(Role.name).join(UserRole, Role.id == UserRole.role_id)
                .where(UserRole.user_id == user.id)
            )
            assert role_name == name
            password = re.search(
                rf"{re.escape(name)}\nЛогин: {re.escape(user.email)}\nПароль: (.+)", content
            )
            assert password is not None
            assert password_hasher.verify(user.password_hash, password.group(1))
            assert scope_for(db, user, "admin.users") == ("all" if name == "Администратор" else None)


def test_repeat_or_existing_user_does_not_change_database_or_credentials(demo_db, tmp_path):
    output = tmp_path / "demo-credentials.txt"
    demo_accounts.create_demo_accounts(output)
    original = output.read_bytes()
    with pytest.raises(ValueError, match="уже есть сотрудники"):
        demo_accounts.create_demo_accounts(output)
    assert output.read_bytes() == original
    with demo_db() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 7
        assert db.scalar(select(func.count()).select_from(UserRole)) == 7

    other = tmp_path / "other-credentials.txt"
    with pytest.raises(ValueError, match="уже есть сотрудники"):
        demo_accounts.create_demo_accounts(other)
    assert not other.exists()
