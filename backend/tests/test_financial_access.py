"""Financial responses must respect current rights without changing saved facts."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_commerce import command, get, prepare
from test_commerce import commerce as commerce

from app.commerce.models import Calculation, CalculationProfile, Payment, PaymentAllocation, PaymentReversal
from app.communication.models import FileRecord
from app.communication.routes import router as communication_router
from app.core.models import Notification, PermissionGrant
from app.crm.models import Request, RequestItem


def set_scope(env, code, scope):
    with env["sessions"]() as db:
        grant = db.scalar(select(PermissionGrant).where(
            PermissionGrant.user_id == env["owner_id"], PermissionGrant.code == code
        ))
        grant.scope = scope
        db.commit()


def make_foreign(env, request_id):
    with env["sessions"]() as db:
        db.get(Request, request_id).owner_id = env["other_id"]
        db.commit()


def sensitive_calculation(env):
    state = prepare(env)
    definition = deepcopy(state["profile"]["definition"])
    definition["constants"]["commission_factor"] = "0.12345"
    definition.update(reward_enabled=True, reward_label="Private bonus", reward_basis="Private policy")
    for formula in definition["formulas"]:
        if formula["name"] == "reward":
            formula["expression"] = "purchase * commission_factor"
    definition["formulas"].extend([
        {"name": "internal_copy", "expression": "reward"},
        {"name": "source_copy", "expression": "purchase"},
        {"name": "result_copy", "expression": "sale_net - cost"},
    ])
    profile = command(env, "/profiles", {
        "name": "Restricted finance", "effective_from": date.today().isoformat(),
        "reason": "Access test", "definition": definition,
    })
    command(env, f"/profiles/{profile['id']}/publish")
    payload = deepcopy(state["calculation_payload"])
    payload.update(profile_id=profile["id"], idempotency_key=str(uuid4()))
    payload["selections"][0]["variables"] = {"commission_factor": "0.23456"}
    saved = command(env, f"/requests/{env['request_id']}/calculations", payload)
    return saved, payload


@pytest.mark.parametrize("category", ["purchase", "reward", "profit", "calculations"])
def test_calculation_projection_respects_each_request_scope(commerce, category):
    env = commerce
    saved, _ = sensitive_calculation(env)
    with env["sessions"]() as db:
        original = deepcopy(db.get(Calculation, saved["id"]).snapshot)
    assert saved["snapshot"] == original
    set_scope(env, f"finance.{category}.read", "own")
    make_foreign(env, env["request_id"])
    row = next(row for row in get(env, f"/requests/{env['request_id']}/calculations")["items"]
               if row["id"] == saved["id"])
    snapshot = row["snapshot"]
    assert snapshot["totals"] == original["totals"]
    assert all(key not in snapshot for key in ("input", "expense_allocations", "rates"))
    if category == "calculations":
        assert "detail" not in snapshot["lines"][0]
        assert "profile" not in snapshot
    else:
        detail = snapshot["lines"][0]["detail"]
        assert not {"commission_factor", "internal_copy", "source_copy", "result_copy"} & detail.keys()
        assert not {"constants", "formulas"} & snapshot["profile"].keys()
        denied = {"purchase": {"purchase"}, "reward": {"reward", "manager_bonus"},
                  "profit": {"cost", "profit", "margin"}}[category]
        assert not denied & detail.keys()
        if category == "purchase":
            assert "quote" not in snapshot["lines"][0]
            assert "price" not in get(env, f"/requests/{env['request_id']}/quotes")["items"][0]
        else:
            assert detail["purchase"] == original["lines"][0]["detail"]["purchase"]
        if category == "reward":
            assert not {"reward_enabled", "reward_label", "reward_basis"} & snapshot["profile"].keys()
    with env["sessions"]() as db:
        assert db.get(Calculation, saved["id"]).snapshot == original
    set_scope(env, f"finance.{category}.read", "all")
    restored = next(row for row in get(env, f"/requests/{env['request_id']}/calculations")["items"]
                    if row["id"] == saved["id"])
    assert restored["snapshot"] == original


@pytest.mark.parametrize("category", ["reward", "profit"])
def test_preview_profile_and_calculation_replay_reproject_after_revocation(commerce, category):
    env = commerce
    saved, payload = sensitive_calculation(env)
    with env["sessions"]() as db:
        original_profile = deepcopy(db.get(CalculationProfile, saved["profile_id"]).definition)
        db.query(PermissionGrant).filter_by(user_id=env["owner_id"], code=f"finance.{category}.read").delete()
        db.commit()
    profile = next(row for row in get(env, "/profiles")["items"] if row["id"] == saved["profile_id"])
    assert not {"constants", "formulas"} & profile["definition"].keys()
    replay = command(env, f"/requests/{env['request_id']}/calculations", payload)
    preview = command(env, f"/requests/{env['request_id']}/calculations/preview", payload)
    assert replay["snapshot"] == preview["snapshot"]
    assert replay["id"] == saved["id"]
    assert "input" not in replay["snapshot"]
    assert "commission_factor" not in replay["snapshot"]["lines"][0]["detail"]
    assert not {"constants", "formulas"} & replay["snapshot"]["profile"].keys()
    with env["sessions"]() as db:
        assert db.get(CalculationProfile, saved["profile_id"]).definition == original_profile
        assert db.get(Calculation, saved["id"]).snapshot == saved["snapshot"]
        assert db.scalar(select(func.count()).select_from(Calculation).where(Calculation.id == saved["id"])) == 1


def cross_request_payment(env):
    with env["sessions"]() as db:
        request = Request(number="ACCESS-002", title="Target request", seller_id=env["seller_id"],
                          client_id=env["client_id"], owner_id=env["owner_id"], is_test=True)
        db.add(request)
        db.flush()
        item = RequestItem(request_id=request.id, description="Target item", cas="64-17-5",
                           quantity=Decimal("10"), unit="pcs", purity="99%", packaging="1 штука")
        db.add(item)
        db.commit()
        target = {**env, "request_id": request.id, "item_id": item.id}
    state = prepare(target, with_invoice=True)
    payment = command(env, f"/requests/{env['request_id']}/payments", {
        "amount": "4000", "currency": "RUB", "payment_date": date.today().isoformat(), "number": "ACCESS",
    })
    payment = command(env, f"/payments/{payment['id']}/confirm", {"version": 1})
    payload = {"idempotency_key": str(uuid4()), "version": payment["version"],
               "allocations": [{"invoice_id": state["invoice"]["id"], "amount": "100"}]}
    payment = command(env, f"/payments/{payment['id']}/allocate", payload)
    return target, state, payment, payload


@pytest.mark.parametrize("action", ["allocate", "reverse"])
def test_payment_replay_uses_current_visibility_without_duplicate_mutation(commerce, action):
    env = commerce
    target, _, payment, payload = cross_request_payment(env)
    if action == "reverse":
        payload = {"idempotency_key": str(uuid4()), "version": payment["version"], "amount": "25",
                   "allocation_id": payment["allocations"][0]["id"], "kind": "unallocate",
                   "reason": "Private target reason"}
        payment = command(env, f"/payments/{payment['id']}/reverse", payload)
    make_foreign(env, target["request_id"])
    for code in ("requests.read", "payments.confirm"):
        set_scope(env, code, "own")
    get(env, f"/requests/{target['request_id']}/invoices", expected=404)
    command(env, f"/payments/{payment['id']}/{action}", {
        **payload, "idempotency_key": str(uuid4()), "version": payment["version"],
    }, expected=404)
    replay = command(env, f"/payments/{payment['id']}/{action}", payload)
    assert replay == get(env, f"/requests/{env['request_id']}/payments")["items"][0]
    assert replay["allocations"] == [] and replay["reversals"] == []
    with env["sessions"]() as db:
        assert db.get(Payment, payment["id"]).version == payment["version"]
        assert db.scalar(select(func.count()).select_from(PaymentAllocation)) == 1
        assert db.scalar(select(func.count()).select_from(PaymentReversal)) == (action == "reverse")
    set_scope(env, "requests.read", "all")
    restored = command(env, f"/payments/{payment['id']}/{action}", payload)
    assert len(restored["allocations"]) == 1
    assert len(restored["reversals"]) == (action == "reverse")


def test_cross_request_reversal_notifies_each_request_owner(commerce):
    env = commerce
    target, _, payment, _ = cross_request_payment(env)
    make_foreign(env, target["request_id"])
    command(env, f"/payments/{payment['id']}/reverse", {
        "version": payment["version"],
        "amount": "25",
        "allocation_id": payment["allocations"][0]["id"],
        "kind": "unallocate",
        "reason": "Correct target request payment",
    })
    with env["sessions"]() as db:
        reversal_notifications = db.scalars(select(Notification).where(
            Notification.event_key.like("payment-reversed:%")
        )).all()
    assert {(row.entity_id, row.user_id) for row in reversal_notifications} == {
        (env["request_id"], env["owner_id"]),
        (target["request_id"], env["other_id"]),
    }


@pytest.mark.parametrize("entity_type, classification", [
    ("quote", "purchase"), ("calculation", "calculation"), ("document", "general"),
])
def test_attachment_export_scope_uses_owning_request(commerce, entity_type, classification):
    env = commerce
    env["client"].app.include_router(communication_router, prefix="/api/v1")
    state = prepare(env, with_invoice=True)
    entity_id = state[{"quote": "quote", "calculation": "calc", "document": "invoice"}[entity_type]]["id"]
    content = b"Synthetic private attachment"
    uploaded = env["client"].post("/api/v1/files", data={
        "entity_type": entity_type, "entity_id": entity_id, "classification": classification,
    }, files={"file": ("attachment.txt", content, "text/plain")},
        headers={"Idempotency-Key": str(uuid4())})
    assert uploaded.status_code == 201, uploaded.text
    file_id = uploaded.json()["id"]
    with env["sessions"]() as db:
        db.get(FileRecord, file_id).status = "clean"
        db.commit()
    set_scope(env, "exports.download", "own")
    allowed = env["client"].get(f"/api/v1/files/{file_id}/download")
    assert allowed.status_code == 200 and allowed.content == content
    make_foreign(env, env["request_id"])
    get(env, f"/files/{file_id}/download", expected=404)
    set_scope(env, "exports.download", "all")
    allowed = env["client"].get(f"/api/v1/files/{file_id}/download")
    assert allowed.status_code == 200 and allowed.content == content
