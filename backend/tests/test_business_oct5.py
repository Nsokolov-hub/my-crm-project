"""Business acceptance: split customs fees, review gates, scopes and generated orders."""

import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from openpyxl import load_workbook
from sqlalchemy import select
from test_business_imports import request
from test_crm import crm, login, post  # noqa: F401
from test_itemized_wave_calculation import _selection

from app.business.models import SupplierMail, WorkflowReview
from app.business.worker import reminders, send_mail
from app.commerce.files import party_details, payment_bank
from app.commerce.itemized import calculate_itemized, itemized_profile
from app.commerce.models import CalculationProfile
from app.core.models import Notification, OutboxEvent, PermissionGrant, User
from app.crm.models import Country, Currency, Request, Task


def configured_profile():
    profile = itemized_profile()
    profile["import_country_id"] = "country-test"
    return profile


def cmd(env, path, body=None, status=200, method="post", key=None):
    result = getattr(env["client"], method)(
        "/api/v1" + path, json={**(body or {}), "idempotency_key": key or str(uuid4())}
    )
    assert result.status_code == status, result.text
    return result.json()


@pytest.mark.parametrize(
    "basis,fee",
    [
        ("200000", "1231"),
        ("200000.01", "2462"),
        ("450000", "2462"),
        ("450000.01", "4924"),
        ("1200000.01", "13541"),
        ("2700000.01", "18465"),
        ("4200000.01", "21344"),
        ("5500000.01", "49240"),
        ("10000000.01", "73860"),
    ],
)
def test_fee_scale_is_based_on_customs_value(basis, fee):
    row = _selection("normal", "reference_standards", "1", basis, "1")
    row["currency_code"] = "RUB"
    result = calculate_itemized(configured_profile(), [row], [], [])
    assert Decimal(result["totals"]["customs_fee_1"]) == Decimal(fee)
    assert Decimal(result["totals"]["customs_fee_2"]) == 0
    assert Decimal(result["totals"]["duty"]) == (Decimal(basis) * Decimal(".05")).quantize(Decimal(".01"))


def test_columns_are_separate_from_progressive_fees_and_strains_duty():
    rows = [
        _selection("a", "columns", "2", "10000", "1"),
        _selection("b", "columns", "1", "10000000", "1"),
        _selection("c", "reference_standards", "2", "50000", "1"),
        _selection("d", "strains", "1", "1000", "1"),
    ]
    for row in rows:
        row["currency_code"] = "RUB"
    result = calculate_itemized(configured_profile(), rows, [], [])
    a, b, c, d = result["lines"]
    assert Decimal(a["detail"]["duty"]) == Decimal(b["detail"]["duty"]) == 0
    assert Decimal(a["detail"]["customs_fee_2"]) == 49200
    assert Decimal(b["detail"]["customs_fee_2"]) == 24600
    assert Decimal(c["detail"]["customs_fee_2"]) == 0
    assert Decimal(d["detail"]["duty"]) == 120
    assert Decimal(result["wave_distribution"]["customs_fee"]) == 73800 + 1231
    assert sum(Decimal(r["detail"]["customs_fee"]) for r in result["lines"]) == Decimal(
        result["totals"]["customs_fee"]
    )
    assert "E+" not in a["detail"]["customs_fee_per_unit"]


@pytest.mark.parametrize("mode,net,cost", [("DAP", "2650.00", "1325.00"), ("RUSSIA", "2100.00", "1100.00")])
def test_supplier_modes_and_expenses(mode, net, cost):
    row = _selection("a", "reference_standards", "1", "1000", "1")
    row.update(currency_code="RUB", calculation_type=mode, markup_coefficient="2")
    expense = {
        "name": "Дополнительный расход",
        "amount": "44" if mode == "DAP" else "100",
        "currency": "RUB",
        "method": "BY_QUANTITY",
        "scope": "REQUEST",
        "stage": "GENERAL",
    }
    result = calculate_itemized(configured_profile(), [row], [expense], [])
    assert Decimal(result["totals"]["customs_fee"]) == Decimal(result["totals"]["import_vat"]) == 0
    assert Decimal(result["totals"]["sale_net"]) == Decimal(net)
    assert Decimal(result["totals"]["cost"]) == Decimal(cost)
    assert result["totals"]["profitability_percent"] == result["totals"]["margin_percent"]
    assert Decimal(result["totals"]["cost_profitability_percent"]) > Decimal(result["totals"]["profitability_percent"])
    assert result["totals"]["markup_amount"] == result["totals"]["profit"]


def structured(env, mode="RUSSIA", markup="1.5", owner=None):
    login(env)
    req = request(env)
    seller = post(
        env,
        "/sellers",
        {
            "name": "ОГК-ХИМ",
            "currency": "RUB",
            "details": {"Банк": "Банк A", "БИК": "123456789", "Расчётный счёт": "40700000000000000000"},
        },
    )
    supplier = post(
        env,
        "/counterparties",
        {
            "name": "Aozeal",
            "kind": "supplier",
            "email": "sales@aozeal.example",
            "details": {"calculation_type": mode, "contract": "C-123"},
        },
    )
    group = post(env, "/product-groups", {"name": "Стандартные образцы", "slug": "reference_standards"})
    n = post(
        env,
        "/nomenclatures",
        {
            "name": "Sample",
            "article": "A123",
            "manufacturer": "Aozeal",
            "product_group_id": group["id"],
            "packings": [{"value": "100", "unit": "mg"}],
        },
    )
    with env["sessions"].begin() as db:
        db.get(Request, req["id"]).seller_id = seller["id"]
        if owner:
            db.get(Request, req["id"]).owner_id = owner
        country = Country(name="Россия", iso2="RU")
        db.add(country)
        db.flush()
        profile = itemized_profile()
        profile["import_country_id"] = country.id
        saved = CalculationProfile(
            name="2026",
            status="published",
            effective_from=date(2026, 1, 1),
            definition=profile,
            reason="Тест",
            author_id=env["admin"].id,
        )
        db.add(saved)
        db.flush()
        profile_id = saved.id
        currency_id = db.scalar(select(Currency.id).where(Currency.code == "RUB"))
    quote = post(
        env,
        f"/requests/{req['id']}/quote-sheets",
        {
            "supplier_id": supplier["id"],
            "items": [
                {
                    "nomenclature_id": n["id"],
                    "packing_id": n["packings"][0]["id"],
                    "quantity": "1",
                    "unit_price": "1000",
                    "currency_id": currency_id,
                    "delivery_days": 7,
                }
            ],
        },
    )
    req = env["client"].get("/api/v1/requests/" + req["id"]).json()
    if mode == "IMPORT":
        wave = cmd(
            env,
            "/waves",
            {
                "supplier_id": supplier["id"],
                "route": "Москва",
                "origin_country": "RU",
                "owner_id": env["admin"].id,
                "close_date": date.today().isoformat(),
                "departure_date": date.today().isoformat(),
                "arrival_date": (date.today() + timedelta(days=7)).isoformat(),
            },
        )
        req = {
            **req,
            **cmd(
                env,
                f"/requests/{req['id']}/wave",
                {"request_version": req["version"], "wave_id": wave["id"]},
                method="put",
            ),
        }
    data = {
        "request_version": req["version"],
        "profile_id": profile_id,
        "selections": [{"quote_item_id": quote["items"][0]["id"], "markup_coefficient": markup}],
        "expenses": [],
        "delivery_required": False,
    }
    return req, data, seller, supplier


def test_domestic_calculation_order_export_and_calendar_source(crm):  # noqa: F811
    req, data, seller, supplier = structured(crm)
    assert req["commercial_stage"] == "quote_given"
    calculation = cmd(crm, f"/requests/{req['id']}/calculations", data)
    assert calculation["snapshot"]["vat_deductible"] is True
    assert calculation["snapshot"]["delivery_days"] == 7
    proposal = cmd(
        crm,
        f"/requests/{req['id']}/proposals",
        {
            "calculation_id": calculation["id"],
            "valid_until": (date.today() + timedelta(days=7)).isoformat(),
            "terms": "Отсрочка",
        },
    )
    accepted = cmd(
        crm,
        f"/proposals/{proposal['id']}/accept",
        {
            "version": proposal["version"],
            "reason": "Клиент подтвердил",
            "lines": [{"line_id": calculation["snapshot"]["lines"][0]["line_id"], "quantity": "1"}],
        },
    )
    assert crm["client"].get("/api/v1/supplier-orders/positions").json()["items"] == []
    review = cmd(crm, f"/requests/{req['id']}/approvals", {
        "execution_ids": [accepted["executions"][0]["id"]], "reviewer_id": crm["admin"].id,
    })
    cmd(crm, f"/approvals/{review['id']}/decision", {"version": review["version"], "decision": "approved"})
    positions = crm["client"].get("/api/v1/supplier-orders/positions").json()["items"]
    assert positions[0]["manufacturer"] == "Aozeal"
    assert positions[0]["article"] == "A123"
    order = cmd(
        crm,
        "/supplier-orders",
        {
            "execution_ids": [positions[0]["id"]],
            "seller_id": seller["id"],
            "expected_date": (date.today() + timedelta(days=7)).isoformat(),
            "prices": {positions[0]["id"]: "990"},
        },
    )
    assert order["number"].startswith("AOZEA")
    assert Decimal(order["total"]) == 990
    assert not crm["client"].get("/api/v1/supplier-orders/positions").json()["items"]
    sheet = load_workbook(
        io.BytesIO(crm["client"].get(f"/api/v1/supplier-orders/{order['id']}/po.xlsx").content)
    ).active
    assert sheet["D1"].value == order["number"]
    assert sheet["F13"].value == 990
    expense = cmd(
        crm,
        "/payment-calendar",
        {
            "direction": "expense",
            "planned_date": date.today().isoformat(),
            "amount": order["total"],
            "purpose": order["number"],
            "supplier_order_id": order["id"],
            "outside_payment_days": True,
        },
    )
    assert expense["status"] == "draft"
    assert accepted


def test_low_markup_gate_reaches_all_leaders_and_cannot_issue_before_approval(crm):  # noqa: F811
    with crm["sessions"].begin() as db:
        for code in (
            "calculations.write",
            "finance.purchase.read",
            "finance.calculations.read",
            "finance.reward.read",
            "finance.profit.read",
            "documents.write",
        ):
            db.add(PermissionGrant(user_id=crm["manager"].id, code=code, scope="own"))
        leader = User(
            name="Второй руководитель", email="leader@crm.test", password_hash=crm["admin"].password_hash
        )
        db.add(leader)
        db.flush()
        for code in ("approvals.decide", "requests.read"):
            db.add(PermissionGrant(user_id=leader.id, code=code, scope="all"))
        leader_id = leader.id
    req, data, _, _ = structured(crm, markup="1.25", owner=crm["manager"].id)
    login(crm, "manager@example.com")
    calculation = cmd(crm, f"/requests/{req['id']}/calculations", data)
    body = {
        "calculation_id": calculation["id"],
        "valid_until": (date.today() + timedelta(days=7)).isoformat(),
        "terms": "Предоплата",
    }
    assert (
        cmd(crm, f"/requests/{req['id']}/proposals", body, status=422)["code"]
        == "CALCULATION_APPROVAL_REQUIRED"
    )
    with crm["sessions"]() as db:
        review = db.scalar(select(WorkflowReview).where(WorkflowReview.entity_id == calculation["id"]))
        assert {
            n.user_id for n in db.scalars(select(Notification).where(Notification.entity_id == review.id))
        } == {crm["admin"].id, leader_id}
    login(crm)
    cmd(
        crm,
        f"/workflow-approvals/{review.id}/decision",
        {"version": review.version, "decision": "approved", "reason": "Разовая продажа"},
    )
    login(crm, "manager@example.com")
    assert cmd(crm, f"/requests/{req['id']}/proposals", body)["status"] == "issued"


def test_calendar_review_currency_summary_recurrence_and_reminders(crm):  # noqa: F811
    login(crm, "manager@example.com")
    row = cmd(
        crm,
        "/payment-calendar",
        {
            "direction": "income",
            "planned_date": "2026-01-31",
            "amount": "100.25",
            "currency": "RUB",
            "purpose": "Аренда",
            "recurrence": "monthly",
            "payment_kind": "prepayment",
        },
    )
    assert cmd(crm, f"/payment-calendar/{row['id']}/confirm", {"version": row["version"]}, status=403)["code"]
    row = cmd(crm, f"/payment-calendar/{row['id']}/submit", {"version": row["version"]})
    login(crm)
    review = crm["client"].get("/api/v1/workflow-approvals").json()["items"][0]
    row = crm["client"].get("/api/v1/payment-calendar").json()["items"][0]
    with crm["sessions"].begin() as db:
        reminders(db, datetime(2026, 2, 1, 10, tzinfo=timezone.utc))
        reminders(db, datetime(2026, 2, 1, 11, tzinfo=timezone.utc))
        assert (
            len(db.scalars(select(Notification).where(Notification.entity_type == "calendar_entry")).all())
            == 2
        )
    cmd(crm, f"/workflow-approvals/{review['id']}/decision", {
        "version": review["version"], "decision": "approved", "reason": "Оплата согласована", "actual_date": "2026-01-31",
    })
    row = next(item for item in crm["client"].get("/api/v1/payment-calendar").json()["items"] if item["id"] == row["id"])
    assert row["status"] == "confirmed"
    result = crm["client"].get("/api/v1/payment-calendar").json()
    assert result["summary"][0]["income"] == "100.25000000"
    assert result["summary"][0]["prepayment_count"] == 1
    assert {r["planned_date"] for r in result["items"]} == {"2026-01-31", "2026-02-28"}
    cmd(crm, f"/payment-calendar/{row['id']}/confirm", {"version": row["version"]}, status=422)


def test_overdue_close_requires_review_and_absence_blocks_assignment(crm):  # noqa: F811
    login(crm)
    now = datetime.now(timezone.utc)
    old = post(
        crm,
        "/tasks",
        {
            "title": "Позвонить",
            "assignee_id": crm["manager"].id,
            "due_at": (now - timedelta(days=2)).isoformat(),
        },
    )
    login(crm, "manager@example.com")
    response = crm["client"].patch(
        "/api/v1/tasks/" + old["id"],
        json={"version": old["version"], "status": "completed", "result": "Позвонил"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completion_pending"
    login(crm)
    review = crm["client"].get("/api/v1/workflow-approvals").json()["items"][0]
    cmd(
        crm,
        f"/workflow-approvals/{review['id']}/decision",
        {"version": review["version"], "decision": "approved", "reason": "Результат принят"},
    )
    with crm["sessions"]() as db:
        assert db.get(Task, old["id"]).status == "completed"
    open_task = post(
        crm,
        "/tasks",
        {
            "title": "Отправить КП",
            "assignee_id": crm["manager"].id,
            "due_at": (now + timedelta(days=2)).isoformat(),
        },
    )
    absence = cmd(
        crm,
        "/employee-absences",
        {
            "user_id": crm["manager"].id,
            "starts_at": (now - timedelta(hours=1)).isoformat(),
            "ends_at": (now + timedelta(days=3)).isoformat(),
            "reason": "Больничный",
        },
    )
    assert absence["postponed_tasks"] == 1
    post(
        crm,
        "/tasks",
        {
            "title": "Новая задача",
            "assignee_id": crm["manager"].id,
            "due_at": (now + timedelta(days=1)).isoformat(),
        },
        status=422,
    )
    with crm["sessions"]() as db:
        assert db.get(Task, open_task["id"]).due_at > (now + timedelta(days=6)).replace(tzinfo=None)


def test_own_only_overrides_all_and_shared_request_access(crm):  # noqa: F811
    login(crm)
    other_request = request(crm)
    with crm["sessions"].begin() as db:
        user = db.get(User, crm["manager"].id)
        user.own_requests_only = True
        db.scalar(
            select(PermissionGrant).where(
                PermissionGrant.user_id == user.id, PermissionGrant.code == "requests.read"
            )
        ).scope = "all"
    login(crm, "manager@example.com")
    assert crm["client"].get("/api/v1/requests/" + other_request["id"]).status_code == 404
    assert not crm["client"].get("/api/v1/requests").json()["items"]
    with crm["sessions"].begin() as db:
        notification = Notification(
            user_id=crm["manager"].id,
            event_key="foreign-request",
            title="Чужая заявка",
            entity_type="request",
            entity_id=other_request["id"],
        )
        db.add(notification)
        db.flush()
        notification_id = notification.id
    visible = crm["client"].get("/api/v1/notifications?read=false").json()
    assert not visible["items"]
    assert visible["unread"] == 0
    post(crm, f"/notifications/{notification_id}/read", {}, status=404)


def test_invoice_legal_details_and_bank_selection_exclude_service_data():
    party = {
        "name": "Клиент",
        "tax_id": "123",
        "details": {
            "city": "Москва",
            "profile": "Завод",
            "revenue": "187000",
            "Юридический адрес": "Москва, дом 1",
            "КПП": "456",
        },
    }
    assert "revenue" not in party_details(party)
    assert "Завод" not in party_details(party)
    assert "Москва, дом 1" in party_details(party)
    bank = payment_bank(
        {"seller": {"name": "ОГК-ХИМ"}, "bank_details": {"Банк": "Сбербанк", "Расчётный счёт": "407"}}
    )
    assert bank["Банк получателя"] == "Сбербанк"
    assert bank["Получатель"] == "ОГК-ХИМ"


def test_supplier_mail_preview_is_inline_and_worker_sends_separate_messages(crm, monkeypatch):  # noqa: F811
    req, _, _, supplier = structured(crm)
    # The separate quote is deliberately hidden from the customer's original request list.
    with crm["sessions"]() as db:
        from app.crm.models import RequestItem

        item = db.scalar(select(RequestItem).where(RequestItem.request_id == req["id"]))
        item_id = item.id
    preview = cmd(
        crm,
        f"/requests/{req['id']}/supplier-mail/preview",
        {"item_ids": [item_id], "supplier_ids": [supplier["id"]], "introduction": "Hello <supplier>"},
    )
    assert "<table" in preview["items"][0]["table_html"]
    assert "Aozeal" in preview["items"][0]["body"]
    from app.core.config import settings

    monkeypatch.setattr(settings, "smtp_host", "smtp.example")
    monkeypatch.setattr(settings, "smtp_from", "crm@example.com")
    messages = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self):
            pass

        def send_message(self, message):
            messages.append(message)

    monkeypatch.setattr("app.business.worker.smtplib.SMTP", SMTP)
    queued = cmd(
        crm,
        f"/requests/{req['id']}/supplier-mail/send",
        {"item_ids": [item_id], "supplier_ids": [supplier["id"]], "subject": "Request sample"},
    )
    with crm["sessions"].begin() as db:
        event = db.scalar(select(OutboxEvent).where(OutboxEvent.kind == "supplier.mail"))
        send_mail(db, event)
        send_mail(db, event)
        assert db.get(SupplierMail, queued["items"][0]["id"]).status == "sent"
    assert len(messages) == 1
    assert messages[0].get_content_type() == "multipart/alternative"
    assert len(list(messages[0].iter_attachments())) == 0


def test_delivery_auto_city_vat_and_disable_removes_previous_auto_expenses(crm):  # noqa: F811
    req, data, _, _ = structured(crm)
    with crm["sessions"].begin() as db:
        from app.crm.models import Counterparty

        db.get(Counterparty, req["client_id"]).details = {
            "Юридический адрес": "630000, г. Новосибирск, ул. Ленина, 1"
        }
    result = cmd(crm, f"/requests/{req['id']}/calculations/preview", {**data, "delivery_required": True})[
        "snapshot"
    ]
    shipping = next(r for r in result["resolved_expenses"] if r["name"].startswith("Доставка СДЭК"))
    assert Decimal(shipping["amount"]) == 10625
    assert Decimal(result["totals"]["cash_need"]) - Decimal(result["totals"]["cost"]) == Decimal("2337.50")
    disabled = cmd(
        crm, f"/requests/{req['id']}/calculations/preview", {**data, "expenses": result["resolved_expenses"]}
    )["snapshot"]
    assert disabled["resolved_expenses"] == []
    assert Decimal(disabled["totals"]["cost"]) == 1000


def test_actual_wave_preserves_sale_and_reallocates_changed_shared_budget(crm):  # noqa: F811
    from app.commerce.models import Execution

    req, data, _, _ = structured(crm, mode="IMPORT")
    data.update(delivery_required=True, delivery_city="Москва")
    data["expenses"] = [
        {
            "name": "Логистика",
            "amount": "100",
            "currency": "RUB",
            "method": "BY_QUANTITY",
            "scope": "WAVE",
            "stage": "INTERNATIONAL_LOGISTICS",
        },
        {"name": "Курьер по Москве", "amount": "200", "currency": "RUB",
         "method": "BY_QUANTITY", "scope": "REQUEST", "stage": "CLIENT_DELIVERY"},
    ]
    calculation = cmd(crm, f"/requests/{req['id']}/calculations", data)
    proposal = cmd(
        crm,
        f"/requests/{req['id']}/proposals",
        {
            "calculation_id": calculation["id"],
            "valid_until": (date.today() + timedelta(days=7)).isoformat(),
            "terms": "Предоплата",
        },
    )
    accepted = cmd(
        crm,
        f"/proposals/{proposal['id']}/accept",
        {
            "version": proposal["version"],
            "reason": "Принято",
            "lines": [{"line_id": calculation["snapshot"]["lines"][0]["line_id"], "quantity": "1"}],
        },
    )
    execution = accepted["executions"][0]
    approval = cmd(
        crm,
        f"/requests/{req['id']}/approvals",
        {"execution_ids": [execution["id"]], "reviewer_id": crm["admin"].id},
    )
    cmd(
        crm,
        f"/approvals/{approval['id']}/decision",
        {"version": approval["version"], "decision": "approved", "reason": "Подтверждённый заказ"},
    )
    assert crm["client"].get("/api/v1/waves").json()["items"][0]["allocations"][0]["execution_id"] == execution["id"]
    from app.commerce.financial import wave_actual_summary

    with crm["sessions"]() as db:
        old = wave_actual_summary(
            db, req["wave_id"], calculation["snapshot"]["profile"], data["expenses"][:1], []
        )
        changed = wave_actual_summary(
            db,
            req["wave_id"],
            calculation["snapshot"]["profile"],
            [{**data["expenses"][0], "amount": "200"}],
            [],
        )
        assert old["status"] == changed["status"] == "current"
        assert Decimal(old["cost"]) == Decimal(calculation["snapshot"]["totals"]["cost"])
        assert Decimal(calculation["snapshot"]["totals"]["client_delivery"]) == 4200
        assert Decimal(changed["cost"]) - Decimal(old["cost"]) == 105  # logistics + import duty on it
        assert (
            old["sales"]
            == changed["sales"]
            == format(Decimal(db.get(Execution, execution["id"]).snapshot["total"]), ".2f")
        )
        assert Decimal(old["profit"]) - Decimal(changed["profit"]) == 105
        assert Decimal(old["prepayment_total"]) == Decimal(old["sales"])
        for summary in (old, changed):
            assert Decimal(summary["profitability_percent"]) == (
                Decimal(summary["profit"]) / Decimal(summary["sale_net"]) * 100
            ).quantize(Decimal(".01"))
            assert Decimal(summary["cost_profitability_percent"]) == (
                Decimal(summary["profit"]) / Decimal(summary["cost"]) * 100
            ).quantize(Decimal(".01"))


def test_manager_report_profitability_uses_accepted_sales_without_vat(crm):  # noqa: F811
    req, data, _, _ = structured(crm)
    calculation = cmd(crm, f"/requests/{req['id']}/calculations", data)
    proposal = cmd(crm, f"/requests/{req['id']}/proposals", {
        "calculation_id": calculation["id"],
        "valid_until": (date.today() + timedelta(days=7)).isoformat(),
        "terms": "Предоплата",
    })
    cmd(crm, f"/proposals/{proposal['id']}/accept", {
        "version": proposal["version"], "reason": "Подтверждённый заказ",
        "lines": [{"line_id": calculation["snapshot"]["lines"][0]["line_id"], "quantity": "1"}],
    })
    with crm["sessions"].begin() as db:
        request_row = db.get(Request, req["id"])
        request_row.is_test = False
        request_row.sale_confirmed_at = datetime.now(timezone.utc)
    response = crm["client"].get("/api/v1/analytics/sales-managers", params={
        "from_date": date.today().isoformat(), "to_date": date.today().isoformat(),
    })
    assert response.status_code == 200, response.text
    row = response.json()["items"][0]
    assert Decimal(row["gross_profit"]) == Decimal("500")
    assert Decimal(row["profitability_percent"]) == Decimal("500") / Decimal("1500") * 100
    assert Decimal(row["cost_profitability_percent"]) == Decimal("50")
    assert row["margin_percent"] == row["profitability_percent"]


def test_calendar_is_available_without_task_permissions_and_cannot_confirm(crm):  # noqa: F811
    from sqlalchemy import delete

    with crm["sessions"].begin() as db:
        db.execute(
            delete(PermissionGrant).where(
                PermissionGrant.user_id == crm["manager"].id,
                PermissionGrant.code.in_(["tasks.read", "tasks.write"]),
            )
        )
    login(crm, "manager@example.com")
    row = cmd(
        crm,
        "/payment-calendar",
        {"direction": "expense", "planned_date": "2026-10-06", "amount": "10", "purpose": "Интернет"},
    )
    assert crm["client"].get("/api/v1/payment-calendar").json()["items"][0]["id"] == row["id"]
    row = cmd(
        crm,
        f"/payment-calendar/{row['id']}",
        {
            "version": row["version"],
            "direction": "expense",
            "planned_date": "2026-10-06",
            "amount": "20",
            "purpose": "Интернет",
        },
        method="patch",
    )
    from app.communication.models import FileRecord
    with crm["sessions"].begin() as db:
        db.add(FileRecord(name="Счёт.pdf", media_type="application/pdf", size=10, sha256="a"*64,
                          storage_key="test-calendar-invoice", author_id=crm["manager"].id,
                          status="clean", classification="general", calendar_entry_id=row["id"]))
    cmd(crm, f"/payment-calendar/{row['id']}/submit", {"version": row["version"]})
    assert cmd(crm, f"/payment-calendar/{row['id']}/confirm", {"version": row["version"] + 1}, status=403)[
        "code"
    ]


def test_product_groups_have_stable_separate_codes(crm):  # noqa: F811
    login(crm)
    first = post(crm, "/product-groups", {"name": "Штаммы", "slug": "strains"})
    second = post(crm, "/product-groups", {"name": "Колонки", "slug": "columns"})
    assert second["internal_code"] == first["internal_code"] + 1
    edited = cmd(
        crm,
        f"/product-groups/{first['id']}",
        {"version": first["version"], "name": "Биологические штаммы"},
        method="patch",
    )
    assert edited["internal_code"] == first["internal_code"]
