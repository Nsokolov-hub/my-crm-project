"""A mistaken unpaid invoice can be voided without erasing its history."""

import copy
from datetime import date, timedelta
from decimal import Decimal

from test_commerce import command, get, prepare
from test_commerce import commerce as commerce

from app.crm.models import Counterparty


def test_unpaid_invoice_can_be_voided_and_issued_again(commerce):
    state = prepare(commerce, with_invoice=True)
    invoice = state["invoice"]
    execution = state["execution"]
    path = f"/invoices/{invoice['id']}/cancel"
    payload = {"version": invoice["version"], "reason": "Клиент исправил реквизиты"}

    cancelled = command(commerce, path, payload)
    assert cancelled["status"] == "cancelled"
    listed = get(commerce, f"/requests/{commerce['request_id']}/invoices")["items"][0]
    assert listed["status"] == "cancelled"
    assert listed["remaining"] == "0"

    replacement = command(
        commerce,
        f"/requests/{commerce['request_id']}/invoices",
        {
            "proposal_id": state["proposal"]["id"],
            "lines": [{"execution_id": execution["id"], "quantity": "10"}],
            "due_date": (date.today() + timedelta(days=10)).isoformat(),
            "terms": "Исправленный счёт",
        },
    )
    assert replacement["id"] != invoice["id"]
    assert replacement["total"] == invoice["total"]


def test_invoice_with_confirmed_allocated_payment_cannot_be_voided(commerce):
    from test_commerce import fund

    state = prepare(commerce, with_invoice=True)
    fund(commerce, state)
    invoice = state["invoice"]
    error = command(
        commerce,
        f"/invoices/{invoice['id']}/cancel",
        {"version": invoice["version"], "reason": "Нужно исправить оплаченный счёт"},
        expected=409,
    )
    assert error["code"] == "INVOICE_HAS_PAYMENT"
    listed = get(commerce, f"/requests/{commerce['request_id']}/invoices")["items"][0]
    assert listed["status"] != "cancelled"


def test_cancelled_invoice_unlocks_execution_correction(commerce):
    state = prepare(commerce, with_invoice=True)
    invoice = state["invoice"]
    execution = state["execution"]
    command(
        commerce,
        f"/invoices/{invoice['id']}/cancel",
        {"version": invoice["version"], "reason": "Клиент меняет принятый состав"},
    )
    cancelled = command(
        commerce,
        f"/executions/{execution['id']}/cancel",
        {"version": execution["version"], "reason": "Клиент выбрал другого поставщика"},
    )
    assert Decimal(cancelled["cancelled_quantity"]) == Decimal(execution["quantity"])


def test_new_supplier_can_replace_a_cancelled_unpaid_order(commerce):
    """The ordinary corrective path remains usable end to end."""
    state = prepare(commerce, with_invoice=True)
    invoice = state["invoice"]
    execution = state["execution"]
    command(
        commerce,
        f"/invoices/{invoice['id']}/cancel",
        {"version": invoice["version"], "reason": "Смена поставщика"},
    )
    command(
        commerce,
        f"/executions/{execution['id']}/cancel",
        {"version": execution["version"], "reason": "Смена поставщика"},
    )
    with commerce["sessions"]() as db:
        supplier = Counterparty(name="Новый поставщик", kind="supplier", owner_id=commerce["owner_id"])
        db.add(supplier)
        db.commit()
        supplier_id = supplier.id

    quote_payload = {**state["quote_payload"], "supplier_id": supplier_id}
    quote = command(commerce, f"/requests/{commerce['request_id']}/quotes", quote_payload)
    calc_payload = copy.deepcopy(state["calculation_payload"])
    calc_payload["selections"][0]["quote_id"] = quote["id"]
    calc_payload["reason"] = "Новый поставщик после отказа прежнего"
    calc = command(commerce, f"/requests/{commerce['request_id']}/calculations", calc_payload)
    proposal = command(
        commerce,
        f"/requests/{commerce['request_id']}/proposals",
        {
            "calculation_id": calc["id"],
            "valid_until": (date.today() + timedelta(days=15)).isoformat(),
            "terms": "Поставка через нового поставщика",
        },
    )
    accepted = command(
        commerce,
        f"/proposals/{proposal['id']}/accept",
        {
            "version": proposal["version"],
            "lines": [{"line_id": quote["id"], "quantity": "10"}],
            "reason": "Клиент согласовал нового поставщика",
        },
    )
    assert accepted["executions"][0]["quote_id"] == quote["id"]
    assert accepted["executions"][0]["id"] != execution["id"]
