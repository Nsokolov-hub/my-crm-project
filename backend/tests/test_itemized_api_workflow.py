"""A saved itemized calculation can issue a customer proposal with profile inputs."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from test_commerce import command, commerce  # noqa: F401

from app.commerce.itemized import itemized_profile
from app.commerce.models import CalculationProfile, Execution, WaveAllocation
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


def test_profile_rates_wave_quantity_and_proposal(commerce):  # noqa: F811
    env = commerce
    now = datetime.now(timezone.utc)
    wave = command(env, "/waves", {
        "supplier_id": env["supplier_id"], "route": "Индия — Москва", "origin_country": "IN",
        "owner_id": env["owner_id"], "close_date": date.today().isoformat(),
        "departure_date": date.today().isoformat(),
        "arrival_date": (date.today() + timedelta(days=14)).isoformat(),
    })
    assert wave["number"] == "Поставщик 1"
    command(env, f"/requests/{env['request_id']}/wave", {
        "request_version": 1, "wave_id": wave["id"],
    }, method="put")

    with env["sessions"].begin() as db:
        group = ProductGroup(name="Стандартные образцы", slug="reference_standards")
        country = Country(name="Индия", iso2="IN")
        currency = Currency(name="Доллар", code="USD")
        db.add_all([group, country, currency])
        db.flush()
        nomenclature = Nomenclature(name="Образец", product_group_id=group.id)
        db.add(nomenclature)
        db.flush()
        packing = Packing(nomenclature_id=nomenclature.id, value=Decimal("1"), unit="pcs", display_name="1 шт.")
        db.add(packing)
        db.flush()
        sheet = QuoteSheet(number="Q-WAVE", request_id=env["request_id"],
                           supplier_id=env["supplier_id"], author_id=env["owner_id"])
        db.add(sheet)
        db.flush()
        quote = QuoteItem(quote_id=sheet.id, supplier_id=env["supplier_id"],
                          nomenclature_id=nomenclature.id, packing_id=packing.id,
                          quantity=Decimal("2"), unit_price=Decimal("100"), currency_id=currency.id,
                          delivery_days=14, quoted_at=now, valid_until=now + timedelta(days=21),
                          source_request_item_id=env["item_id"], author_id=env["owner_id"])
        db.add(quote)
        profile = itemized_profile()
        profile["import_country_id"] = country.id
        profile["customs_fee_brackets"] = [{"from_amount": "0", "to_amount": None, "fee": "4997"}]
        profile["exchange_rates"] = [{"currency": "USD", "management_per_unit": "100",
                                       "quoted_units": "1", "date": date.today().isoformat(),
                                       "source": "Финансовый профиль"}]
        profile["default_expenses"] = [{"name": "Декларант", "amount": "25000",
                                        "currency": "RUB", "method": "BY_QUANTITY",
                                        "stage": "GENERAL", "calculation_type": "FIXED"}]
        saved_profile = CalculationProfile(name="Тест волны", status="published",
                                           effective_from=date.today(), definition=profile,
                                           reason="Проверка", author_id=env["owner_id"])
        db.add(saved_profile)
        other_request = Request(number="OTHER-WAVE", title="Ранее подтверждённый заказ",
                                client_id=env["client_id"], seller_id=env["seller_id"],
                                owner_id=env["owner_id"])
        db.add(other_request)
        db.flush()
        previous = Execution(request_id=other_request.id, item_id=env["item_id"],
                             proposal_id="previous-proposal", line_id="previous-line",
                             quantity=Decimal("4"), cancelled_quantity=Decimal("0"), unit="pcs",
                             snapshot={}, acceptance_reason="Подтверждено", accepted_by=env["owner_id"])
        db.add(previous)
        db.flush()
        db.add(WaveAllocation(wave_id=wave["id"], execution_id=previous.id,
                              approval_id="previous-approval", quantity=Decimal("4"), active=True))

    payload = {"request_version": 2, "profile_id": saved_profile.id,
               "selections": [{"quote_item_id": quote.id, "markup_coefficient": "1.5"}]}
    preview = command(env, f"/requests/{env['request_id']}/calculations/preview", payload)
    assert preview["snapshot"]["wave_distribution"]["total_quantity"] == "6.000000"
    assert preview["snapshot"]["expense_allocations"][0]["existing_wave_share"] == "16666.67"
    assert preview["snapshot"]["rates"][0]["source"] == "Финансовый профиль"
    calculation = command(env, f"/requests/{env['request_id']}/calculations", payload)
    assert calculation["snapshot"]["lines"][0]["expense_details"]["Декларант"] == "8333.33"
    proposal = command(env, f"/requests/{env['request_id']}/proposals", {
        "calculation_id": calculation["id"],
        "valid_until": (date.today() + timedelta(days=15)).isoformat(),
        "terms": "Предоплата",
    })
    assert proposal["kind"] == "proposal"
    assert proposal["snapshot"]["lines"][0]["quantity"] == "2.000000"
    with env["sessions"].begin() as db:
        db.query(PermissionGrant).filter_by(user_id=env["other_id"], code="requests.read").update({"scope": "all"})
    env["user_id"] = env["other_id"]
    denied = command(env, f"/requests/{env['request_id']}/proposals", {
        "calculation_id": calculation["id"],
        "valid_until": (date.today() + timedelta(days=15)).isoformat(),
        "terms": "Предоплата",
    }, expected=403)
    assert denied["code"] == "FORBIDDEN"
