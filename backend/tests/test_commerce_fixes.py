"""Regression coverage for payment restoration and corrected order fulfillment."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_commerce import approve, command, fund, get, prepare, wave
from test_commerce import commerce as commerce_fixture

from app.commerce.fulfillment import WAVE_TRANSITIONS
from app.commerce.models import Execution
from app.crm.models import Request, RequestItem


@pytest.fixture
def commerce(tmp_path, monkeypatch):
    yield from commerce_fixture.__wrapped__(tmp_path, monkeypatch)


def raw(env, path, payload, method="post"):
    client = TestClient(env["client"].app, raise_server_exceptions=False)
    return getattr(client, method)("/api/v1" + path, json={"idempotency_key": str(uuid4()), **payload})


def accepted(env):
    state = prepare(env)
    result = command(
        env,
        f"/proposals/{state['proposal']['id']}/accept",
        {
            "version": 1,
            "lines": [{"line_id": state["quote"]["id"], "quantity": "10"}],
            "reason": "Synthetic review acceptance",
        },
    )
    state["execution"] = result["executions"][0]
    return state


def allocated(env, number="REVIEW-WAVE", shipment=None):
    state = prepare(env, with_invoice=True)
    fund(env, state)
    approval = approve(env, state)
    shipment = shipment or wave(env, number)
    allocation = command(
        env,
        f"/waves/{shipment['id']}/allocations",
        {"execution_id": state["execution"]["id"], "approval_id": approval["id"], "quantity": "10"},
    )
    return state, shipment, allocation


def event(env, allocation, kind):
    return command(
        env,
        f"/allocations/{allocation['id']}/events",
        {
            "kind": kind,
            "quantity": "10",
            "reason": "Synthetic review event",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def read_wave(env, shipment):
    return next(row for row in get(env, "/waves")["items"] if row["id"] == shipment["id"])


def test_valid_revision_of_uninvoiced_execution_succeeds(commerce):
    state = accepted(commerce)
    path = f"/executions/{state['execution']['id']}/revise"
    payload = {"version": state["execution"]["version"], "quantity": "5", "reason": "Customer reduces order"}
    result = command(commerce, path, payload)
    revised = command(
        commerce,
        f"/executions/{result['id']}/revise",
        {"version": result["version"], "quantity": "8", "reason": "Customer adjusts again"},
    )
    assert Decimal(revised["quantity"]) == 8
    assert revised["previous_id"] == result["id"]
    assert revised["revision"] == 3
    with commerce["sessions"]() as db:
        original = db.get(Execution, state["execution"]["id"])
        assert original.quantity == original.cancelled_quantity == 10
        assert Decimal(original.snapshot["quantity"]) == 10
        latest = db.get(Execution, revised["id"])
        assert Decimal(latest.snapshot["total"]) == Decimal("2875")


def test_cancel_uninvoiced_execution_succeeds(commerce):
    state = accepted(commerce)
    result = command(
        commerce,
        f"/executions/{state['execution']['id']}/cancel",
        {"version": state["execution"]["version"], "reason": "Customer cancels order"},
    )
    assert Decimal(result["cancelled_quantity"]) == 10
    assert result["state"] == "cancelled"


def test_funding_reallocation_succeeds_and_commits(commerce):
    env = commerce
    state = prepare(env, with_invoice=True)
    payment = fund(env, state)
    approve(env, state)
    payment = command(
        env,
        f"/payments/{payment['id']}/reverse",
        {
            "version": payment["version"],
            "amount": "100",
            "allocation_id": payment["allocations"][0]["id"],
            "kind": "unallocate",
            "reason": "Correct original allocation",
        },
    )
    response = raw(
        env,
        f"/payments/{payment['id']}/allocate",
        {
            "version": payment["version"],
            "allocations": [{"invoice_id": state["invoice"]["id"], "amount": "100"}],
        },
    )
    execution = get(env, f"/requests/{env['request_id']}/executions")["items"][0]
    assert response.status_code == 200, {
        "status": response.status_code,
        "body": response.text,
        "persisted_ratio": execution["funding"]["ratio"],
        "persisted_deficit": execution["financing_deficit"],
    }
    assert Decimal(execution["funding"]["ratio"]) == 1
    assert execution["financing_deficit"] is False


def test_ordered_event_allows_closing_wave(commerce):
    _, shipment, allocation = allocated(commerce)
    event(commerce, allocation, "ordered")
    current = read_wave(commerce, shipment)
    response = raw(
        commerce,
        f"/waves/{shipment['id']}",
        {"version": current["version"], "status": "closed", "reason": "Close planned shipment"},
        method="patch",
    )
    assert response.status_code == 200, {
        "status_after_ordered": current["status"],
        "patch_status": response.status_code,
        "body": response.text,
    }


def test_full_delivery_keeps_supported_wave_status(commerce):
    _, shipment, allocation = allocated(commerce)
    for kind in ("shipped", "arrived", "delivered"):
        allocation = event(commerce, allocation, kind)
    current = read_wave(commerce, shipment)
    assert current["status"] in WAVE_TRANSITIONS, current["status"]


def test_ready_event_does_not_reopen_closed_wave(commerce):
    _, shipment, allocation = allocated(commerce)
    command(
        commerce,
        f"/waves/{shipment['id']}",
        {"version": shipment["version"], "status": "closed", "reason": "No more positions"},
        method="patch",
    )
    event(commerce, allocation, "ready")
    current = read_wave(commerce, shipment)
    assert current["status"] == "closed", current["status"]


def test_wave_does_not_finish_when_second_request_is_undelivered(commerce):
    env = commerce
    state, shipment, first = allocated(env)
    with env["sessions"]() as db:
        request = Request(
            number="REVIEW-SECOND",
            title="Second request in shared shipment",
            seller_id=env["seller_id"],
            client_id=env["client_id"],
            owner_id=env["owner_id"],
            is_test=True,
        )
        db.add(request)
        db.flush()
        item = RequestItem(
            request_id=request.id,
            description="Second test item",
            cas="64-17-5",
            quantity=Decimal("10"),
            unit="pcs",
            purity="99%",
            packaging="1 штука",
            allow_analogue=False,
        )
        db.add(item)
        db.commit()
        other = {**env, "request_id": request.id, "item_id": item.id}
    quote = command(
        other,
        f"/requests/{other['request_id']}/quotes",
        {**state["quote_payload"], "item_id": other["item_id"]},
    )
    calculation = command(
        other,
        f"/requests/{other['request_id']}/calculations",
        {
            **state["calculation_payload"],
            "selections": [{"quote_id": quote["id"], "quote_revision": 1, "quantity": "10", "unit": "pcs"}],
        },
    )
    proposal = command(
        other,
        f"/requests/{other['request_id']}/proposals",
        {
            "calculation_id": calculation["id"],
            "valid_until": state["proposal"]["valid_until"],
            "terms": "Second request",
        },
    )
    execution = command(
        other,
        f"/proposals/{proposal['id']}/accept",
        {
            "version": 1,
            "lines": [{"line_id": quote["id"], "quantity": "10"}],
            "reason": "Accept second order",
        },
    )["executions"][0]
    invoice = command(
        other,
        f"/requests/{other['request_id']}/invoices",
        {
            "proposal_id": proposal["id"],
            "lines": [{"execution_id": execution["id"], "quantity": "10"}],
            "due_date": state["invoice"]["valid_until"],
            "terms": "Second request",
        },
    )
    other_state = {"execution": execution, "invoice": invoice}
    fund(other, other_state)
    approval = approve(other, other_state)
    command(
        other,
        f"/waves/{shipment['id']}/allocations",
        {"execution_id": execution["id"], "approval_id": approval["id"], "quantity": "10"},
    )
    for kind in ("shipped", "arrived", "delivered"):
        first = event(env, first, kind)
    current = read_wave(env, shipment)
    assert len(current["allocations"]) == 2
    assert current["status"] not in ("delivered", "completed"), {
        "wave_status": current["status"],
        "delivered_quantities": [row["delivered"] for row in current["allocations"]],
    }


def test_execution_with_active_invoice_cannot_be_cancelled(commerce):
    state = prepare(commerce, with_invoice=True)
    result = command(
        commerce,
        f"/executions/{state['execution']['id']}/cancel",
        {"version": state["execution"]["version"], "reason": "Customer cancels invoice"},
        expected=422,
    )
    assert result["code"] == "HAS_INVOICE"


@pytest.mark.parametrize("kind", ["correction", "claim_opened"])
def test_completed_wave_reopens_after_correction_or_claim(commerce, kind):
    _, shipment, allocation = allocated(commerce)
    for event_kind in ("shipped", "arrived", "delivered"):
        allocation = event(commerce, allocation, event_kind)
    for status in ("closed", "shipped", "arrived", "completed"):
        shipment = command(
            commerce,
            f"/waves/{shipment['id']}",
            {"version": shipment["version"], "status": status, "reason": "Complete shipment"},
            method="patch",
        )
    payload = {
        "kind": kind,
        "quantity": "1",
        "reason": "Correct delivered amount or record claim",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }
    if kind == "correction":
        payload["correction_of"] = next(
            row["id"] for row in allocation["events"] if row["kind"] == "delivered"
        )
    command(commerce, f"/allocations/{allocation['id']}/events", payload)
    current = read_wave(commerce, shipment)
    assert current["status"] == "arrived"
    assert current["version"] > shipment["version"]


def test_revision_cannot_overfill_demand_across_proposals(commerce):
    state = accepted(commerce)
    revised = command(
        commerce,
        f"/executions/{state['execution']['id']}/revise",
        {"version": state["execution"]["version"], "quantity": "5", "reason": "Reduce first composition"},
    )
    proposal = command(
        commerce,
        f"/requests/{commerce['request_id']}/proposals",
        {
            "calculation_id": state["calc"]["id"],
            "valid_until": state["proposal"]["valid_until"],
            "terms": "Separate remaining quantity",
        },
    )
    command(
        commerce,
        f"/proposals/{proposal['id']}/accept",
        {
            "version": 1,
            "lines": [{"line_id": state["quote"]["id"], "quantity": "5"}],
            "reason": "Accept remaining demand",
        },
    )
    error = command(
        commerce,
        f"/executions/{revised['id']}/revise",
        {"version": revised["version"], "quantity": "6", "reason": "Attempt excess demand"},
        expected=422,
    )
    assert error["code"] == "ACCEPTANCE_EXCEEDED"
    with commerce["sessions"]() as db:
        assert db.get(Execution, revised["id"]).cancelled_quantity == 0


def test_funding_restore_notification_can_repeat_after_another_correction(commerce):
    from app.core.models import Notification

    env = commerce
    state = prepare(env, with_invoice=True)
    payment = fund(env, state)
    approve(env, state)
    for _ in range(2):
        payment = command(
            env,
            f"/payments/{payment['id']}/reverse",
            {
                "version": payment["version"],
                "amount": "100",
                "allocation_id": payment["allocations"][0]["id"],
                "kind": "unallocate",
                "reason": "Correct payment allocation",
            },
        )
        payment = command(
            env,
            f"/payments/{payment['id']}/allocate",
            {
                "version": payment["version"],
                "allocations": [{"invoice_id": state["invoice"]["id"], "amount": "100"}],
            },
        )
    with env["sessions"]() as db:
        restored = db.scalars(
            select(Notification).where(
                Notification.event_key.like("funding-restored:%"), Notification.user_id == env["owner_id"]
            )
        ).all()
        assert len(restored) == 2
        assert all(row.entity_type == "request" and row.entity_id == env["request_id"] for row in restored)


def test_execution_state_tracks_approval_before_allocation(commerce):
    state = prepare(commerce, with_invoice=True)
    fund(commerce, state)
    approval = command(
        commerce,
        f"/requests/{commerce['request_id']}/approvals",
        {"execution_ids": [state["execution"]["id"]], "reviewer_id": commerce["owner_id"]},
    )
    assert get(commerce, f"/requests/{commerce['request_id']}/executions")["items"][0]["state"] == "pending"
    command(commerce, f"/approvals/{approval['id']}/decision", {"version": 1, "decision": "approved"})
    assert get(commerce, f"/requests/{commerce['request_id']}/executions")["items"][0]["state"] == "approved"


def test_transfer_after_order_fact_is_fully_corrected(commerce):
    _, _, allocation = allocated(commerce)
    allocation = event(commerce, allocation, "ordered")
    ordered = next(row for row in allocation["events"] if row["kind"] == "ordered")
    command(
        commerce,
        f"/allocations/{allocation['id']}/events",
        {
            "kind": "correction",
            "correction_of": ordered["id"],
            "quantity": "10",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "reason": "Cancel mistaken order fact",
        },
    )
    target = wave(commerce, "CORRECTED-TRANSFER")
    result = command(
        commerce,
        f"/allocations/{allocation['id']}/transfer",
        {
            "version": allocation["version"],
            "target_wave_id": target["id"],
            "quantity": "10",
            "reason": "Move the corrected plan",
        },
    )
    assert len(result["items"]) == 1
    assert result["items"][0]["wave_id"] == target["id"]
    assert result["items"][0]["previous_id"] == allocation["id"]
