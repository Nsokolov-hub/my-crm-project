"""Repeat-order reminders use released customer sale prices and respect request visibility."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.commerce.models import CommercialDocument, Execution
from app.core.models import PermissionGrant
from app.crm.models import Nomenclature, QuoteItem, Request, RequestItem
from tests.support.business import cmd, handoff, release, structured
from tests.support.crm import login, post


def sold(env):
    req, data, _, _ = structured(env)
    calculation = cmd(env, f"/requests/{req['id']}/calculations", data)
    proposal, execution = release(env, req, calculation, "1")
    handoff(env, req, execution)
    return req, data, proposal, execution, calculation


def repeat(env, req, article=" a123 "):
    current = post(env, "/requests", {"title": "Повторный заказ", "client_id": req["client_id"]})
    with env["sessions"].begin() as db:
        db.add(RequestItem(request_id=current["id"], description="Повтор", article=article))
    return current


def history(env, current, **params):
    return env["client"].get(f"/api/v1/requests/{current['id']}/sale-price-history", params=params)


def test_sale_history_uses_the_actual_sale_snapshot_currency_and_vat(crm):
    req, data, proposal, execution, calculation = sold(crm)
    current = repeat(crm, req)
    with crm["sessions"].begin() as db:
        old = db.get(Execution, execution["id"])
        old.procurement_at = datetime(2026, 8, 15, 10, tzinfo=timezone.utc)
        quote = db.get(QuoteItem, data["selections"][0]["quote_item_id"])
        nomenclature = db.get(Nomenclature, quote.nomenclature_id)
        nomenclature.article = "REPLACED"
        nomenclature.name = "Новое имя в каталоге"
        quote.unit_price = Decimal("99999")

    response = history(crm, current)
    assert response.status_code == 200, response.text
    [row] = response.json()["items"]
    assert (row["request_id"], row["request_number"], row["execution_id"]) == (
        req["id"], req["number"], execution["id"])
    assert row["sold_at"] == "2026-08-15T10:00:00Z"
    assert (row["article"], row["packing"], row["manufacturer"]) == ("A123", "100 mg", "Aozeal")
    assert row["description"] == "Sample 100 mg"
    assert Decimal(row["unit_price"]) == Decimal(calculation["snapshot"]["lines"][0]["unit_price"])
    assert row["currency"] == proposal["currency"] == "RUB"
    assert row["price_includes_vat"] is True
    assert Decimal(row["quantity"]) == 1
    assert response.json()["limit"] == 50


def add_sale(env, source, *, client_id=None, owner_id=None, article="A123", approved=True,
             confirmed=True, cancelled=False, days=1, price="123.45", currency="RUB", vat=True):
    """Additional released history with independent customer and request ownership."""
    source_req, _, proposal_data, execution_data, _ = source
    with env["sessions"].begin() as db:
        old_proposal = db.get(CommercialDocument, proposal_data["id"])
        old_execution = db.get(Execution, execution_data["id"])
        at = datetime(2026, 11, 1, tzinfo=timezone.utc) - timedelta(days=days)
        req = Request(number=f"H-{price}-{days}", title="Историческая продажа",
                      client_id=client_id or source_req["client_id"], owner_id=owner_id or env["admin"].id,
                      commercial_stage="sale_confirmed" if confirmed else "composition_agreed",
                      sale_confirmed_at=at if confirmed else None)
        db.add(req)
        db.flush()
        proposal = CommercialDocument(
            request_id=req.id, seller_id=old_proposal.seller_id, client_id=req.client_id,
            kind="proposal", number=req.number, calculation_id=old_proposal.calculation_id,
            status="accepted", currency=currency, total=Decimal(price), valid_until=old_proposal.valid_until,
            snapshot={"price_includes_vat": vat}, files={}, author_id=env["admin"].id)
        db.add(proposal)
        db.flush()
        snapshot = deepcopy(old_execution.snapshot)
        snapshot["product"]["article"] = article
        snapshot["quote_item"]["nomenclature_id"] = "old-catalogue-id"
        snapshot["unit_price"] = price
        ex = Execution(request_id=req.id, item_id=old_execution.item_id, proposal_id=proposal.id,
                       quote_item_id=old_execution.quote_item_id, line_id=old_execution.line_id,
                       quantity=2, cancelled_quantity=2 if cancelled else 0, unit="pcs", snapshot=snapshot,
                       acceptance_reason="Продажа", accepted_by=env["admin"].id,
                       procurement_at=at if approved else None)
        db.add(ex)
        db.flush()
        return req.id, ex.id


def test_sale_history_omits_drafts_other_customers_articles_cancellations_and_current_request(crm):
    source = sold(crm)
    req = source[0]
    current = repeat(crm, req)
    other_client = post(crm, "/counterparties", {"name": "Другой покупатель"})
    add_sale(crm, source, client_id=other_client["id"], days=2)
    add_sale(crm, source, article="OTHER", days=3)
    add_sale(crm, source, approved=False, days=4)
    add_sale(crm, source, confirmed=False, days=5)
    add_sale(crm, source, cancelled=True, days=6)
    historical_id, historical_execution = add_sale(crm, source, days=7, price="77.25", currency="USD", vat=False)
    # The request being calculated is excluded even when it already contains an approved sale.
    assert {row["request_id"] for row in history(crm, req).json()["items"]} == {historical_id}

    rows = history(crm, current).json()["items"]
    assert {row["request_id"] for row in rows} == {req["id"], historical_id}
    old = next(row for row in rows if row["execution_id"] == historical_execution)
    assert (old["unit_price"], old["currency"], old["price_includes_vat"]) == ("77.25", "USD", False)
    assert len(history(crm, current, limit=1).json()["items"]) == 1
    assert history(crm, current, limit=101).status_code == 422


def test_sale_history_cannot_reveal_another_managers_requests_or_arbitrary_quotes(crm):
    source = sold(crm)
    req, data, _, _, _ = source
    current = repeat(crm, req)
    owned, execution_id = add_sale(crm, source, owner_id=crm["manager"].id, days=10)
    with crm["sessions"].begin() as db:
        db.get(Request, current["id"]).owner_id = crm["manager"].id
    login(crm, "manager@example.com")
    [row] = history(crm, current).json()["items"]
    assert (row["request_id"], row["execution_id"]) == (owned, execution_id)
    assert history(crm, req).status_code == 404
    # Even a known quote identifier from an inaccessible request is not accepted as a selection.
    foreign_quote_id = data["selections"][0]["quote_item_id"]
    assert history(crm, current, quote_item_ids=foreign_quote_id).status_code == 404
    assert history(crm, current, quote_item_ids="").json()["items"] == []
    with crm["sessions"].begin() as db:
        grant = db.query(PermissionGrant).filter_by(user_id=crm["manager"].id, code="requests.read").one()
        grant.allow = False
    assert history(crm, current).status_code == 404


def test_quote_selection_limits_history_to_the_selected_article(crm):
    source = sold(crm)
    req, data, _, _, _ = source
    current = repeat(crm, req)
    with crm["sessions"]() as db:
        quote = db.get(QuoteItem, data["selections"][0]["quote_item_id"])
        body = {key: getattr(quote, key) for key in ("supplier_id", "nomenclature_id", "packing_id", "currency_id")}
    sheet = post(crm, f"/requests/{current['id']}/quote-sheets", {
        "supplier_id": body.pop("supplier_id"),
        "items": [{**body, "quantity": "1", "unit_price": "5", "delivery_days": 7}],
    })
    quote_id = sheet["items"][0]["id"]
    another_request, _ = add_sale(crm, source, article="B222", days=20)
    with crm["sessions"].begin() as db:
        db.add(RequestItem(request_id=current["id"], description="Другая позиция", article="B222"))
        # Another article must not match merely because somebody later reused the catalogue card.
        unrelated = db.get(Execution, source[3]["id"])
        snapshot = deepcopy(unrelated.snapshot)
        snapshot["product"]["article"] = "RENAMED"
        unrelated.snapshot = snapshot
    assert {row["request_id"] for row in history(crm, current).json()["items"]} == {another_request}
    assert history(crm, current, quote_item_ids=quote_id).json()["items"] == []
    with crm["sessions"].begin() as db:
        db.get(Nomenclature, body["nomenclature_id"]).article = None
    # With no current article, stable catalogue identity remains a useful fallback.
    [fallback] = history(crm, current, quote_item_ids=quote_id).json()["items"]
    assert fallback["request_id"] == req["id"] and fallback["article"] == "RENAMED"
    assert history(crm, current, quote_item_ids="missing").status_code == 404
