"""Business remark of 13 October: a leader could not record the client's acceptance in a colleague's request."""

from app.bootstrap import ROLE_GRANTS
from app.core.models import PermissionGrant, Role, User, UserRole
from app.core.security import password_hasher
from tests.support.business import cmd, issue, structured
from tests.support.crm import PASSWORD, login


def colleague(crm, without=()):
    """An employee with the standard leader and sales roles, as the owners of the business work."""
    with crm["sessions"].begin() as db:
        user = User(email="leader@example.com", name="Тимур", password_hash=password_hasher.hash(PASSWORD))
        db.add(user)
        db.flush()
        for title in ("Руководитель", "Менеджер продаж"):
            codes, scope = ROLE_GRANTS[title]
            role = Role(name=title)
            db.add(role)
            db.flush()
            db.add_all(PermissionGrant(role_id=role.id, code=code, scope=scope)
                       for code in codes.split() if (title, code) not in without)
            db.add(UserRole(user_id=user.id, role_id=role.id))
    login(crm, "leader@example.com")


def proposal_of_a_colleague(crm):
    req, data, _, _ = structured(crm, mode="RUSSIA", owner=crm["manager"].id)
    calculation = cmd(crm, f"/requests/{req['id']}/calculations", data)
    return calculation, issue(crm, req, calculation)


def acceptance(calculation, proposal):
    return {"version": proposal["version"], "reason": "Пришла оплата",
            "lines": [{"line_id": calculation["snapshot"]["lines"][0]["line_id"], "quantity": "1"}]}


def test_a_leader_records_the_acceptance_in_a_colleagues_request(crm):
    calculation, proposal = proposal_of_a_colleague(crm)
    colleague(crm)
    accepted = cmd(crm, f"/proposals/{proposal['id']}/accept", acceptance(calculation, proposal))
    assert accepted["proposal"]["status"] == "accepted"


def test_a_right_limited_to_own_requests_is_named_instead_of_not_found(crm):
    calculation, proposal = proposal_of_a_colleague(crm)
    # The leader role as installed before 13 October: documents only in own requests.
    colleague(crm, without={("Руководитель", "documents.write")})
    refused = cmd(crm, f"/proposals/{proposal['id']}/accept", acceptance(calculation, proposal), status=403)
    assert refused["code"] == "REQUEST_NOT_SHARED"
    assert "«Выпуск документов»" in refused["message"] and "Менеджер теста" in refused["message"]
