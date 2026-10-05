"""Wave budgets follow all planned orders while released documents remain immutable."""

import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from openpyxl import load_workbook
from test_commerce import command, commerce  # noqa: F401
from test_itemized_wave_calculation import _selection

from app.commerce.financial import (
    selection_from_saved_line,
    validate_unknown_wave_basis,
    wave_financial_digest,
)
from app.commerce.itemized import calculate_itemized, itemized_profile
from app.commerce.models import Calculation, CalculationProfile, CommercialDocument
from app.core.errors import DomainError
from app.core.models import PermissionGrant
from app.crm.models import (
    Country,
    Currency,
    Nomenclature,
    Packing,
    ProductGroup,
    QuoteItem,
    QuoteSheet,
    Request,
)


def profile_definition(country_id):
    profile = itemized_profile()
    profile.update(import_country_id=country_id, vat_rate="20", round_sale_up_to_ruble=False)
    profile["customs_fee_brackets"] = [
        {"from_amount": "0", "to_amount": "1000", "fee": "50"},
        {"from_amount": "1000.01", "to_amount": "2000", "fee": "300"},
        {"from_amount": "2000.01", "to_amount": None, "fee": "800"},
    ]
    profile["default_expenses"] = [
        {
            "name": "Логистика",
            "amount": "120",
            "currency": "RUB",
            "method": "BY_QUANTITY",
            "scope": "WAVE",
            "stage": "INTERNATIONAL_LOGISTICS",
        },
        {"name": "Декларант", "amount": "90", "currency": "RUB", "method": "BY_QUANTITY", "scope": "WAVE"},
    ]
    return profile


def test_whole_wave_crosses_fee_brackets_and_reallocates_every_order():
    profile = profile_definition("country")
    first = _selection("first", "reference_standards", "2", "300", "1")
    second = _selection("second", "reference_standards", "4", "200", "1")
    third = _selection("third", "reference_standards", "4", "150", "1")
    for row in (first, second, third):
        row["currency_code"] = "RUB"
    one = calculate_itemized(profile, [first], profile["default_expenses"], [])
    assert one["wave_distribution"]["customs_fee"] == "50.00"
    two = calculate_itemized(
        profile, [second], profile["default_expenses"], [], wave_existing_selections=[first]
    )
    assert two["wave_distribution"]["customs_value"] == "1520.00"
    assert two["wave_distribution"]["customs_fee"] == "300.00"
    assert {row["quote_item_id"]: row["customs_fee"] for row in two["wave_distribution"]["allocations"]} == {
        "first": "100.00",
        "second": "200.00",
    }
    three = calculate_itemized(
        profile, [third], profile["default_expenses"], [], wave_existing_selections=[first, second]
    )
    assert three["wave_distribution"]["customs_value"] == "2120.00"
    assert three["wave_distribution"]["customs_fee"] == "800.00"
    assert sum(Decimal(row["customs_fee"]) for row in three["wave_distribution"]["allocations"]) == Decimal(
        "800"
    )
    assert {
        row["quote_item_id"]: row["expenses_total"] for row in three["wave_distribution"]["allocations"]
    } == {
        "first": "202.00",
        "second": "404.00",
        "third": "404.00",
    }
    assert three["lines"][0]["detail"]["customs_fee_per_unit"] == "80.00"
    assert Decimal(three["lines"][0]["detail"]["cost"]) >= Decimal("404")


def test_vat_deduction_removes_import_vat_from_cost_but_not_cash_needed():
    profile = profile_definition("country")
    row = _selection("first", "reference_standards", "2", "300", "1")
    row["currency_code"] = "RUB"
    profile["vat_deduction_mode"] = False
    before = calculate_itemized(profile, [row], [], [])
    profile["vat_deduction_mode"] = True
    after = calculate_itemized(profile, [row], [], [])
    imported = Decimal(before["totals"]["import_vat"])
    assert imported > 0
    assert Decimal(before["totals"]["cost"]) - Decimal(after["totals"]["cost"]) == imported
    # Markup is always on customs value + duty; unrecoverable VAT is recharged afterwards.
    assert Decimal(before["totals"]["sale_net"]) - Decimal(after["totals"]["sale_net"]) == imported
    assert before["totals"]["cost_after_markup"] == after["totals"]["cost_after_markup"]
    assert before["totals"]["profit"] == after["totals"]["profit"]
    assert before["totals"]["cash_need"] == after["totals"]["cash_need"]
    assert before["totals"]["import_vat"] == after["totals"]["import_vat"]


def test_fixed_internal_bonus_is_allocated_only_to_its_request():
    profile = profile_definition("country")
    own = _selection("own", "reference_standards", "2", "300", "1")
    peer = _selection("peer", "reference_standards", "2", "300", "1")
    for row in (own, peer):
        row["currency_code"] = "RUB"
    result = calculate_itemized(
        profile,
        [own],
        [],
        [],
        internal_adjustment={"enabled": True, "type": "FIXED", "value": "100"},
        wave_existing_selections=[peer],
    )
    assert result["lines"][0]["detail"]["internal_bonus"] == "100.00"


def test_partial_accepted_snapshot_preserves_weight_for_shared_costs():
    profile = profile_definition("country")
    profile["customs_fee_brackets"] = [{"from_amount": "0", "to_amount": None, "fee": "0"}]
    old = _selection("old", "reference_standards", "4", "150", "1")
    old["currency_code"] = "RUB"
    old["weight"] = "8"
    saved = calculate_itemized(profile, [old], [], [])
    accepted = selection_from_saved_line(saved["lines"][0], "old-request", Decimal("1"), {"weight": "8"})
    assert accepted["weight"] == "2"
    new = _selection("new", "reference_standards", "1", "150", "1")
    new["currency_code"] = "RUB"
    new["weight"] = "2"
    recalculated = calculate_itemized(
        profile,
        [new],
        [
            {
                "name": "Перевозка",
                "amount": "100",
                "currency": "RUB",
                "method": "BY_WEIGHT",
                "scope": "WAVE",
            }
        ],
        [],
        wave_existing_selections=[accepted],
    )
    assert recalculated["lines"][0]["expense_details"]["Перевозка"] == "50.00"
    assert recalculated["expense_allocations"][0]["wave_parts"] == {"new": "50.00", "old": "50.00"}


@pytest.mark.parametrize(
    "kind, method",
    [
        ("PERCENTAGE", "BY_PURCHASE_VALUE"),
        ("BRACKET", "BY_WEIGHT"),
        ("FIXED", "BY_PURCHASE_VALUE"),
        ("FIXED", "EQUALLY_BY_POSITION"),
    ],
)
def test_unknown_accepted_basis_cannot_silently_omit_common_expenses(kind, method):
    profile = profile_definition("country")
    profile["customs_fee_brackets"] = [{"from_amount": "0", "to_amount": None, "fee": "50"}]
    with pytest.raises(DomainError, match="нет сохранённой базы") as raised:
        validate_unknown_wave_basis(
            profile, [{"scope": "WAVE", "calculation_type": kind, "method": method}], Decimal("2")
        )
    assert raised.value.code == "WAVE_PERCENT_BASIS_UNAVAILABLE"
    validate_unknown_wave_basis(
        profile, [{"scope": "WAVE", "calculation_type": "FIXED", "method": "BY_QUANTITY"}], Decimal("2")
    )


def test_shared_foreign_expense_rate_changes_wave_digest():
    profile = profile_definition("country")
    rows = [_selection("own", "reference_standards", "2", "300", "1")]
    rows[0]["_purchase_rub"] = "600"
    expenses = [{"name": "Логистика", "amount": "1", "currency": "USD", "scope": "WAVE"}]

    def rates(amount):
        return [
            {
                "currency": "USD",
                "management_per_unit": amount,
                "quoted_units": "1",
                "date": "2026-10-02",
                "source": "Проверка",
            }
        ]

    old = wave_financial_digest(rows, expenses, profile, rates=rates("90"))
    assert old != wave_financial_digest(rows, expenses, profile, rates=rates("100"))
    assert old == wave_financial_digest(rows, expenses, profile, rates=rates("90.0000"))


def wave_payload(env, **overrides):
    return {
        "supplier_id": env["supplier_id"],
        "route": "Индия — Москва",
        "origin_country": "IN",
        "owner_id": env["owner_id"],
        "close_week": 53,
        "close_year": 2020,
        "departure_week": 1,
        "departure_year": 2021,
        "arrival_week": 3,
        "arrival_year": 2021,
        **overrides,
    }


def test_wave_iso_week_boundaries_and_legacy_dates(commerce):  # noqa: F811
    env = commerce
    wave = command(env, "/waves", wave_payload(env))
    assert wave["close_date"] == "2020-12-28"
    assert wave["departure_date"] == "2021-01-04"
    invalid = command(env, "/waves", wave_payload(env, close_year=2021), expected=422)
    assert invalid["code"] == "WAVE_WEEK"
    incomplete = command(env, "/waves", wave_payload(env, arrival_year=None), expected=422)
    assert incomplete["code"] == "WAVE_WEEK"
    reversed_weeks = command(
        env, "/waves", wave_payload(env, arrival_week=52, arrival_year=2020), expected=422
    )
    assert reversed_weeks["code"] == "WAVE_DATES"
    legacy = command(
        env,
        "/waves",
        {
            "supplier_id": env["supplier_id"],
            "route": "IN — RU",
            "origin_country": "IN",
            "owner_id": env["owner_id"],
            "close_date": "2024-12-30",
            "departure_date": "2025-01-01",
            "arrival_date": "2025-01-08",
        },
    )
    assert (legacy["close_year"], legacy["close_week"]) == (2025, 1)


def test_api_wave_reprices_zero_two_three_orders_and_delivery_documents(commerce, monkeypatch):  # noqa: F811
    env = commerce
    from app.commerce import files

    paragraphs = []
    paragraph = files.Paragraph

    def capture_paragraph(text, *args, **kwargs):
        paragraphs.append(text)
        return paragraph(text, *args, **kwargs)

    monkeypatch.setattr(files, "Paragraph", capture_paragraph)
    wave = command(env, "/waves", wave_payload(env))
    now = datetime.now(timezone.utc)
    requests = []
    quotes = []
    with env["sessions"].begin() as db:
        group = ProductGroup(name="Стандарты", slug="reference_standards")
        country = Country(name="Индия", iso2="IN")
        rub = Currency(code="RUB", name="Рубль")
        db.add_all([group, country, rub, Currency(code="USD", name="Доллар")])
        db.flush()
        nomenclature = Nomenclature(name="Образец", product_group_id=group.id)
        db.add(nomenclature)
        db.flush()
        packing = Packing(
            nomenclature_id=nomenclature.id, value=Decimal("1"), unit="pcs", display_name="1 шт."
        )
        db.add(packing)
        profile = CalculationProfile(
            name="Расчёт волны",
            status="published",
            effective_from=date.today(),
            definition=profile_definition(country.id),
            reason="Проверка",
            author_id=env["owner_id"],
        )
        db.add(profile)
        for index, (quantity, price) in enumerate((("2", "300"), ("4", "200"), ("4", "150"))):
            request = (
                db.get(Request, env["request_id"])
                if index == 0
                else Request(
                    number=f"WAVE-{index}",
                    title=f"Заявка {index}",
                    client_id=env["client_id"],
                    seller_id=env["seller_id"],
                    owner_id=env["owner_id"],
                )
            )
            request.wave_id = wave["id"]
            db.add(request)
            db.flush()
            sheet = QuoteSheet(
                number=f"Q-{index}",
                request_id=request.id,
                supplier_id=env["supplier_id"],
                author_id=env["owner_id"],
            )
            db.add(sheet)
            db.flush()
            quote = QuoteItem(
                quote_id=sheet.id,
                supplier_id=env["supplier_id"],
                nomenclature_id=nomenclature.id,
                packing_id=packing.id,
                quantity=Decimal(quantity),
                unit_price=Decimal(price),
                currency_id=rub.id,
                delivery_days=14,
                quoted_at=now,
                valid_until=now + timedelta(days=21),
                source_request_item_id=env["item_id"],
                author_id=env["owner_id"],
            )
            db.add(quote)
            db.flush()
            requests.append(request.id)
            quotes.append(quote.id)
    empty = env["client"].get("/api/v1/waves").json()["items"][0]["financial_summary"]
    assert empty["provisional"] is True
    assert empty["customs_fee"] == "50.00"
    assert empty["expenses_total"] == "260.00"
    saved = []
    proposal = None
    invoice = None
    for index in range(3):
        result = command(
            env,
            f"/requests/{requests[index]}/calculations",
            {
                "request_version": 1,
                "profile_id": profile.id,
                "selections": [{"quote_item_id": quotes[index]}],
                "delivery_days": 45,
                "vat_deductible": index == 2,
            },
        )
        saved.append(result)
        assert result["wave_stale"] is False
        if index == 0:
            proposal = command(
                env,
                f"/requests/{requests[0]}/proposals",
                {
                    "calculation_id": result["id"],
                    "valid_until": (date.today() + timedelta(days=15)).isoformat(),
                    "terms": "Предоплата",
                },
            )
            accepted = command(
                env,
                f"/proposals/{proposal['id']}/accept",
                {
                    "version": proposal["version"],
                    "lines": [{"line_id": result["snapshot"]["lines"][0]["line_id"], "quantity": "2"}],
                    "reason": "Подтверждено покупателем",
                },
            )
            invoice = command(
                env,
                f"/requests/{requests[0]}/invoices",
                {
                    "proposal_id": proposal["id"],
                    "lines": [{"execution_id": accepted["executions"][0]["id"], "quantity": "2"}],
                    "due_date": (date.today() + timedelta(days=15)).isoformat(),
                    "terms": "Предоплата",
                },
            )
            assert invoice["snapshot"]["delivery_days"] == proposal["snapshot"]["delivery_days"] == 45
    summary = env["client"].get("/api/v1/waves").json()["items"][0]["financial_summary"]
    assert summary["status"] == "current"
    assert Decimal(summary["total_quantity"]) == 10
    assert Decimal(summary["customs_value"]) == 2120
    assert Decimal(summary["customs_fee"]) == 800
    assert Decimal(summary["expenses_total"]) == 1010
    assert {row["request_id"]: row["customs_fee"] for row in summary["allocations"]} == {
        requests[0]: "160.00",
        requests[1]: "320.00",
        requests[2]: "320.00",
    }
    preview_payload = {
        "request_version": 1,
        "profile_id": profile.id,
        "selections": [{"quote_item_id": quotes[0]}],
        "delivery_days": 45,
    }
    preview = command(env, f"/requests/{requests[0]}/calculations/preview", preview_payload)
    with env["sessions"].begin() as db:
        old_quote = db.get(QuoteItem, quotes[2])
        replacement = QuoteItem(
            quote_id=old_quote.quote_id,
            supplier_id=old_quote.supplier_id,
            nomenclature_id=old_quote.nomenclature_id,
            packing_id=old_quote.packing_id,
            quantity=Decimal("1"),
            unit_price=Decimal("150"),
            currency_id=old_quote.currency_id,
            delivery_days=21,
            quoted_at=now,
            valid_until=now + timedelta(days=21),
            source_request_item_id=env["item_id"],
            author_id=env["owner_id"],
        )
        db.add(replacement)
    revised = command(
        env,
        f"/requests/{requests[2]}/calculations",
        {
            "request_version": 1,
            "profile_id": profile.id,
            "previous_id": saved[2]["id"],
            "selections": [{"quote_item_id": replacement.id}],
        },
    )
    assert revised["snapshot"]["delivery_days"] == 21
    assert revised["wave_stale"] is False
    stale_save = command(
        env,
        f"/requests/{requests[0]}/calculations",
        {
            **preview_payload,
            "expected_wave_digest": preview["snapshot"]["wave_distribution"]["digest"],
        },
        expected=409,
    )
    assert stale_save["code"] == "WAVE_CHANGED"
    with env["sessions"]() as db:
        assert db.query(Calculation).filter_by(request_id=requests[0]).count() == 1
    changed = env["client"].get("/api/v1/waves").json()["items"][0]["financial_summary"]
    assert Decimal(changed["total_quantity"]) == 7
    assert Decimal(changed["customs_fee"]) == 300
    assert sum(Decimal(row["customs_fee"]) for row in changed["allocations"]) == 300
    assert quotes[2] not in {row["quote_item_id"] for row in changed["allocations"]}
    first = env["client"].get(f"/api/v1/requests/{requests[0]}/calculations").json()["items"][0]
    assert first["wave_stale"] is True
    assert first["snapshot"] == saved[0]["snapshot"]
    with env["sessions"]() as db:
        stored_proposal = db.get(CommercialDocument, proposal["id"])
        assert stored_proposal.snapshot == proposal["snapshot"]
        assert db.get(Calculation, saved[0]["id"]).snapshot["totals"]["customs_fee"] == "50.00"
    assert proposal["snapshot"]["delivery_days"] == 45
    assert "import_vat" not in proposal["snapshot"]["totals"]
    pdf = env["client"].get(f"/api/v1/documents/{proposal['id']}/file?format=pdf")
    assert pdf.content.startswith(b"%PDF-")
    assert "Общий срок поставки: 45 дней" in paragraphs
    xlsx = env["client"].get(f"/api/v1/documents/{proposal['id']}/file?format=xlsx")
    book = load_workbook(io.BytesIO(xlsx.content))
    assert any(row[0] == "Общий срок поставки, дней" and row[1] == 45 for row in book.active.values)
    assert saved[2]["snapshot"]["vat_deductible"] is True
    assert saved[2]["snapshot"]["totals"]["import_vat"] != "0"
    usd_budget = [
        {
            "name": "Логистика USD",
            "amount": "1",
            "currency": "USD",
            "method": "BY_QUANTITY",
            "scope": "WAVE",
            "stage": "INTERNATIONAL_LOGISTICS",
        },
        {"name": "Декларант", "amount": "90", "currency": "RUB", "scope": "WAVE"},
    ]

    def fx(value):
        return [
            {
                "currency": "USD",
                "management_per_unit": value,
                "quoted_units": "1",
                "date": date.today().isoformat(),
                "source": "Проверка общего курса",
            }
        ]

    saved_fx = command(
        env,
        f"/requests/{requests[0]}/calculations",
        {
            **preview_payload,
            "expenses": usd_budget,
            "rates": fx("90"),
        },
    )
    fx_preview = command(
        env,
        f"/requests/{requests[0]}/calculations/preview",
        {
            **preview_payload,
            "expenses": usd_budget,
            "rates": fx("90"),
        },
    )
    command(
        env,
        f"/requests/{requests[1]}/calculations",
        {
            "request_version": 1,
            "profile_id": profile.id,
            "selections": [{"quote_item_id": quotes[1]}],
            "rates": fx("100"),
        },
    )
    latest_own = env["client"].get(f"/api/v1/requests/{requests[0]}/calculations").json()["items"][0]
    assert latest_own["id"] == saved_fx["id"] and latest_own["wave_stale"] is True
    stale_fx = command(
        env,
        f"/requests/{requests[0]}/calculations",
        {
            **preview_payload,
            "expenses": usd_budget,
            "rates": fx("90"),
            "expected_wave_digest": fx_preview["snapshot"]["wave_distribution"]["digest"],
        },
        expected=409,
    )
    assert stale_fx["code"] == "WAVE_CHANGED"
    with env["sessions"].begin() as db:
        for code in ("finance.purchase.read", "finance.calculations.read"):
            db.query(PermissionGrant).filter_by(user_id=env["owner_id"], code=code).update({"scope": "own"})
        for request_id in requests[1:]:
            db.get(Request, request_id).owner_id = env["other_id"]
    scoped_preview = command(
        env, f"/requests/{requests[0]}/calculations/preview", {**preview_payload, "rates": fx("100")}
    )["snapshot"]
    scoped_saved = (
        env["client"].get(f"/api/v1/requests/{requests[0]}/calculations").json()["items"][0]["snapshot"]
    )
    for projection in (scoped_preview, scoped_saved):
        assert {row["request_id"] for row in projection["wave_distribution"]["allocations"]} == {requests[0]}
        for expense in projection["expense_allocations"]:
            assert not {quotes[1], quotes[2], replacement.id} & expense["wave_parts"].keys()
        assert requests[1] not in str(projection)
        assert requests[2] not in str(projection)
    assert env["client"].get("/api/v1/waves").json()["items"][0]["financial_summary"] is None
