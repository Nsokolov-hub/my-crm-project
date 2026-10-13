"""A proposal issued to a duplicate client still turns into an invoice and takes payments after the merge."""

from datetime import date, timedelta

from app.crm.models import Counterparty, Request
from tests.support.commerce import command, fund, prepare


def test_invoice_and_payment_of_a_request_moved_from_a_merged_duplicate(commerce):
    state = prepare(commerce)
    accepted = command(commerce, f"/proposals/{state['proposal']['id']}/accept", {
        "version": 1, "lines": [{"line_id": state["quote"]["id"], "quantity": "10"}],
        "reason": "Клиент подтвердил весь состав",
    })
    with commerce["sessions"].begin() as db:
        main = Counterparty(name="Основной клиент", kind="client", owner_id=commerce["owner_id"])
        db.add(main)
        db.flush()
        main_id = main.id
        db.get(Request, commerce["request_id"]).client_id = main_id
    invoice = {
        "proposal_id": state["proposal"]["id"],
        "lines": [{"execution_id": accepted["executions"][0]["id"], "quantity": "10"}],
        "due_date": (date.today() + timedelta(days=10)).isoformat(),
        "terms": "Полная предоплата",
    }
    path = f"/requests/{commerce['request_id']}/invoices"
    # Another client on the request is a change of parties.
    assert command(commerce, path, invoice, expected=422)["code"] == "DOCUMENT_PARTIES_CHANGED"

    with commerce["sessions"].begin() as db:
        duplicate = db.get(Counterparty, commerce["client_id"])
        duplicate.merged_into_id, duplicate.archived = main_id, True
    state["invoice"] = command(commerce, path, invoice)
    assert state["invoice"]["client_id"] == commerce["client_id"]
    # The payment comes from the main card and settles the invoice issued to the duplicate.
    payment = fund(commerce, state)
    assert payment["client_id"] == main_id
    assert payment["status"] == "confirmed"
