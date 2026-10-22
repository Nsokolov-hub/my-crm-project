"""Business remarks of 22 October 2026: archives of certificates in a request, planned volume taken off a wave,
a quote typed by hand from the catalogue, a copy of a saved calculation, requests of a plan gathered into its
working wave, and suppliers' answers counted with their refusals."""

import io
import zipfile
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from app.business.models import SupplierMail
from app.communication.models import FileRecord
from app.core.db import utcnow
from app.crm.models import QuoteItem, Request, RequestItem
from tests.support.business import cmd, handoff, peer_request, release, structured, wave_row
from tests.support.crm import login, post

LOGISTICS = {"name": "Международная логистика", "amount": "400", "currency": "RUB", "method": "BY_QUANTITY",
             "scope": "WAVE", "stage": "INTERNATIONAL_LOGISTICS"}


def get(crm, path, status=200):
    response = crm["client"].get(f"/api/v1{path}")
    assert response.status_code == status, response.text
    return response.json()


def archive(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        for name, data in entries:
            package.writestr(name, data)
    return buffer.getvalue()


def upload(crm, request_id, name, content, media_type="application/x-zip-compressed", status=201):
    response = crm["client"].post("/api/v1/files", data={"entity_type": "request", "entity_id": request_id},
                                  files={"file": (name, content, media_type)},
                                  headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == status, response.text
    return response.json()


PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def test_certificates_come_as_an_archive_and_chosen_files_go_out_as_one(crm):
    req, _, _, _ = structured(crm)
    certificates = archive([(f"Certificate {number}.pdf", PDF) for number in range(1, 6)])
    packed = upload(crm, req["id"], "Сертификаты.zip", certificates)
    assert (packed["name"], packed["status"]) == ("Сертификаты.zip", "quarantined")
    refused = upload(crm, req["id"], "Сертификаты.zip", archive([("CoA.pdf", PDF), ("setup.exe", b"MZ")]),
                     status=422)
    assert refused["code"] == "FILE_ARCHIVE_UNSAFE"

    first = upload(crm, req["id"], "CoA.pdf", PDF, "application/pdf")
    second = upload(crm, req["id"], "CoA.pdf", PDF + b"\n", "application/pdf")
    with crm["sessions"].begin() as db:
        for file_id in (packed["id"], first["id"], second["id"]):
            db.get(FileRecord, file_id).status = "clean"
    response = crm["client"].get("/api/v1/files/archive", params={
        "ids": ",".join((packed["id"], first["id"], second["id"])), "name": f"Вложения заявки №{req['number']}"})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as package:
        assert package.namelist() == ["Сертификаты.zip", "CoA.pdf", "CoA (2).pdf"]
        assert package.read("CoA (2).pdf") == PDF + b"\n"
    with crm["sessions"].begin() as db:
        db.get(FileRecord, second["id"]).status = "quarantined"
    unchecked = get(crm, f"/files/archive?ids={first['id']},{second['id']}", status=409)
    assert unchecked["code"] == "FILE_NOT_READY"


def test_planned_volume_is_taken_off_the_plan(crm):
    req, data, _, _ = structured(crm, mode="IMPORT", wave_kind="plan")
    with crm["sessions"]() as db:
        group_id = db.get(RequestItem, db.get(QuoteItem, data["selections"][0]["quote_item_id"])
                          .source_request_item_id).product_group_id
    plan = wave_row(crm, req["wave_id"])
    plan = cmd(crm, f"/waves/{plan['id']}/forecasts", {
        "version": plan["version"], "product_group_id": group_id, "target_quantity": "12", "unit_price_rub": "1000",
        "reason": "План поставки"}, method="put")
    assert Decimal(plan["financial_summary"]["forecast_quantity"]) == 12
    [forecast] = plan["forecasts"]
    removed = cmd(crm, f"/waves/{plan['id']}/forecasts/{forecast['id']}/remove",
                  {"version": plan["version"], "reason": "Поставщик закрыл план"})
    assert removed["forecasts"][0]["active"] is False
    assert Decimal(removed["financial_summary"].get("forecast_quantity", "0")) == 0
    again = cmd(crm, f"/waves/{plan['id']}/forecasts/{forecast['id']}/remove",
                {"version": removed["version"], "reason": "Ещё раз"}, status=409)
    assert again["code"] == "WAVE_FORECAST_REMOVED"


def test_a_position_moved_from_another_invoice_is_quoted_by_hand_in_a_few_clicks(crm):
    req, data, seller, _ = structured(crm)
    login(crm)
    other = post(crm, "/requests", {"title": "Перенос позиции", "client_id": req["client_id"]})
    with crm["sessions"].begin() as db:
        db.get(Request, other["id"]).seller_id = seller["id"]
    found = get(crm, f"/requests/{other['id']}/quote-candidates?name=samp")
    [product] = found["items"]
    assert (product["name"], product["article"], product["request_item_id"]) == ("Sample", "A123", None)
    last = product["packings"][0]["last"]
    assert (last["supplier_name"], last["unit_price"], last["currency_code"], last["delivery_days"]) == (
        "Aozeal", "1000.00000000", "RUB", 7)
    assert last["request_number"] == req["number"]
    assert get(crm, f"/requests/{other['id']}/quote-candidates?article=a12")["items"][0]["name"] == "Sample"
    assert get(crm, f"/requests/{other['id']}/quote-candidates?name=s")["items"] == []

    sheet = post(crm, f"/requests/{other['id']}/quote-sheets", {"supplier_id": last["supplier_id"], "items": [{
        "nomenclature_id": product["nomenclature_id"], "packing_id": product["packings"][0]["id"], "quantity": "3",
        "unit_price": "950", "currency_id": last["currency_id"], "delivery_days": 10}]})
    [quoted] = get(crm, f"/requests/{other['id']}/quote-items?all=true")["items"]
    assert (quoted["quote_number"], quoted["unit_price"], quoted["request_packings"]) == (
        sheet["number"], "950.00000000", "3")
    # The position is at once available to a calculation.
    calculation = cmd(crm, f"/requests/{other['id']}/calculations/preview", {
        **data, "request_version": get(crm, f"/requests/{other['id']}")["version"],
        "selections": [{"quote_item_id": quoted["id"], "markup_coefficient": "1.5"}]})
    assert calculation["snapshot"]["lines"][0]["quantity"] == "3"


def test_any_saved_calculation_is_copied_into_a_new_one(crm):
    req, data, seller, supplier = structured(crm)
    first = cmd(crm, f"/requests/{req['id']}/calculations", data)
    second = cmd(crm, f"/requests/{req['id']}/calculations", {
        **data, "selections": [{**data["selections"][0], "markup_coefficient": "2"}]})
    copied = {**data, "copy_of_id": first["id"], "previous_id": second["id"]}
    preview = cmd(crm, f"/requests/{req['id']}/calculations/preview", {**data, "copy_of_id": first["id"]})
    assert preview["snapshot"]["copy_of"]["number"] == first["snapshot"]["calculation_number"]
    saved = cmd(crm, f"/requests/{req['id']}/calculations", copied)
    snapshot = saved["snapshot"]
    assert snapshot["copy_of"] == {"id": first["id"], "number": first["snapshot"]["calculation_number"]}
    assert snapshot["source_label"] == f"Копия расчёта {first['snapshot']['calculation_number']}"
    assert snapshot["version_number"] == 3 and snapshot["base_version_id"] == second["id"]
    with crm["sessions"]() as db:
        quote = db.get(QuoteItem, data["selections"][0]["quote_item_id"])
        item_data = {"nomenclature_id": quote.nomenclature_id, "packing_id": quote.packing_id, "quantity": "1",
                     "unit_price": "1000", "currency_id": quote.currency_id, "delivery_days": 7}
    other, other_quote = peer_request(crm, seller, supplier, item_data, None)
    foreign = cmd(crm, f"/requests/{other['id']}/calculations/preview", {
        **data, "request_version": other["version"], "copy_of_id": first["id"],
        "selections": [{"quote_item_id": other_quote["id"], "markup_coefficient": "1.5"}]}, status=404)
    assert foreign["code"] == "CALCULATION_REQUIRED"


def test_requests_of_a_plan_are_gathered_into_its_working_wave(crm):
    req, data, seller, supplier = structured(crm, mode="IMPORT", wave_kind="plan")
    with crm["sessions"]() as db:
        quote = db.get(QuoteItem, data["selections"][0]["quote_item_id"])
        source = db.get(RequestItem, quote.source_request_item_id)
        group_id = source.product_group_id
        item_data = {"nomenclature_id": quote.nomenclature_id, "packing_id": quote.packing_id, "quantity": "1",
                     "unit_price": "1000", "currency_id": quote.currency_id, "delivery_days": 7}
    plan = wave_row(crm, req["wave_id"])
    plan = cmd(crm, f"/waves/{plan['id']}/forecasts", {
        "version": plan["version"], "product_group_id": group_id, "target_quantity": "4", "unit_price_rub": "1000",
        "reason": "План поставки"}, method="put")
    data["expenses"] = [LOGISTICS]
    first = cmd(crm, f"/requests/{req['id']}/calculations", data)
    _, execution = release(crm, req, first, "1")
    handoff(crm, req, execution)
    [work] = wave_row(crm, plan["id"])["working_waves"]

    peer, peer_quote = peer_request(crm, seller, supplier, item_data, plan["id"])
    payload = {"request_version": peer["version"], "profile_id": data["profile_id"], "delivery_days": 14,
               "selections": [{"quote_item_id": peer_quote["id"], "quantity": "2"}], "delivery_required": False,
               "expenses": [LOGISTICS]}
    on_plan = cmd(crm, f"/requests/{peer['id']}/calculations", payload)
    listed = get(crm, f"/waves/{work['id']}/plan-requests")["items"]
    # The first request stays planned in the plan; its order is already in the working wave.
    assert [(row["number"], row["orders"]) for row in listed] == [(req["number"], 1), (peer["number"], 0)]
    assert listed[1]["calculation_number"] == on_plan["snapshot"]["calculation_number"]

    work = wave_row(crm, work["id"])
    moved = cmd(crm, f"/waves/{work['id']}/take-plan-requests",
                {"version": work["version"], "request_ids": [peer["id"]]})
    assert moved["moved"] == [peer["number"]]
    assert get(crm, f"/requests/{peer['id']}")["wave_id"] == work["id"]
    assert [row["number"] for row in get(crm, f"/waves/{work['id']}/plan-requests")["items"]] == [req["number"]]
    saved = get(crm, f"/requests/{peer['id']}/calculations")["items"][0]
    assert saved["wave_moved"] is True

    # In the working wave the costs are shared with its order and with the rest of the plan: 1 + 2 + 1 = 4.
    peer = get(crm, f"/requests/{peer['id']}")
    gathered = cmd(crm, f"/requests/{peer['id']}/calculations/preview",
                   {**payload, "request_version": peer["version"]})["snapshot"]
    distribution = gathered["wave_distribution"]
    assert (Decimal(distribution["existing_quantity"]), Decimal(distribution["selected_quantity"]),
            Decimal(distribution["forecast_quantity"]), Decimal(distribution["total_quantity"])) == (1, 2, 1, 4)
    assert Decimal(gathered["totals"]["international_logistics"]) == 200
    refused = cmd(crm, f"/waves/{plan['id']}/take-plan-requests",
                  {"version": wave_row(crm, plan["id"])["version"], "request_ids": [req["id"]]}, status=422)
    assert refused["code"] == "WAVE_KIND"


def test_a_refusal_counts_as_an_answer_in_the_request_list(crm):
    req, _, _, supplier = structured(crm)
    other = post(crm, "/counterparties", {"name": "Shenzhen Sungening", "kind": "supplier"})
    with crm["sessions"].begin() as db:
        for supplier_id, replied in ((supplier["id"], None), (other["id"], utcnow())):
            db.add(SupplierMail(request_id=req["id"], supplier_id=supplier_id, recipient="rfq@example.com",
                                subject="RFQ", body="", html_body="", status="sent", sent_at=utcnow(),
                                replied_at=replied, author_id=crm["admin"].id))
    row = next(row for row in get(crm, "/requests")["items"] if row["id"] == req["id"])
    assert (row["answered_suppliers"], row["asked_suppliers"], row["quoted_suppliers"]) == (2, 2, 1)
    with crm["sessions"]() as db:
        assert db.scalar(select(SupplierMail.id).where(SupplierMail.replied_at.is_not(None)))
