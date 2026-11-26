"""End-to-end client price reduction without rewriting historical calculations or quotes."""

import io
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal

from openpyxl import load_workbook

from app.business.models import PriceReductionRequest
from app.core.models import OutboxEvent
from app.crm.models import Nomenclature, QuoteItem, QuoteSheet, Request
from tests.support.business import cmd, confirm, issue, structured
from tests.support.crm import login


def _decision(client, review, decision="approved", expected=200):
    response = client.post(
        f"/api/v1/workflow-approvals/{review['id']}/decision",
        json={
            "version": review["version"],
            "decision": decision,
            "reason": "Проверка согласования снижения",
            "idempotency_key": f"decision-{review['id']}-{decision}",
        },
    )
    assert response.status_code == expected, response.text
    return response.json()


def _template_with_prices(client, request_id, reduction_id, discounted=True):
    response = client.get(
        f"/api/v1/requests/{request_id}/quote-import/template.xlsx",
        params={"price_reduction_id": reduction_id},
    )
    assert response.status_code == 200, response.text
    book = load_workbook(io.BytesIO(response.content))
    sheet = book["Квоты"]
    headers = {cell.value: cell.column for cell in sheet[1]}
    for row in range(2, sheet.max_row + 1):
        cell = sheet.cell(row, headers["Цена"])
        if discounted:
            cell.value = float(Decimal(str(cell.value)) * Decimal("0.9"))
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def _preview(client, request_id, reduction_id, content):
    response = client.post(
        f"/api/v1/requests/{request_id}/table-imports/preview",
        data={"kind": "quotes", "price_reduction_id": reduction_id},
        files={
            "file": (
                "discount.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_price_reduction_full_flow_and_assigned_second_approver(crm, monkeypatch):
    req, data, _, _ = structured(crm)
    calculation = cmd(crm, f"/requests/{req['id']}/calculations", data)
    historical_snapshot = deepcopy(calculation["snapshot"])
    issue(crm, req, calculation)
    with crm["sessions"].begin() as db:
        request = db.get(Request, req["id"])
        request.owner_id = crm["manager"].id
        # The manually created fixture quote predates transport metadata; the reviewed template must be valid.
        for product in db.query(Nomenclature):
            product.storage_temperature = "1-25"
            product.transport_hazard = "Не опасный"
        quote_id = calculation["snapshot"]["lines"][0]["quote_item_id"]
        old_sheet_id = db.get(QuoteItem, quote_id).quote_id
        request_version = request.version

    quote = crm["client"].get(
        f"/api/v1/requests/{req['id']}/quote-items", params={"ids": quote_id}
    ).json()["items"][0]
    options = crm["client"].get(
        f"/api/v1/requests/{req['id']}/price-reduction-options",
        params={"quote_item_ids": quote_id},
    ).json()
    line = calculation["snapshot"]["lines"][0]
    target = Decimal(line["unit_price"]) * Decimal("0.9")
    hints = crm["client"].post(
        f"/api/v1/requests/{req['id']}/price-reduction-hints",
        json={"targets": [{"quote_item_id": quote_id, "target_sale_price": str(target)}]},
    ).json()
    assert hints["items"][0]["required_purchase_rub"] is not None
    assert {row["id"] for row in options["managers"]} == {crm["admin"].id, crm["manager"].id}

    reduction = cmd(crm, f"/requests/{req['id']}/price-reductions", {
        "request_version": request_version,
        "quote_item_ids": [quote_id],
        "leader_id": crm["admin"].id,
        "manager_id": crm["manager"].id,
        "client_due_date": (date.today() + timedelta(days=30)).isoformat(),
        "comment": "Клиент готов рассмотреть аналог, нужна скидка 10 %",
        "targets": [{"quote_item_id": quote_id, "target_sale_price": str(target)}],
    })
    assert reduction["status"] == "pending_leader"
    assert reduction["reviews"][0]["reviewer_id"] == crm["admin"].id
    _decision(crm["client"], reduction["reviews"][0])

    after_leader = crm["client"].get(f"/api/v1/price-reductions/{reduction['id']}").json()
    manager_review = after_leader["reviews"][-1]
    assert after_leader["status"] == "pending_manager"
    assert manager_review["reviewer_id"] == crm["manager"].id
    # Even a global approver cannot decide a stage assigned to another particular employee.
    _decision(crm["client"], manager_review, expected=404)

    login(crm, "manager@example.com")
    visible = crm["client"].get("/api/v1/workflow-approvals").json()["items"]
    assigned = next(row for row in visible if row["id"] == manager_review["id"])
    assert assigned["can_decide"] is True
    _decision(crm["client"], manager_review)
    login(crm)

    approved = crm["client"].get(f"/api/v1/price-reductions/{reduction['id']}").json()
    assert approved["status"] == "approved"
    monkeypatch.setattr("app.business.price_reductions.settings.smtp_host", "smtp.test")
    monkeypatch.setattr("app.business.price_reductions.settings.smtp_from", "crm@test.local")
    mailed = cmd(crm, f"/price-reductions/{reduction['id']}/mail", {
        "version": approved["version"],
        "subject": "Please review the price",
        "body": "Dear supplier, please consider a 10 % discount for the positions below.",
        "send": True,
    })
    assert mailed["status"] == "awaiting_price_reduction"
    assert "Номенклатура" in mailed["mails"][0]["body"]
    assert quote["nomenclature_name"] in mailed["mails"][0]["body"]
    assert mailed["mails"][0]["kind"] == "price_reduction"
    with crm["sessions"].begin() as db:
        assert db.query(OutboxEvent).filter_by(kind="supplier.mail").count() == 1
        assert db.get(Request, req["id"]).commercial_stage == "awaiting_price_reduction"

    # A file without an actual discount is rejected by the normal preview and cannot be confirmed.
    invalid = _preview(crm["client"], req["id"], reduction["id"], _template_with_prices(
        crm["client"], req["id"], reduction["id"], discounted=False
    ))
    assert invalid["summary"]["errors"] == 1
    assert "ниже" in invalid["rows"][0]["errors"][0]

    batch = _preview(crm["client"], req["id"], reduction["id"], _template_with_prices(
        crm["client"], req["id"], reduction["id"], discounted=True
    ))
    assert batch["summary"]["errors"] == 0
    assert batch["rows"][0]["data"]["replace_quote_item_id"] == quote_id
    completed_import = confirm(crm, req["id"], batch["id"])
    replacement_id = completed_import["result"]["price_reduction_map"][quote_id]

    current = crm["client"].get(
        f"/api/v1/requests/{req['id']}/quote-items", params={"all": "true"}
    ).json()["items"]
    assert quote_id not in {row["id"] for row in current}
    replacement = next(row for row in current if row["id"] == replacement_id)
    assert replacement["previous_id"] == quote_id
    assert Decimal(replacement["previous_unit_price"]) == Decimal(quote["unit_price"])
    assert Decimal(replacement["discount_percent"]) == Decimal("10.00")
    assert replacement["price_reduction_id"] == reduction["id"]
    with crm["sessions"].begin() as db:
        old = db.get(QuoteItem, quote_id)
        assert old.archived is True and old.archive_status == "superseded"
        assert Decimal(old.unit_price) == Decimal(quote["unit_price"])
        assert db.get(QuoteSheet, old_sheet_id).archived is True
        assert db.get(PriceReductionRequest, reduction["id"]).status == "completed"
        assert db.get(PriceReductionRequest, reduction["id"]).result_map == {quote_id: replacement_id}
    assert calculation["snapshot"] == historical_snapshot
