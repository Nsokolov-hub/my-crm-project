from copy import deepcopy
from datetime import date, datetime, timezone

from fastapi import APIRouter
from pydantic import ValidationError
from sqlalchemy import func, or_, select

from app.core.errors import error
from app.core.security import can, check_request, has_request_permission, require_permission
from app.core.service import advisory, audit, check_version, idem, lock, serialize
from app.core.service import page as paginate
from app.crm.models import (
    Counterparty,
    Country,
    Currency,
    Nomenclature,
    Packing,
    ProductGroup,
    QuoteItem,
    QuoteSheet,
)
from app.crm.models import (
    Request as CRMRequest,
)

from .calculator import calculate, digest, example_profile, validate_profile
from .itemized import calculate_itemized, itemized_profile
from .models import (
    Calculation,
    CalculationProfile,
    Execution,
    ExpenseType,
    Product,
    Quote,
    Wave,
    WaveAllocation,
)
from .procurement import DB, Actor, check_quote, product_view, validate_quantity
from .schemas import CalculationIn, Expense, ExpenseTypeIn, ExpenseTypePatch, ProfileIn, Rate, RequestWaveIn

router = APIRouter(tags=["Расчёты и настраиваемые профили"])


def filter_profile_definition(definition: dict, *, purchase: bool, reward: bool, profit: bool) -> dict:
    definition = deepcopy(definition)
    if not reward:
        definition.pop("reward_enabled", None)
        definition.pop("reward_label", None)
        definition.pop("reward_basis", None)
        definition.pop("default_bonus_coefficient", None)
        definition.pop("bonus_withdrawal_percent", None)
    if not purchase:
        definition.pop("default_expenses", None)
        definition.pop("exchange_rates", None)
    # Profile variables and formulas are user-defined: their names do not identify
    # the financial information they contain or can be used to reconstruct.
    if not (purchase and reward and profit):
        definition.pop("constants", None)
        definition.pop("formulas", None)
        for field in ("customs_rules", "customs_fee_brackets", "financing_annual_rate", "default_markup_coefficient"):
            definition.pop(field, None)
    return definition


def profile_view(db, user, profile: CalculationProfile) -> dict:
    data = serialize(profile)
    data["definition"] = filter_profile_definition(
        data.get("definition", {}),
        purchase=can(db, user, "finance.purchase.read"),
        reward=can(db, user, "finance.reward.read"),
        profit=can(db, user, "finance.profit.read"),
    )
    return data


def filter_calculation_snapshot(db, user, request_id: str, snapshot: dict) -> dict:
    if not snapshot:
        return snapshot
    snapshot = deepcopy(snapshot)

    can_purchase = has_request_permission(db, user, request_id, "finance.purchase.read")
    can_calculations = has_request_permission(db, user, request_id, "finance.calculations.read")
    can_reward = has_request_permission(db, user, request_id, "finance.reward.read")
    can_profit = has_request_permission(db, user, request_id, "finance.profit.read")
    all_finances = can_purchase and can_reward and can_profit

    lines = snapshot.get("lines", [])
    for line in lines:
        if not can_purchase:
            line.pop("quote", None)
            line.pop("quote_item", None)
            line.pop("supplier_id", None)
            line.pop("purchase_currency", None)
        if not all_finances:
            line.pop("expense_details", None)
            line.pop("customs_rule", None)

        if "detail" in line:
            if not can_calculations:
                line.pop("detail", None)
            elif not all_finances:
                # Expose only known outputs; arbitrary constants/intermediate
                # expressions may alias a denied price, reward or margin.
                allowed = {"quantity", "sale_net", "sale_tax"}
                if can_purchase:
                    allowed.add("purchase")
                    allowed.update(("purchase_foreign", "exchange_rate", "purchase_rub"))
                if can_reward:
                    allowed.update((
                        "reward", "manager_bonus", "internal_bonus", "bonus_coefficient", "service_fee",
                        "bonus_withdrawal_percent", "bonus_withdrawal_fee", "additional_service_fee",
                    ))
                if can_profit:
                    allowed.update(("cost", "profit", "margin", "markup_amount", "profitability_percent"))
                line["detail"] = {key: value for key, value in line["detail"].items() if key in allowed}

    if "totals" in snapshot and not all_finances:
        allowed = {"net", "tax", "total", "sale_net", "sale_tax", "sale_total"}
        if can_purchase:
            allowed.update(("purchase_foreign", "purchase_rub"))
        if can_reward:
            allowed.update(("internal_bonus", "service_fee"))
        if can_profit:
            allowed.update(("cost", "profit", "markup_amount", "profitability_percent"))
        snapshot["totals"] = {key: value for key, value in snapshot["totals"].items() if key in allowed}

    if not can_calculations:
        snapshot.pop("profile", None)
    elif "profile" in snapshot:
        snapshot["profile"] = filter_profile_definition(
            snapshot["profile"], purchase=can_purchase, reward=can_reward, profit=can_profit
        )

    if not can_calculations or not all_finances:
        # Inputs include per-line coefficient overrides and expense allocations
        # can disclose the purchase basis, even after detail has been filtered.
        snapshot.pop("input", None)
        snapshot.pop("expense_allocations", None)
        snapshot.pop("rates", None)
        snapshot.pop("payment_terms", None)
        snapshot.pop("wave_distribution", None)
        snapshot.pop("resolved_expenses", None)
    if not can_reward:
        snapshot.pop("internal_adjustment", None)

    return snapshot


def calculation_view(db, user, calculation: Calculation) -> dict:
    result = serialize(calculation)

    snapshot = result.get("snapshot", {})
    if snapshot:
        for name in ("version_number", "base_version_id", "source_type", "source_id", "source_label", "request_number"):
            result[name] = snapshot.get(name)
        result["snapshot"] = filter_calculation_snapshot(db, user, calculation.request_id, snapshot)

    if not has_request_permission(db, user, calculation.request_id, "finance.calculations.read"):
        result.pop("reason", None)

    return result


def latest_calculation(db, request_id: str) -> Calculation | None:
    return db.scalar(
        select(Calculation)
        .where(Calculation.request_id == request_id)
        .order_by(Calculation.created_at.desc(), Calculation.id.desc())
        .limit(1)
    )


def latest_wave_expenses(db, wave_id: str) -> tuple[list[dict] | None, str | None]:
    """Use the latest saved budget for the wave, never a preview or a different wave."""
    calculations = db.scalars(
        select(Calculation)
        .where(Calculation.snapshot["wave"]["id"].as_string() == wave_id)
        .order_by(Calculation.created_at.desc(), Calculation.id.desc())
    )
    for calculation in calculations:
        snapshot = calculation.snapshot
        if snapshot.get("algorithm_version") != "itemized-v2":
            continue
        expenses = snapshot.get("resolved_expenses")
        if expenses is None:
            # Older saved calculations did not persist resolved profile defaults.
            expenses = (snapshot.get("input") or {}).get("expenses")
            if not expenses:
                continue
        return [dict(row) for row in expenses if row.get("scope", "WAVE") == "WAVE"], calculation.id
    return None, None


def supplier_logistics(db, supplier_id: str) -> dict | None:
    supplier = db.get(Counterparty, supplier_id)
    details = supplier.details if supplier and isinstance(supplier.details, dict) else {}
    amount = details.get("Логистика по умолчанию")
    if not amount:
        return None
    currency = str(details.get("Валюта логистики") or "RUB").strip().upper()
    try:
        return Expense.model_validate({
            "name": "Международная логистика", "amount": str(amount).replace(",", "."),
            "currency": currency, "method": "BY_QUANTITY", "scope": "WAVE",
            "stage": "INTERNATIONAL_LOGISTICS", "basis": f"Тариф поставщика {supplier.name}",
        }).model_dump(mode="json")
    except ValidationError:
        error("SUPPLIER_LOGISTICS_INVALID", "Проверьте сумму и валюту логистики в карточке поставщика")


@router.get("/requests/{request_id}/wave-expenses")
def request_wave_expenses(request_id: str, db: DB, user: Actor):
    check_request(db, user, request_id, "finance.calculations.read")
    require_permission(db, user, "finance.purchase.read", request_id)
    request = db.get(CRMRequest, request_id)
    if not request.wave_id:
        return {"expenses": None, "source_calculation_id": None}
    expenses, source_id = latest_wave_expenses(db, request.wave_id)
    if expenses is None:
        wave = db.get(Wave, request.wave_id)
        default_logistics = supplier_logistics(db, wave.supplier_id) if wave else None
        if default_logistics:
            expenses = [default_logistics]
    return {"expenses": expenses, "source_calculation_id": source_id}


def expense_type_view(db, row: ExpenseType) -> dict:
    value = serialize(row)
    currency = db.get(Currency, row.currency_id)
    value["currency_code"] = currency.code if currency else None
    return value


@router.get("/expense-types")
def list_expense_types(db: DB, user: Actor, q: str = "", active: bool = True, page: int = 1, page_size: int = 100):
    if not can(db, user, "profiles.write"):
        require_permission(db, user, "finance.calculations.read")
        require_permission(db, user, "finance.purchase.read")
    stmt = select(ExpenseType).where(ExpenseType.active == active)
    if q:
        stmt = stmt.where(ExpenseType.name.ilike(f"%{q}%"))
    result = paginate(db, stmt.order_by(ExpenseType.name, ExpenseType.id), page, page_size)
    result["items"] = [expense_type_view(db, db.get(ExpenseType, row["id"])) for row in result["items"]]
    return result


@router.post("/expense-types", status_code=201)
def create_expense_type(data: ExpenseTypeIn, db: DB, user: Actor):
    require_permission(db, user, "profiles.write")

    def operation():
        if not data.name.strip():
            error("EXPENSE_TYPE_NAME", "Укажите название вида расхода", field="name")
        currency = db.get(Currency, data.currency_id)
        if not currency or not currency.active:
            error("CURRENCY_REQUIRED", "Выберите действующую валюту расхода", field="currency_id")
        if data.calculation_type in ("PERCENTAGE", "BRACKET") and not data.percent_base:
            error("PERCENT_BASE_REQUIRED", "Выберите базу начисления расхода", field="percent_base")
        if data.calculation_type == "BRACKET" and not data.brackets:
            error("EXPENSE_BRACKET_REQUIRED", "Укажите диапазоны расхода", field="brackets")
        advisory(db, "expense_type.name")
        if db.scalar(select(ExpenseType.id).where(func.lower(ExpenseType.name) == data.name.strip().lower())):
            error("EXPENSE_TYPE_EXISTS", "Вид расхода с таким названием уже есть", 409, "name")
        fields = data.model_dump(exclude={"idempotency_key"})
        fields["name"] = data.name.strip()
        fields["brackets"] = [bracket.model_dump(mode="json") for bracket in data.brackets]
        row = ExpenseType(**fields)
        db.add(row)
        db.flush()
        audit(db, user, "expense_type", row.id, "created", after=serialize(row))
        return {"id": row.id}

    def reconstruct(result):
        return expense_type_view(db, db.get(ExpenseType, result["id"]))

    return idem(db, user, data.idempotency_key, "expense-types.create", data.model_dump(mode="json"), operation, reconstruct)


@router.patch("/expense-types/{expense_type_id}")
def change_expense_type(expense_type_id: str, data: ExpenseTypePatch, db: DB, user: Actor):
    require_permission(db, user, "profiles.write")
    row = lock(db, ExpenseType, expense_type_id)
    check_version(row, data.version)
    before = serialize(row)
    row.active = data.active
    row.version += 1
    db.flush()
    audit(db, user, "expense_type", row.id, "updated", before, serialize(row))
    return expense_type_view(db, row)


def stamp_version(db, request_id: str, snapshot: dict, previous: Calculation | None) -> tuple[dict, str]:
    if previous:
        version_number = int(previous.snapshot.get("version_number") or db.scalar(
            select(func.count(Calculation.id)).where(Calculation.request_id == request_id)
        )) + 1
        snapshot["origin_source_type"] = snapshot["source_type"]
        snapshot["origin_source_id"] = snapshot["source_id"]
        snapshot["source_type"] = "PREVIOUS_VERSION"
        snapshot["source_id"] = previous.id
        snapshot["source_label"] = f"Версия №{version_number - 1} от {previous.created_at.date().isoformat()}"
    else:
        version_number = 1
    snapshot["version_number"] = version_number
    snapshot["base_version_id"] = previous.id if previous else None
    snapshot["calculation_number"] = f"{snapshot['request_number']}-V{version_number}"
    reason = (
        f"На основе версии №{version_number - 1}"
        if previous else f"Создано из {'квоты' if snapshot['source_type'] == 'QUOTE' else 'заявки'} {snapshot['request_number']}"
    )
    return snapshot, reason


def itemized_selection(db, request_id: str, selection, index: int) -> dict:
    quote = db.get(QuoteItem, selection.quote_item_id)
    if not quote:
        error("QUOTE_ITEM_NOT_FOUND", "Позиция квоты не найдена", 404, f"selections.{index}.quote_item_id")
    sheet = db.get(QuoteSheet, quote.quote_id)
    if not sheet or sheet.request_id != request_id:
        error("QUOTE_ITEM_NOT_FOUND", "Позиция квоты не относится к этой заявке", 404, f"selections.{index}.quote_item_id")
    if quote.valid_until.replace(tzinfo=quote.valid_until.tzinfo or timezone.utc) <= datetime.now(timezone.utc):
        error("QUOTE_EXPIRED", "Срок действия выбранной позиции квоты истёк", field=f"selections.{index}.quote_item_id")
    if quote.delivery_days is None:
        error("DELIVERY_DAYS_REQUIRED", "Закупщик должен указать срок поставки в квоте", field=f"selections.{index}.quote_item_id")
    nomenclature = db.get(Nomenclature, quote.nomenclature_id)
    packing = db.get(Packing, quote.packing_id)
    currency = db.get(Currency, quote.currency_id)
    if not nomenclature or not packing or packing.nomenclature_id != nomenclature.id:
        error("NOMENCLATURE_PACKING", "У позиции квоты не найдена номенклатура или фасовка", field=f"selections.{index}.quote_item_id")
    if not currency or not currency.active:
        error("CURRENCY_REQUIRED", f"Для позиции {nomenclature.name} {packing.display_name} отсутствует валюта закупки", field=f"selections.{index}.quote_item_id")
    group = db.get(ProductGroup, nomenclature.product_group_id) if nomenclature.product_group_id else None
    if not group:
        error("PRODUCT_GROUP_REQUIRED", f"У позиции {nomenclature.name} {packing.display_name} не определена товарная группа", field=f"selections.{index}.quote_item_id")
    return {
        "quote_item": serialize(quote),
        "nomenclature": serialize(nomenclature),
        "packing": serialize(packing),
        "product_group": serialize(group),
        "currency_code": currency.code,
        "markup_coefficient": str(selection.markup_coefficient) if selection.markup_coefficient is not None else None,
        "bonus_coefficient": str(selection.bonus_coefficient) if selection.bonus_coefficient is not None else None,
        "weight": str(selection.weight) if selection.weight is not None else None,
    }


def build_calculation(db, user, request_id: str, data: CalculationIn) -> dict:
    advisory(db, f"request-commerce:{request_id}")
    req = lock(db, CRMRequest, request_id)
    check_version(req, data.request_version)
    profile = db.get(CalculationProfile, data.profile_id)
    if (
        not profile
        or profile.status != "published"
        or profile.effective_from > date.today()
        or (profile.effective_until and profile.effective_until < date.today())
    ):
        error(
            "PROFILE_NOT_EFFECTIVE", "Выберите активный опубликованный профиль, действующий на дату расчёта"
        )
    itemized = profile.definition.get("methodology") == "itemized_v2"
    if itemized:
        if data.internal_adjustment or any(selection.bonus_coefficient is not None
                                           for selection in data.selections):
            require_permission(db, user, "finance.reward.read", request_id)
        if not all(selection.quote_item_id for selection in data.selections):
            error("QUOTE_ITEM_REQUIRED", "Для выбранной методики используйте позиции табличной квоты", field="selections")
        country = db.get(Country, profile.definition.get("import_country_id"))
        if not country or not country.active:
            error("IMPORT_COUNTRY_REQUIRED", "Выберите действующую страну ввоза из справочника", field="profile_id")
        try:
            profile_rates = [Rate.model_validate(rate).model_dump(mode="json") for rate in profile.definition.get("exchange_rates", [])]
        except ValidationError:
            error("PROFILE_RATES_INVALID", "Проверьте курсы в профиле расчёта", field="profile_id")
        rates = [rate.model_dump(mode="json") for rate in data.rates] or profile_rates
        for index, rate in enumerate(rates):
            currency = db.scalar(select(Currency).where(Currency.code == rate["currency"]))
            if not currency or not currency.active:
                error("CURRENCY_REQUIRED", f"Валюта курса {rate['currency']} не найдена в справочнике", field=f"rates.{index}.currency")
        selections = [itemized_selection(db, request_id, selection, index) for index, selection in enumerate(data.selections)]
        if not req.wave_id:
            error("WAVE_REQUIRED", "Руководитель должен назначить волну поставки до расчёта", field="wave_id")
        wave = db.get(Wave, req.wave_id)
        suppliers = {row["quote_item"]["supplier_id"] for row in selections}
        if not wave or suppliers != {wave.supplier_id}:
            error("WAVE_SUPPLIER", "Выбранная волна должна быть открыта и относиться к поставщику всех позиций", field="wave_id")
        advisory(db, f"wave:{wave.id}")
        wave = lock(db, Wave, wave.id)
        if wave.status not in ("planned", "assembling"):
            error("WAVE_CLOSED", "Для расчёта выберите открытую волну поставки", field="wave_id")
        selected_quote_ids = [row["quote_item"]["id"] for row in selections]
        existing_quantity = db.scalar(
            select(func.coalesce(func.sum(WaveAllocation.quantity), 0))
            .join(Execution, Execution.id == WaveAllocation.execution_id)
            .where(WaveAllocation.wave_id == wave.id, WaveAllocation.active.is_(True),
                   or_(Execution.quote_item_id.is_(None), Execution.quote_item_id.not_in(selected_quote_ids)))
        )
        wave_expenses, wave_expense_source = latest_wave_expenses(db, wave.id)
        if data.expenses is not None:
            effective_expenses = [expense.model_dump(mode="json") for expense in data.expenses]
        elif wave_expenses is not None:
            request_defaults = [row for row in profile.definition.get("default_expenses", [])
                                if row.get("scope") == "REQUEST"]
            effective_expenses = wave_expenses + request_defaults
        else:
            effective_expenses = list(profile.definition.get("default_expenses", []))
            default_logistics = supplier_logistics(db, wave.supplier_id)
            if default_logistics:
                effective_expenses = [row for row in effective_expenses
                                      if row.get("stage") != "INTERNATIONAL_LOGISTICS"] + [default_logistics]
        snapshot = calculate_itemized(
            profile.definition,
            selections,
            effective_expenses,
            [{**rate, "author_id": user.id} for rate in rates],
            data.internal_adjustment,
            data.payment_terms,
            wave_existing_quantity=existing_quantity,
        )
        snapshot["wave"] = {"id": wave.id, "number": wave.number, "supplier_id": wave.supplier_id,
                            "existing_quantity": str(existing_quantity),
                            "expense_source_calculation_id": wave_expense_source}
        snapshot["resolved_expenses"] = effective_expenses
        snapshot["request_number"] = req.number
        snapshot["source_type"] = "QUOTE" if len({row["quote_item"]["quote_id"] for row in selections}) == 1 else "REQUEST"
        snapshot["source_id"] = selections[0]["quote_item"]["quote_id"] if snapshot["source_type"] == "QUOTE" else request_id
        if snapshot["source_type"] == "QUOTE":
            sheet = db.get(QuoteSheet, snapshot["source_id"])
            snapshot["source_label"] = f"Квота №{sheet.number}"
        else:
            snapshot["source_label"] = f"Заявка №{req.number}"
        snapshot["input"] = {**data.model_dump(mode="json"), "expenses": effective_expenses}
        snapshot["request_version"] = req.version
        snapshot["profile_id"] = profile.id
        snapshot["profile_created_at"] = profile.created_at.isoformat()
        return snapshot
    if any(selection.quote_item_id for selection in data.selections):
        error("PROFILE_METHOD", "Для табличной квоты выберите профиль новой методики", field="profile_id")
    selections = []
    for selection in data.selections:
        quote = lock(db, Quote, selection.quote_id)
        if quote.request_id != request_id:
            error("QUOTE_NOT_FOUND", "Квота не найдена в заявке", 404)
        if quote.revision != selection.quote_revision:
            error("QUOTE_REVISION_CONFLICT", "Редакция квоты изменилась", 409)
        check_quote(db, quote, require_verified=False)
        product = db.get(Product, quote.product_id)
        validate_quantity(quote, product, selection.quantity, selection.unit)
        if quote.sample and not profile.definition["allow_samples"]:
            error("SAMPLES_DISABLED", "Профиль не разрешает нулевую стоимость образцов")
        selections.append(
            {
                **selection.model_dump(mode="json"),
                "quote": serialize(quote),
                "product": product_view(db, product),
            }
        )
    snapshot = calculate(
        profile.definition,
        selections,
        [expense.model_dump(mode="json") for expense in (data.expenses or [])],
        [{**rate.model_dump(mode="json"), "author_id": user.id} for rate in data.rates],
    )
    snapshot["input"] = data.model_dump(mode="json")
    snapshot["request_version"] = req.version
    snapshot["profile_id"] = profile.id
    snapshot["profile_created_at"] = profile.created_at.isoformat()
    snapshot["request_number"] = req.number
    snapshot["source_type"] = "QUOTE" if len({row["quote"]["id"] for row in selections}) == 1 else "REQUEST"
    snapshot["source_id"] = selections[0]["quote"]["id"] if snapshot["source_type"] == "QUOTE" else request_id
    snapshot["source_label"] = f"Квота по заявке №{req.number}" if snapshot["source_type"] == "QUOTE" else f"Заявка №{req.number}"
    return snapshot


@router.get("/profiles/example")
def sample_profile(db: DB, user: Actor):
    require_permission(db, user, "profiles.write")
    return {
        "is_test": True,
        "notice": "Условный арифметический пример A08. Пользователь задаёт собственные действующие формулы и ставки.",
        "definition": example_profile(),
    }


@router.get("/profiles/example-v2")
def sample_itemized_profile(db: DB, user: Actor):
    require_permission(db, user, "profiles.write")
    return {
        "notice": "Выберите страну ввоза и дополните диапазоны таможенного сбора для сумм выше 500 000 ₽ перед публикацией.",
        "definition": itemized_profile(),
    }


@router.put("/requests/{request_id}/wave")
def assign_request_wave(request_id: str, data: RequestWaveIn, db: DB, user: Actor):
    require_permission(db, user, "waves.write", request_id)

    def operation():
        advisory(db, f"request-commerce:{request_id}")
        req = lock(db, CRMRequest, request_id)
        check_version(req, data.request_version)
        wave = db.get(Wave, data.wave_id) if data.wave_id else None
        if data.wave_id and (not wave or not wave.supplier_id or wave.status not in ("planned", "assembling")):
            error("WAVE_REQUIRED", "Выберите открытую волну с указанным поставщиком", field="wave_id")
        before = {"wave_id": req.wave_id}
        req.wave_id = wave.id if wave else None
        req.version += 1
        db.flush()
        audit(db, user, "request", req.id, "assign_wave", before=before,
              after={"wave_id": req.wave_id}, reason="Планирование поставки")
        return {"wave_id": req.wave_id, "version": req.version}

    return idem(db, user, data.idempotency_key, f"assign-wave:{request_id}",
                data.model_dump(mode="json"), operation)


@router.get("/profiles")
def list_profiles(db: DB, user: Actor):
    require_permission(db, user, "finance.calculations.read")
    return {
        "items": [
            profile_view(db, user, profile)
            for profile in db.scalars(
                select(CalculationProfile).order_by(CalculationProfile.created_at.desc())
            ).all()
        ]
    }


@router.post("/profiles")
def create_profile(data: ProfileIn, db: DB, user: Actor):
    require_permission(db, user, "profiles.write")
    require_permission(db, user, "templates.write")

    def operation():
        definition = data.definition.model_dump(mode="json")
        if definition.get("methodology") == "itemized_v2":
            try:
                definition["default_expenses"] = [
                    Expense.model_validate(expense).model_dump(mode="json")
                    for expense in definition.get("default_expenses", [])
                ]
                definition["exchange_rates"] = [
                    Rate.model_validate(rate).model_dump(mode="json")
                    for rate in definition.get("exchange_rates", [])
                ]
            except ValidationError:
                error("PROFILE_INPUTS_INVALID", "Проверьте расходы и курсы профиля", field="definition")
        validate_profile(definition)
        if definition.get("methodology") == "itemized_v2":
            country = db.get(Country, definition["import_country_id"])
            if not country or not country.active:
                error("IMPORT_COUNTRY_REQUIRED", "Выберите действующую страну ввоза из справочника", field="definition.import_country_id")
            for rule in definition["customs_rules"]:
                group = db.scalar(select(ProductGroup).where(
                    ProductGroup.slug == rule["product_group_slug"], ProductGroup.active.is_(True),
                ))
                if not group:
                    error("PRODUCT_GROUP_REQUIRED", "Таможенное правило ссылается на неизвестную товарную группу", field="definition.customs_rules")
        if data.effective_until and data.effective_until < data.effective_from:
            error("PROFILE_DATES", "Дата окончания должна быть не раньше даты начала")
        if data.previous_id and not db.get(CalculationProfile, data.previous_id):
            error("PROFILE_NOT_FOUND", "Исходный профиль не найден", 404)
        profile = CalculationProfile(
            name=data.name,
            previous_id=data.previous_id,
            effective_from=data.effective_from,
            effective_until=data.effective_until,
            definition=definition,
            reason=data.reason,
            author_id=user.id,
        )
        db.add(profile)
        db.flush()
        audit(
            db,
            user,
            "calculation_profile",
            profile.id,
            "create",
            after=serialize(profile),
            reason=data.reason,
        )
        return {"id": profile.id}

    def reconstruct(result: dict) -> dict:
        profile = db.get(CalculationProfile, result["id"])
        return profile_view(db, user, profile)

    return idem(
        db, user, data.idempotency_key, "create-profile", data.model_dump(mode="json"), operation, reconstruct
    )


@router.post("/profiles/{profile_id}/publish")
def publish_profile(profile_id: str, db: DB, user: Actor):
    require_permission(db, user, "profiles.write")
    require_permission(db, user, "templates.write")
    profile = db.get(CalculationProfile, profile_id)
    if not profile:
        error("PROFILE_NOT_FOUND", "Профиль не найден", 404)
    if profile.status == "published":
        error("PROFILE_ALREADY_PUBLISHED", "Профиль уже опубликован", 400)

    # Архивируем предыдущий опубликованный профиль, если это новая версия
    if profile.previous_id:
        prev = db.get(CalculationProfile, profile.previous_id)
        if prev and prev.status == "published":
            prev.status = "archived"
            prev.effective_until = date.today()

    profile.status = "published"
    profile.effective_from = date.today()
    db.flush()
    audit(db, user, "calculation_profile", profile.id, "publish", after=serialize(profile))
    return profile_view(db, user, profile)


@router.post("/requests/{request_id}/calculations/preview")
def preview_calculation(request_id: str, data: CalculationIn, db: DB, user: Actor):
    check_request(db, user, request_id, "calculations.write")
    require_permission(db, user, "finance.purchase.read", request_id)
    require_permission(db, user, "finance.calculations.read", request_id)
    result = build_calculation(db, user, request_id, data)
    previous = latest_calculation(db, request_id)
    if "previous_id" in data.model_fields_set and data.previous_id != (previous.id if previous else None):
        error("CALCULATION_VERSION_CONFLICT", "Основание версии изменилось. Обновите расчёт", 409)
    result, _ = stamp_version(db, request_id, result, previous)
    result = filter_calculation_snapshot(db, user, request_id, result)
    return {"saved": False, "snapshot": result}


@router.post("/requests/{request_id}/calculations")
def save_calculation(request_id: str, data: CalculationIn, db: DB, user: Actor):
    check_request(db, user, request_id, "calculations.write")
    require_permission(db, user, "finance.purchase.read", request_id)
    require_permission(db, user, "finance.calculations.read", request_id)

    def operation():
        snapshot = build_calculation(db, user, request_id, data)
        previous = latest_calculation(db, request_id)
        if "previous_id" in data.model_fields_set and data.previous_id != (previous.id if previous else None):
            error("CALCULATION_VERSION_CONFLICT", "Основание версии изменилось. Обновите расчёт", 409)
        snapshot, reason = stamp_version(db, request_id, snapshot, previous)
        obj = Calculation(
            request_id=request_id,
            profile_id=data.profile_id,
            previous_id=previous.id if previous else None,
            snapshot=snapshot,
            digest=digest(snapshot),
            reason=reason,
            author_id=user.id,
        )
        db.add(obj)
        db.flush()
        audit(
            db,
            user,
            "calculation",
            obj.id,
            "save",
            after={"digest": obj.digest, "profile_id": obj.profile_id},
            reason=reason,
        )
        return {"id": obj.id}

    def reconstruct(result: dict) -> dict:
        obj = db.get(Calculation, result["id"])
        return calculation_view(db, user, obj)

    return idem(
        db,
        user,
        data.idempotency_key,
        f"save-calculation:{request_id}",
        data.model_dump(mode="json"),
        operation,
        reconstruct,
    )


@router.get("/requests/{request_id}/calculations")
def list_calculations(request_id: str, db: DB, user: Actor):
    check_request(db, user, request_id)
    return {
        "items": [
            calculation_view(db, user, item)
            for item in db.scalars(
                select(Calculation)
                .where(Calculation.request_id == request_id)
                .order_by(Calculation.created_at.desc())
            ).all()
        ]
    }
