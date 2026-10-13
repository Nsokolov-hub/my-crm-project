"""Business remark of 13 October: DAP and resale in Russia are calculated without a supply wave."""

from app.crm.models import QuoteItem
from tests.support.business import cmd, structured
from tests.support.crm import post


def test_resale_in_russia_needs_no_wave_and_stays_apart_from_imports(crm):
    req, data, _, _ = structured(crm, mode="RUSSIA")
    assert req.get("wave_id") is None
    calculation = cmd(crm, f"/requests/{req['id']}/calculations", data)
    assert [line["calculation_type"] for line in calculation["snapshot"]["lines"]] == ["RUSSIA"]
    assert "wave" not in calculation["snapshot"]

    importer = post(crm, "/counterparties", {"name": "Импортёр", "kind": "supplier"})
    with crm["sessions"]() as db:
        source = db.get(QuoteItem, data["selections"][0]["quote_item_id"])
        item = {"nomenclature_id": source.nomenclature_id, "packing_id": source.packing_id, "quantity": "1",
                "unit_price": "10", "currency_id": source.currency_id, "delivery_days": 30}
    imported = post(crm, f"/requests/{req['id']}/quote-sheets", {"supplier_id": importer["id"], "items": [item]})
    data["request_version"] = crm["client"].get(f"/api/v1/requests/{req['id']}").json()["version"]
    data["selections"].append({"quote_item_id": imported["items"][0]["id"], "markup_coefficient": "1.5"})
    refused = cmd(crm, f"/requests/{req['id']}/calculations/preview", data, status=422)
    assert refused["code"] == "CALCULATION_TYPES_MIXED"
