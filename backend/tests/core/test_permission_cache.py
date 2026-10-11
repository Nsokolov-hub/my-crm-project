"""Permission scopes are read once per request and stay correct when grants change mid-request."""

from sqlalchemy import delete, update

from app.core.models import PermissionGrant, Role, User, UserRole
from app.core.security import can, scope_for, users_with_permission
from tests.support.crm import login
from tests.support.queries import statements_while


def test_all_scopes_of_a_user_are_loaded_with_one_query(crm):
    login(crm, "manager@example.com")
    seen = statements_while(crm, lambda: crm["client"].get("/api/v1/auth/me"))
    grant_reads = [s for s in seen if "FROM permission_grants" in s]
    # One read for the effective scopes of all 36 codes and one for the user's own grant list.
    assert len(grant_reads) == 2


def test_grant_changes_in_the_same_session_are_visible_immediately(crm):
    with crm["sessions"]() as db:
        user = db.get(User, crm["manager"].id)
        assert scope_for(db, user, "requests.read") == "own"
        assert not can(db, user, "analytics.read")

        db.add(PermissionGrant(user_id=user.id, code="analytics.read", scope="all"))
        db.flush()
        assert can(db, user, "analytics.read")

        db.execute(update(PermissionGrant).where(PermissionGrant.user_id == user.id,
                                                 PermissionGrant.code == "requests.read").values(scope="all"))
        assert scope_for(db, user, "requests.read") == "all"

        db.execute(delete(PermissionGrant).where(PermissionGrant.user_id == user.id,
                                                 PermissionGrant.code == "analytics.read"))
        assert not can(db, user, "analytics.read")
        db.rollback()


def test_role_grants_count_and_a_personal_deny_overrides_them(crm):
    with crm["sessions"]() as db:
        user = db.get(User, crm["manager"].id)
        role = Role(name="Руководитель теста")
        db.add(role)
        db.flush()
        db.add(PermissionGrant(role_id=role.id, code="approvals.decide", scope="all"))
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.flush()
        assert can(db, user, "approvals.decide")
        assert user.id in {row.id for row in users_with_permission(db, "approvals.decide")}

        db.add(PermissionGrant(user_id=user.id, code="approvals.decide", scope="own", allow=False))
        db.flush()
        assert not can(db, user, "approvals.decide")
        assert user.id not in {row.id for row in users_with_permission(db, "approvals.decide")}
        db.rollback()


def test_users_with_permission_skips_inactive_people(crm):
    with crm["sessions"]() as db:
        holders = {row.id for row in users_with_permission(db, "admin.users")}
        assert crm["admin"].id in holders and crm["manager"].id not in holders
        db.get(User, crm["admin"].id).active = False
        db.flush()
        assert crm["admin"].id not in {row.id for row in users_with_permission(db, "admin.users")}
        db.rollback()
