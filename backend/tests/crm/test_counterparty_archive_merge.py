"""Business remark of 13 October: an archive for clients and suppliers, and merging duplicate clients."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from app.communication.models import FileRecord
from app.crm.models import Counterparty, CounterpartyDocument, Request, Task
from tests.support.business import request, structured
from tests.support.crm import login, post


def command(crm, path, body, status=200):
    return post(crm, path, {**body, "idempotency_key": str(uuid4())}, status)


def archive(crm, counterparty, archived=True, status=200):
    return command(crm, f"/counterparties/{counterparty['id']}/archive",
                   {"version": counterparty["version"], "archived": archived, "reason": "Проверка архива"}, status)


def merge(crm, duplicate, target_id, status=200, key=None):
    body = {"version": duplicate["version"], "target_id": target_id, "reason": "Дубль из импорта",
            "idempotency_key": key or str(uuid4())}
    return post(crm, f"/counterparties/{duplicate['id']}/merge", body, status)


def current(crm, counterparty_id):
    return crm["client"].get(f"/api/v1/counterparties/{counterparty_id}").json()


def test_a_duplicate_hands_its_records_to_the_main_card_and_stays_in_the_archive(crm):
    login(crm)
    req = request(crm)
    duplicate = current(crm, req["client_id"])
    main = post(crm, "/counterparties", {"name": "ООО Вектор", "tax_id": "7708392654"})
    with crm["sessions"].begin() as db:
        row = db.get(Counterparty, duplicate["id"])
        row.email, row.phone, row.details = "info@vector.example", "+7 495 000-00-00", {"city": "Москва"}
    contact = post(crm, f"/counterparties/{duplicate['id']}/contacts", {"name": "Иван Петров"})
    call = post(crm, "/calls", {"client_id": duplicate["id"], "result": "awaiting_request",
                                "next_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()})
    document = crm["client"].post(f"/api/v1/counterparties/{duplicate['id']}/documents",
                                  files={"file": ("contract.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
                                  data={"category": "contract"}, headers={"Idempotency-Key": str(uuid4())})
    assert document.status_code == 201, document.text

    key, before = str(uuid4()), current(crm, duplicate["id"])
    merged = merge(crm, before, main["id"], key=key)
    assert merged["id"] == main["id"]
    assert merged["moved"] == {"requests": 1, "contacts": 1, "calls": 1, "documents": 1, "tasks": 1}
    # The main card keeps its own data and takes what it lacked.
    assert (merged["tax_id"], merged["email"], merged["phone"]) == ("7708392654", "info@vector.example",
                                                                    "+7 495 000-00-00")
    assert merged["details"]["city"] == "Москва"
    # A repeated command returns the first result.
    assert merge(crm, before, main["id"], key=key) == merged

    assert crm["client"].get(f"/api/v1/requests/{req['id']}").json()["client_id"] == main["id"]
    contacts = crm["client"].get(f"/api/v1/counterparties/{main['id']}/contacts").json()["items"]
    assert [row["id"] for row in contacts] == [contact["id"]]
    calls = crm["client"].get(f"/api/v1/calls?client_id={main['id']}").json()["items"]
    assert [row["id"] for row in calls] == [call["id"]]
    with crm["sessions"]() as db:
        assert db.scalar(select(CounterpartyDocument.counterparty_id)) == main["id"]
        assert db.scalar(select(FileRecord.client_id).where(FileRecord.client_id.is_not(None))) == main["id"]
        assert db.scalar(select(Task.entity_id).where(Task.entity_type == "counterparty")) == main["id"]

    old = current(crm, duplicate["id"])
    assert old["archived"] is True and old["merged_into_id"] == main["id"]
    assert old["merged_into_name"] == "ООО Вектор"
    names = {row["name"] for row in crm["client"].get("/api/v1/counterparties").json()["items"]}
    assert "ООО Вектор" in names and duplicate["name"] not in names
    archived = crm["client"].get("/api/v1/counterparties?archived=true").json()["items"]
    assert [(row["id"], row["merged_into_name"]) for row in archived] == [(duplicate["id"], "ООО Вектор")]
    restored = archive(crm, old, archived=False, status=409)
    assert restored["code"] == "COUNTERPARTY_MERGED" and "ООО Вектор" in restored["message"]


def test_merge_keeps_the_roles_supplier_history_and_access_of_the_cards(crm):
    login(crm)
    client = post(crm, "/counterparties", {"name": "Клиент"})
    supplier = post(crm, "/counterparties", {"name": "Поставщик", "kind": "supplier"})
    assert merge(crm, client, client["id"], 422)["field"] == "target_id"
    assert "клиентом" in merge(crm, client, supplier["id"], 422)["message"]
    archived = post(crm, "/counterparties", {"name": "Закрытый"})
    archive(crm, archived)
    assert "архиве" in merge(crm, client, archived["id"], 422)["message"]

    _, _, _, quoting = structured(crm)
    other_supplier = post(crm, "/counterparties", {"name": "Другой поставщик", "kind": "supplier"})
    refused = merge(crm, current(crm, quoting["id"]), other_supplier["id"], 409)
    assert refused["code"] == "MERGE_SUPPLIER_HISTORY"

    # Merging moves requests between clients, so it is a decision of someone who may reassign them.
    login(crm, "manager@example.com")
    own = post(crm, "/counterparties", {"name": "Свой дубль"})
    own_main = post(crm, "/counterparties", {"name": "Свой основной"})
    assert merge(crm, own, own_main["id"], 403)["code"] == "FORBIDDEN"


def test_a_client_leaves_for_the_archive_with_its_finished_requests_and_returns_with_them(crm):
    login(crm)
    req = request(crm)
    client = current(crm, req["client_id"])
    refused = archive(crm, client, status=409)
    assert refused["code"] == "COUNTERPARTY_IN_WORK" and req["number"] in refused["message"]

    earlier = post(crm, "/requests", {"title": "Отменённая раньше", "client_id": client["id"]})
    with crm["sessions"].begin() as db:
        db.get(Request, req["id"]).commercial_stage = "closed_lost"
        db.get(Request, earlier["id"]).archived = True
    archived = archive(crm, current(crm, client["id"]))
    assert archived["archived"] is True
    assert crm["client"].get("/api/v1/requests").json()["total"] == 0
    assert post(crm, "/requests", {"title": "Новая", "client_id": client["id"]}, 422)

    restored = archive(crm, archived, archived=False)
    assert restored["archived"] is False
    with crm["sessions"]() as db:
        assert (db.get(Request, req["id"]).archived, db.get(Request, req["id"]).archived_with_client) == (False, False)
        # A request archived on its own stays in the archive.
        assert db.get(Request, earlier["id"]).archived is True
    assert archive(crm, restored, archived=False, status=409)["code"] == "ARCHIVE_STATE"


def test_a_supplier_with_a_wave_under_way_stays_out_of_the_archive(crm):
    _, _, _, supplier = structured(crm, mode="IMPORT")
    refused = archive(crm, current(crm, supplier["id"]), status=409)
    assert refused["code"] == "COUNTERPARTY_IN_WORK" and "волна" in refused["message"]
