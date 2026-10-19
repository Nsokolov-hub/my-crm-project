"""Business remarks of 19 October 2026: several statuses at once, a PO without freight, payments in
«Согласования», work and system notices, and waves finished by the leader or by their delivered orders."""

import io
from datetime import date, timedelta

from openpyxl import load_workbook
from sqlalchemy import select

from app.commerce.models import Wave
from app.core.models import Notification, PermissionGrant, User
from tests.support.business import cmd, handoff, release, structured, wave_row
from tests.support.crm import login, post

LOGISTICS = [{"name": "Международная логистика", "amount": "300", "currency": "RUB", "scope": "WAVE",
              "stage": "INTERNATIONAL_LOGISTICS", "method": "BY_QUANTITY"}]


def ordered(crm, wave_kind="work"):
    """An import request whose approved position is handed to procurement and ordered from the supplier."""
    req, data, seller, supplier = structured(crm, mode="IMPORT", wave_kind=wave_kind)
    wave = wave_row(crm, req["wave_id"])
    cmd(crm, f"/waves/{wave['id']}/budget", {"version": wave["version"], "profile_id": data["profile_id"],
                                             "expenses": LOGISTICS, "reason": "Бюджет волны"}, method="put")
    data["expenses"] = LOGISTICS
    calculation = cmd(crm, f"/requests/{req['id']}/calculations", data)
    _, execution = release(crm, req, calculation, "1")
    handoff(crm, req, execution)
    return req, seller, execution


def create_order(crm, seller, execution, **extra):
    return cmd(crm, "/supplier-orders", {
        "execution_ids": [execution["id"]], "seller_id": seller["id"],
        "expected_date": (date.today() + timedelta(days=21)).isoformat(), **extra,
    })


def lines(crm, **params):
    response = crm["client"].get("/api/v1/supplier-orders/positions", params={"ordered": "true", **params})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def refs(rows):
    return [{"id": row["id"], "version": row["version"]} for row in rows]


def person(crm, email, *codes):
    with crm["sessions"].begin() as db:
        user = User(email=email, name=email.split("@")[0], password_hash=crm["admin"].password_hash)
        db.add(user)
        db.flush()
        db.add_all(PermissionGrant(user_id=user.id, code=code, scope="all") for code in codes)
        return user.id


def test_order_paid_in_a_consolidation_carries_no_logistics_and_statuses_filter_together(crm):
    req, seller, execution = ordered(crm)
    order = create_order(crm, seller, execution, logistics_separately=True, logistics_amount="300")
    assert order["logistics_amount"] == "0" and order["logistics_separately"] is True
    assert order["logistics_basis"] == "Оплачивается отдельно"
    pdf = crm["client"].get(f"/api/v1/supplier-orders/{order['id']}/po.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    values = [cell.value for row in load_workbook(io.BytesIO(crm["client"].get(
        f"/api/v1/supplier-orders/{order['id']}/po.xlsx").content)).active for cell in row]
    assert "International logistics: paid separately for the consolidated shipment" in values

    # The wave logistics stays free for the consolidated order that pays it.
    hint = crm["client"].get("/api/v1/supplier-orders/logistics", params={"execution_ids": [execution["id"]]}).json()
    assert hint["amount"] == "300"

    line = lines(crm)[0]
    cmd(crm, "/supplier-order-lines/status", {"lines": refs([line]), "status": "delivery"})
    assert [row["status"] for row in lines(crm, status="waiting,delivery,arrived_ru")] == ["delivery"]
    assert lines(crm, status="arrived_ru,cancelled") == []
    orders = crm["client"].get("/api/v1/supplier-orders", params={"status": "delivery,arrived_ru"}).json()["items"]
    assert [row["number"] for row in orders] == [order["number"]]


def test_po_can_leave_out_the_freight_of_an_order_that_carries_it(crm):
    req, seller, execution = ordered(crm)
    order = create_order(crm, seller, execution)
    assert order["logistics_amount"] == "300"
    book = load_workbook(io.BytesIO(crm["client"].get(
        f"/api/v1/supplier-orders/{order['id']}/po.xlsx", params={"without_logistics": "true"}).content)).active
    values = [cell.value for row in book for cell in row]
    assert "International logistics" not in values
    assert "International logistics: paid separately for the consolidated shipment" in values
    assert max(value for value in values if isinstance(value, (int, float))) == 1000


def test_declared_payment_waits_in_approvals_and_asks_the_confirmer(crm):
    req, data, seller, _ = structured(crm)
    finance = person(crm, "finance@example.com", "requests.read", "payments.confirm")
    payment = cmd(crm, f"/requests/{req['id']}/payments", {
        "amount": "150000", "currency": "RUB", "payment_date": date.today().isoformat(), "number": "115"})
    waiting = crm["client"].get("/api/v1/payments", params={"status": "declared"}).json()["items"]
    assert [(row["id"], row["request_number"], row["client_name"]) for row in waiting] == [
        (payment["id"], req["number"], "Рабочий клиент")]
    with crm["sessions"]() as db:
        notice = db.scalar(select(Notification).where(Notification.user_id == finance))
        assert notice.event_key == f"payment-declared:{payment['id']}" and notice.entity_type == "payment"
        assert db.scalar(select(Notification).where(Notification.user_id == crm["admin"].id,
                                                    Notification.event_key.like("payment-declared:%"))) is None
    cmd(crm, f"/payments/{payment['id']}/confirm", {"version": payment["version"]})
    assert crm["client"].get("/api/v1/payments", params={"status": "declared"}).json()["items"] == []
    login(crm, "finance@example.com")
    work = crm["client"].get("/api/v1/notifications", params={"category": "work"}).json()
    assert work["unread_work"] == 1 and work["items"][0]["category"] == "work"


def test_system_notices_do_not_ring_the_bell_and_can_all_be_read(crm):
    login(crm)
    with crm["sessions"].begin() as db:
        owner = crm["admin"].id
        db.add_all([
            Notification(user_id=owner, event_key="supplier-mail-sent:m1", title="Запрос поставщику отправлен",
                         entity_type="file", entity_id="m1"),
            Notification(user_id=owner, event_key="file-verdict:f1", title="Файл проверен и доступен",
                         entity_type="file", entity_id="f1"),
            Notification(user_id=owner, event_key="mention:x:1", title="Вас упомянули в обсуждении",
                         entity_type="file", entity_id="x"),
        ])
    everything = crm["client"].get("/api/v1/notifications").json()
    assert (everything["unread"], everything["unread_work"], everything["unread_system"]) == (1, 1, 2)
    system = crm["client"].get("/api/v1/notifications", params={"category": "system"}).json()
    assert {row["event_key"] for row in system["items"]} == {"supplier-mail-sent:m1", "file-verdict:f1"}
    assert post(crm, "/notifications/read-all", {"category": "system"}, status=200)["count"] == 2
    after = crm["client"].get("/api/v1/notifications").json()
    assert (after["unread_work"], after["unread_system"]) == (1, 0)


def test_wave_finishes_by_itself_when_every_order_is_delivered_to_its_client(crm):
    req, seller, execution = ordered(crm)
    create_order(crm, seller, execution)
    wave_id = req["wave_id"]
    assert wave_row(crm, wave_id)["order_lines"] == {"waiting": 1}
    line = lines(crm)[0]
    moved = cmd(crm, "/supplier-order-lines/status", {"lines": refs([line]), "status": "arrived_ru"})
    assert moved["finished_waves"] == []
    delivered = cmd(crm, "/supplier-order-lines/status", {"lines": refs(lines(crm)), "status": "delivered"})
    assert delivered["finished_waves"] == [wave_id]
    wave = wave_row(crm, wave_id)
    assert (wave["status"], wave["archived"], wave["order_lines"]) == ("completed", True, {"delivered": 1})


def test_only_a_leader_moves_the_wave_state_and_marks_the_delivery_done(crm):
    req, seller, execution = ordered(crm, wave_kind="plan")
    plan = wave_row(crm, req["wave_id"])
    working = plan["working_waves"][0]
    current = wave_row(crm, working["id"])
    logistician = person(crm, "logist@example.com", "requests.read", "waves.write")
    login(crm, "logist@example.com")
    refused = cmd(crm, f"/waves/{working['id']}", {"version": current["version"], "status": "closed",
                                                   "reason": "Собрали заказ"}, status=403, method="patch")
    assert refused["code"] == "WAVE_STATUS_LEADER"
    moved = cmd(crm, f"/waves/{working['id']}", {"version": current["version"], "reason": "Сдвиг недели",
                                                 "arrival_week": current["arrival_week"],
                                                 "arrival_year": current["arrival_year"]}, method="patch")
    assert moved["status"] == current["status"]
    assert cmd(crm, f"/waves/{plan['id']}/complete", {"version": plan["version"]}, status=403)["code"] == "FORBIDDEN"

    login(crm)
    plan = wave_row(crm, plan["id"])
    done = cmd(crm, f"/waves/{plan['id']}/complete", {"version": plan["version"], "reason": "Поставка выполнена"})
    assert done["archived"] is True
    with crm["sessions"]() as db:
        work = db.get(Wave, working["id"])
        assert (work.status, work.archived) == ("completed", True)
    again = cmd(crm, f"/waves/{plan['id']}/complete", {"version": done["version"]}, status=409)
    assert again["code"] == "WAVE_ARCHIVE_STATE"
    assert logistician
