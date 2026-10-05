from copy import deepcopy
from datetime import date, datetime, timezone

from fastapi import APIRouter
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.errors import DomainError, error
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

from .calculator import calculate, dec, digest, example_profile, profitability_metrics, validate_profile
from .itemized import calculate_itemized, itemized_profile
from .logistics import normalize_logistics_expenses
from .models import (
    Calculation,
    CalculationProfile,
    CommercialDocument,
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
        for field in ("customs_rules", "customs_fee_brackets", "customs_fee_overrides", "financing_annual_rate", "default_markup_coefficient"):
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
                    allowed.update(("cost", "profit", "margin", "markup_amount", "profitability_percent", "margin_percent", "cost_profitability_percent"))
                line["detail"] = {key: value for key, value in line["detail"].items() if key in allowed}

    if "totals" in snapshot and not all_finances:
        allowed = {"net", "tax", "total", "sale_net", "sale_tax", "sale_total"}
        if can_purchase:
            allowed.update(("purchase_foreign", "purchase_rub"))
        if can_reward:
            allowed.update(("internal_bonus", "service_fee"))
        if can_profit:
            allowed.update(("cost", "profit", "markup_amount", "profitability_percent", "margin_percent", "cost_profitability_percent"))
        snapshot["totals"] = {key: value for key, value in snapshot["totals"].items() if key in allowed}

    if not can_calculations:
        snapshot.pop("profile", None)
    elif "profile" in snapshot:
        snapshot["profile"] = filter_profile_definition(
            snapshot["profile"], purchase=can_purchase, reward=can_reward, profit=can_profit
        )

    if can_calculations and all_finances and "wave_distribution" in snapshot:
        distribution = snapshot["wave_distribution"]
        visible_quote_ids = {line.get("quote_item_id") for line in lines}
        visible_allocations = []
        for allocation in distribution.get("allocations", []):
            peer_id = allocation.get("request_id")
            if peer_id and all(has_request_permission(db, user, peer_id, permission) for permission in (
                "requests.read", "finance.purchase.read", "finance.calculations.read",
            )):
                visible_allocations.append(allocation)
                visible_quote_ids.add(allocation.get("quote_item_id"))
        distribution["allocations"] = visible_allocations
        for allocation in snapshot.get("expense_allocations", []):
            if "wave_parts" in allocation:
                allocation["wave_parts"] = {key: value for key, value in allocation["wave_parts"].items()
                                            if key in visible_quote_ids}
        for expenses in (snapshot.get("resolved_expenses", []), (snapshot.get("input") or {}).get("expenses") or []):
            for expense in expenses:
                if expense.get("method") == "MANUAL":
                    expense["manual"] = {key: value for key, value in (expense.get("manual") or {}).items()
                                         if key in visible_quote_ids}
        source_id = (snapshot.get("wave") or {}).get("expense_source_calculation_id")
        source = db.get(Calculation, source_id) if source_id else None
        if source and not has_request_permission(db, user, source.request_id, "finance.calculations.read"):
            snapshot["wave"]["expense_source_calculation_id"] = None

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
        wave_id = (snapshot.get("wave") or {}).get("id")
        if wave_id and snapshot.get("algorithm_version") == "itemized-v2":
            try:
                current = wave_financial_summary(db, wave_id)
                result["wave_current_digest"] = current.get("digest")
                result["wave_stale"] = snapshot["wave"].get("financial_digest") != current.get("digest")
            except DomainError as exc:
                result["wave_stale"] = True
                result["wave_recalculation_error"] = exc.message
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
        return [row for row in normalize_logistics_expenses(expenses)
                if row.get("scope", "WAVE") == "WAVE"], calculation.id
    return None, None


def existing_wave_components(db, wave_id: str, selected_quote_ids: list[str]) -> tuple:
    """Use accepted document snapshots as the basis of a wave-wide percentage."""
    quantity = dec("0")
    missing_basis_quantity = dec("0")
    components = {"purchase_rub": dec("0"), "duty": dec("0")}
    allocations = db.scalars(select(WaveAllocation).where(
        WaveAllocation.wave_id == wave_id, WaveAllocation.active.is_(True)
    )).all()
    for allocation in allocations:
        execution = db.get(Execution, allocation.execution_id)
        if not execution or execution.quote_item_id in selected_quote_ids:
            continue
        quantity += allocation.quantity
        proposal = db.get(CommercialDocument, execution.proposal_id)
        calculation = db.get(Calculation, proposal.calculation_id) if proposal else None
        if not calculation:
            missing_basis_quantity += allocation.quantity
            continue
        line = next((row for row in calculation.snapshot.get("lines", [])
                     if row.get("line_id") == execution.line_id), None)
        if not line or not dec(line.get("quantity", "0")) or "purchase_rub" not in (line.get("detail") or {}):
            missing_basis_quantity += allocation.quantity
            continue
        factor = allocation.quantity / dec(line["quantity"])
        detail = line.get("detail") or {}
        for key in components:
            components[key] += dec(detail.get(key, "0")) * factor
    return quantity, components, missing_basis_quantity


def selection_from_saved_line(line: dict, request_id: str, quantity=None, input_selection=None) -> dict | None:
    """Use immutable recorded purchases; repricing a wave never rewrites accepted prices."""
    detail = line.get("detail") or {}
    quote = line.get("quote_item")
    if not quote or not line.get("product_group") or "purchase_rub" not in detail:
        return None
    quote = deepcopy(quote)
    original_quantity = dec(line["quantity"])
    if not original_quantity:
        return None
    quantity = original_quantity if quantity is None else dec(quantity)
    quote["quantity"] = str(quantity)
    return {
        "quote_item": quote,
        "nomenclature": deepcopy(line["product"]),
        "packing": {"display_name": line.get("packing") or line["product"].get("packaging", "")},
        "product_group": deepcopy(line["product_group"]),
        "currency_code": line["purchase_currency"],
        "calculation_type": line.get("calculation_type", "IMPORT"),
        "markup_coefficient": detail.get("markup_coefficient"),
        "bonus_coefficient": detail.get("bonus_coefficient"),
        "weight": str(dec(input_selection["weight"]) * quantity / original_quantity) if (input_selection or {}).get("weight") is not None else None,
        "_exchange_rate": detail.get("exchange_rate", "1"),
        "_purchase_rub": str(dec(detail["purchase_rub"]) * quantity / original_quantity),
        "_request_id": request_id,
    }


def wave_selections(db, wave_id: str, *, exclude_request_id=None, exclude_quote_ids=()) -> tuple:
    """Latest planned selections plus allocated accepted lines not present in that plan."""
    result = {}
    requests = db.scalars(select(CRMRequest).where(CRMRequest.wave_id == wave_id, CRMRequest.archived.is_(False))).all()
    for request in requests:
        if request.id == exclude_request_id:
            continue
        calculation = latest_calculation(db, request.id)
        if not calculation or (calculation.snapshot.get("wave") or {}).get("id") != wave_id:
            continue
        inputs = {row.get("quote_item_id"): row for row in (calculation.snapshot.get("input") or {}).get("selections", [])}
        for line in calculation.snapshot.get("lines", []):
            quote_id = line.get("quote_item_id")
            if quote_id in exclude_quote_ids:
                continue
            selection = selection_from_saved_line(line, request.id, input_selection=inputs.get(quote_id))
            if selection:
                result[quote_id] = selection
    unknown_quantity = dec("0")
    components = {"purchase_rub": dec("0"), "duty": dec("0")}
    allocated = {}
    allocations = db.scalars(select(WaveAllocation).where(
        WaveAllocation.wave_id == wave_id, WaveAllocation.active.is_(True)
    )).all()
    for allocation in allocations:
        execution = db.get(Execution, allocation.execution_id)
        if not execution or execution.quote_item_id in exclude_quote_ids:
            continue
        quote_id = execution.quote_item_id
        if quote_id in result:
            continue
        proposal = db.get(CommercialDocument, execution.proposal_id)
        calculation = db.get(Calculation, proposal.calculation_id) if proposal else None
        line = next((row for row in (calculation.snapshot.get("lines", []) if calculation else [])
                     if row.get("line_id") == execution.line_id), None)
        input_selection = next((row for row in (calculation.snapshot.get("input") or {}).get("selections", [])
                                if row.get("quote_item_id") == quote_id), None) if calculation else None
        selection = selection_from_saved_line(line, execution.request_id, allocation.quantity, input_selection) if line else None
        if not selection:
            unknown_quantity += allocation.quantity
            continue
        if quote_id in allocated:
            previous = allocated[quote_id]
            previous["quote_item"]["quantity"] = str(dec(previous["quote_item"]["quantity"]) + allocation.quantity)
            previous["_purchase_rub"] = str(dec(previous["_purchase_rub"]) + dec(selection["_purchase_rub"]))
            if previous.get("weight") is not None or selection.get("weight") is not None:
                previous["weight"] = str(dec(previous.get("weight") or "0") + dec(selection.get("weight") or "0"))
        else:
            allocated[quote_id] = selection
    result.update(allocated)
    return list(result.values()), unknown_quantity, components


def fee_is_independent_of_unknown_value(profile: dict) -> bool:
    brackets = profile.get("customs_fee_brackets") or []
    return (len(brackets) == 1 and dec(brackets[0].get("from_amount", "0")) == 0
            and brackets[0].get("to_amount") is None)


def validate_unknown_wave_basis(profile: dict, expenses: list[dict], unknown_quantity) -> None:
    if not unknown_quantity:
        return
    if not fee_is_independent_of_unknown_value(profile) or any(
        row.get("scope", "WAVE") == "WAVE" and (
            row.get("calculation_type") in ("PERCENTAGE", "BRACKET")
            or row.get("method", "BY_QUANTITY") != "BY_QUANTITY"
        ) for row in expenses
    ):
        error("WAVE_PERCENT_BASIS_UNAVAILABLE",
              "Для общих расходов нет сохранённой базы части ранее принятых позиций волны", 409,
              field="expenses")


def wave_revision_digest(db, wave: Wave) -> str:
    """A preview guard also detects another request replacing the common FX/budget."""
    members = []
    for request in db.scalars(select(CRMRequest).where(CRMRequest.wave_id == wave.id, CRMRequest.archived.is_(False))):
        calculation = latest_calculation(db, request.id)
        members.append((request.id, calculation.id if calculation else None))
    _, budget_source = latest_wave_expenses(db, wave.id)
    allocations = [(row.id, row.version, format(row.quantity.normalize(), "f")) for row in db.scalars(
        select(WaveAllocation).where(WaveAllocation.wave_id == wave.id, WaveAllocation.active.is_(True))
    )]
    return digest({"wave_version": wave.version, "members": sorted(members),
                   "allocations": sorted(allocations), "budget_source": budget_source})


def wave_financial_digest(selections: list[dict], expenses: list[dict], profile: dict, unknown_quantity="0", rates=None) -> str:
    basis = sorted([{
        "id": row["quote_item"]["id"], "quantity": str(dec(row["quote_item"]["quantity"])),
        "purchase_rub": str(dec(row.get("_purchase_rub", "0"))),
        "group": row["product_group"]["slug"], "calculation_type": row.get("calculation_type", "IMPORT"), "weight": str(dec(row.get("weight") or "0")),
    } for row in selections], key=lambda row: row["id"])
    # Fixed decimal spelling is canonical: 2 and 2.000000 represent one plan.
    for row in basis:
        for key in ("quantity", "purchase_rub", "weight"):
            row[key] = format(dec(row[key]).normalize(), "f")
    expense_currencies = {row.get("currency", "RUB") for row in expenses if row.get("scope", "WAVE") == "WAVE"}
    expense_currencies.update(row.get("minimum_currency", "RUB") for row in expenses
                              if row.get("scope", "WAVE") == "WAVE" and dec(row.get("minimum_amount", "0")))
    effective_rates = {row["currency"]: format((dec(row["management_per_unit"]) / dec(row.get("quoted_units", "1"))).normalize(), "f")
                       for row in rates or [] if row["currency"] in expense_currencies}
    if "RUB" in expense_currencies:
        effective_rates["RUB"] = "1"
    return digest({"rows": basis, "effective_expense_rates": effective_rates, "unknown_quantity": format(dec(unknown_quantity).normalize(), "f"),
                   "expenses": [row for row in expenses if row.get("scope", "WAVE") == "WAVE"],
                   "customs_rules": profile.get("customs_rules"),
                   "customs_fee_brackets": profile.get("customs_fee_brackets"),
                   "customs_fee_overrides": profile.get("customs_fee_overrides"),
                   "vat_rate": profile.get("vat_rate"), "rounding": profile.get("rounding"),
                   "dated_bracket_day": date.today().isoformat() if any(
                       bracket.get("valid_from") or bracket.get("valid_to")
                       for bracket in [*profile.get("customs_fee_brackets", []),
                                       *[entry for expense in expenses for entry in expense.get("brackets", [])]]
                   ) else None})


def wave_financial_summary(db, wave_id: str) -> dict:
    calculations = db.scalars(select(Calculation).where(
        Calculation.snapshot["wave"]["id"].as_string() == wave_id
    ).order_by(Calculation.created_at.desc(), Calculation.id.desc())).all()
    latest = next((row for row in calculations if row.snapshot.get("algorithm_version") == "itemized-v2"), None)
    if latest:
        profile = deepcopy(latest.snapshot["profile"])
        expenses, source_id = latest_wave_expenses(db, wave_id)
        rates = latest.snapshot.get("rates") or []
    else:
        published = db.scalar(select(CalculationProfile).where(
            CalculationProfile.status == "published", CalculationProfile.effective_from <= date.today(),
            (CalculationProfile.effective_until.is_(None)) | (CalculationProfile.effective_until >= date.today()),
            CalculationProfile.definition["methodology"].as_string() == "itemized_v2",
        ).order_by(CalculationProfile.created_at.desc(), CalculationProfile.id.desc()).limit(1))
        if not published:
            return {"status": "unconfigured", "provisional": True, "total_quantity": "0", "allocations": []}
        profile = deepcopy(published.definition)
        expenses = [row for row in normalize_logistics_expenses(profile.get("default_expenses", []))
                    if row.get("scope", "WAVE") == "WAVE"]
        wave = db.get(Wave, wave_id)
        logistics = supplier_logistics(db, wave.supplier_id) if wave else None
        if logistics:
            expenses = [row for row in expenses if row.get("stage") != "INTERNATIONAL_LOGISTICS"] + [logistics]
        rates = profile.get("exchange_rates") or []
        source_id = None
    selections, unknown_quantity, components = wave_selections(db, wave_id)
    validate_unknown_wave_basis(profile, expenses or [], unknown_quantity)
    if not selections:
        from .itemized import _bracket_amount, _money, _rate_map
        fee = _money(_bracket_amount(profile["customs_fee_brackets"], dec("0"), field="customs_fee_brackets"), profile.get("rounding", "half_up"))
        fx = _rate_map(rates, [])
        provisional_expenses = fee
        for expense in expenses or []:
            currency = expense.get("currency", "RUB")
            if currency not in fx:
                error("RATE_REQUIRED", f"Для расхода {expense['name']} отсутствует курс {currency} к RUB")
            kind = expense.get("calculation_type", "FIXED")
            if kind == "PERCENTAGE":
                minimum_currency = expense.get("minimum_currency", "RUB")
                minimum = dec(expense.get("minimum_amount", "0"))
                if minimum and minimum_currency not in fx:
                    error("RATE_REQUIRED", f"Для минимума расхода {expense['name']} отсутствует курс {minimum_currency} к RUB")
                amount = minimum * fx.get(minimum_currency, dec("1"))
            elif kind == "BRACKET":
                amount = _bracket_amount(expense.get("brackets") or [], dec("0"), field="expenses") * fx[currency]
            else:
                amount = dec(expense.get("amount", "0")) * fx[currency]
            if expense.get("include_in_cost", True):
                provisional_expenses += _money(amount, profile.get("rounding", "half_up"))
        return {"status": "provisional", "provisional": True, "total_quantity": str(unknown_quantity),
                "customs_value": "0", "customs_fee": str(fee), "import_vat": "0", "expenses_total": str(provisional_expenses),
                "allocations": [], "source_calculation_id": source_id,
                "digest": wave_financial_digest([], expenses or [], profile, unknown_quantity, rates)}
    # Only shared costs belong to the wave ledger; individual adjustments stay in saved requests.
    calculated = calculate_itemized(profile, selections, expenses or [], rates,
                                    wave_existing_quantity=unknown_quantity, wave_existing_components=components)
    summary = calculated["wave_distribution"]
    for row in summary["allocations"]:
        request = db.get(CRMRequest, row.get("request_id")) if row.get("request_id") else None
        row["request_number"] = request.number if request else None
    summary.update(status="partial" if unknown_quantity else "current", provisional=bool(unknown_quantity),
                   source_calculation_id=source_id,
                   digest=wave_financial_digest(selections, expenses or [], profile, unknown_quantity, rates))
    summary["actual"] = wave_actual_summary(db, wave_id, profile, expenses or [], rates)
    return summary


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
        "calculation_type": (db.get(Counterparty, quote.supplier_id).details or {}).get("calculation_type", "IMPORT"),
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
    if data.vat_deductible is None:
        data = data.model_copy(update={"vat_deductible": itemized})
    if itemized:
        if data.internal_adjustment or any(selection.bonus_coefficient is not None
                                           for selection in data.selections):
            require_permission(db, user, "finance.reward.read", request_id)
        if not all(selection.quote_item_id for selection in data.selections):
            error("QUOTE_ITEM_REQUIRED", "Для выбранной методики используйте позиции табличной квоты", field="selections")
        country = db.get(Country, profile.definition.get("import_country_id"))
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
        importing = any(row['calculation_type'] == 'IMPORT' for row in selections)
        if importing and (not country or not country.active):
            error('IMPORT_COUNTRY_REQUIRED', 'Выберите действующую страну ввоза из справочника', field='profile_id')
        wave = None
        peer_selections, existing_quantity, existing_components = [], dec('0'), {}
        wave_expenses, wave_expense_source = None, None
        if importing:
            if not req.wave_id:
                error('WAVE_REQUIRED', 'Руководитель должен назначить волну поставки до расчёта', field='wave_id')
            wave = db.get(Wave, req.wave_id)
            suppliers = {row['quote_item']['supplier_id'] for row in selections}
            if not wave or suppliers != {wave.supplier_id}:
                error('WAVE_SUPPLIER', 'Волна должна относиться к поставщику всех выбранных позиций', field='wave_id')
            advisory(db, f'wave:{wave.id}')
            wave = lock(db, Wave, wave.id)
            if wave.status not in ('planned', 'assembling'):
                error('WAVE_CLOSED', 'Для расчёта выберите открытую волну', field='wave_id')
            peer_selections, existing_quantity, existing_components = wave_selections(
                db, wave.id, exclude_request_id=request_id,
                exclude_quote_ids=[row['quote_item']['id'] for row in selections])
            wave_expenses, wave_expense_source = latest_wave_expenses(db, wave.id)
        missing_basis = existing_quantity
        if data.expenses is not None:
            effective_expenses = [expense.model_dump(mode="json") for expense in data.expenses]
        elif wave_expenses is not None:
            request_defaults = [row for row in normalize_logistics_expenses(profile.definition.get("default_expenses", []))
                                if row.get("scope") == "REQUEST"]
            effective_expenses = wave_expenses + request_defaults
        else:
            effective_expenses = normalize_logistics_expenses(profile.definition.get("default_expenses", []))
            default_logistics = supplier_logistics(db, wave.supplier_id) if wave else None
            if not importing:
                effective_expenses = [row for row in effective_expenses if row.get("scope") == "REQUEST"]
            if default_logistics:
                effective_expenses = [row for row in effective_expenses
                                      if row.get("stage") != "INTERNATIONAL_LOGISTICS"] + [default_logistics]
        from app.business.routes import delivery_expense
        delivery = delivery_expense(db, req, data.delivery_city, data.delivery_required)
        effective_expenses = [row for row in effective_expenses if not row.get('name', '').startswith('Доставка СДЭК до 15 кг:') and row.get('name') != 'НДС доставки СДЭК']
        if delivery:
            delivery_vat = (dec(delivery['amount']) * dec(profile.definition['vat_rate']) / 100).quantize(dec('.01'))
            effective_expenses += [delivery, {**delivery, 'name': 'НДС доставки СДЭК', 'amount': str(delivery_vat),
                                             'include_in_cost': not data.vat_deductible, 'basis': 'НДС тарифа СДЭК'}]
        effective_expenses = normalize_logistics_expenses(effective_expenses)
        validate_unknown_wave_basis(profile.definition, effective_expenses, missing_basis)
        effective_profile = deepcopy(profile.definition)
        effective_profile["vat_deduction_mode"] = data.vat_deductible
        for selection in selections:
            selection["_request_id"] = request_id
        snapshot = calculate_itemized(
            effective_profile,
            selections,
            effective_expenses,
            [{**rate, "author_id": user.id} for rate in rates],
            data.internal_adjustment,
            data.payment_terms,
            wave_existing_quantity=existing_quantity,
            wave_existing_components=existing_components,
            wave_existing_selections=peer_selections,
        )
        if wave:
            snapshot["wave"] = {"id": wave.id, "number": wave.number, "supplier_id": wave.supplier_id,
                                "existing_quantity": str(existing_quantity),
                                "expense_source_calculation_id": wave_expense_source}
            digest_rows = [selection_from_saved_line(line, request_id) for line in snapshot["lines"]]
            for index, row in enumerate(digest_rows):
                row["weight"] = selections[index].get("weight")
            snapshot["wave"]["financial_digest"] = wave_financial_digest(
                [*digest_rows, *peer_selections], effective_expenses, effective_profile, existing_quantity, rates)
            snapshot["wave_distribution"]["digest"] = digest({
                "financial_digest": snapshot["wave"]["financial_digest"],
                "wave_revision": wave_revision_digest(db, wave),
            })
        else:
            snapshot['wave_distribution']['digest'] = digest({'selections': selections, 'expenses': effective_expenses, 'profile': effective_profile})
        snapshot["vat_deductible"] = data.vat_deductible
        snapshot["delivery_days"] = data.delivery_days if data.delivery_days is not None else max(
            (line.get("delivery_days") or 0 for line in snapshot["lines"]), default=0)
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
    if data.vat_deductible:
        error("PROFILE_METHOD", "Для расчёта с вычетом НДС выберите профиль новой методики", field="profile_id")
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
        if (db.get(Counterparty, quote.supplier_id).details or {}).get('calculation_type', 'IMPORT') != 'IMPORT':
            error('PROFILE_METHOD', 'Для DAP и закупок в РФ используйте табличные квоты и профиль новой методики')
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
    snapshot["vat_deductible"] = data.vat_deductible
    snapshot["delivery_days"] = data.delivery_days
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
        "notice": "Сбор 1: шкала 2026 года по обычным товарам. Сбор 2: 73 800 ₽ на колонки, пошлина 0%. Штаммы: пошлина 12%.",
        "definition": itemized_profile(),
    }


@router.put("/requests/{request_id}/wave")
def assign_request_wave(request_id: str, data: RequestWaveIn, db: DB, user: Actor):
    require_permission(db, user, "waves.write", request_id)

    def operation():
        advisory(db, f"request-commerce:{request_id}")
        req = lock(db, CRMRequest, request_id)
        check_version(req, data.request_version)
        for wave_id in sorted({value for value in (req.wave_id, data.wave_id) if value}):
            advisory(db, f"wave:{wave_id}")
        wave = lock(db, Wave, data.wave_id) if data.wave_id else None
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
        if data.expected_wave_digest is not None and data.expected_wave_digest != (snapshot.get("wave_distribution") or {}).get("digest"):
            error("WAVE_CHANGED", "Состав или расходы волны изменились. Выполните расчёт заново перед сохранением", 409)
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
        from app.business.routes import review_calculation
        review_calculation(db, user, obj)
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


def wave_actual_summary(db, wave_id, profile, expenses, rates):
    """Accepted sales keep their prices; only shared wave costs are redistributed."""
    selections, accepted = {}, []
    for allocation in db.scalars(select(WaveAllocation).where(WaveAllocation.wave_id == wave_id, WaveAllocation.active.is_(True))):
        execution = db.get(Execution, allocation.execution_id)
        proposal = db.get(CommercialDocument, execution.proposal_id)
        if not proposal:
            return {'status': 'incomplete', 'message': 'Для принятой позиции отсутствует исходный документ'}
        calculation = db.get(Calculation, proposal.calculation_id)
        line = next((line for line in calculation.snapshot.get('lines', []) if line['line_id'] == execution.line_id), None)
        if not line:
            return {'status': 'incomplete', 'message': 'Нет сохранённого расчёта принятой позиции'}
        selection = selection_from_saved_line(line, execution.request_id, allocation.quantity)
        if not selection:
            return {'status': 'incomplete', 'message': 'Для старой позиции нет базы таможенной стоимости'}
        key = line['quote_item_id']
        if key in selections:
            selections[key]['quote_item']['quantity'] = str(dec(selections[key]['quote_item']['quantity']) + allocation.quantity)
            selections[key]['_purchase_rub'] = str(dec(selections[key]['_purchase_rub']) + dec(selection['_purchase_rub']))
        else:
            selections[key] = selection
        accepted.append((allocation, line, calculation.snapshot, execution))
    if not accepted:
        return {'status': 'empty', 'message': 'В волну ещё не распределены принятые позиции'}
    current = calculate_itemized(profile, list(selections.values()), expenses, rates)
    current_lines = {line['line_id']: line for line in current['lines']}
    sales = sale_net = costs = prepaid = deferred = dec('0')
    for allocation, old, snapshot, execution in accepted:
        quantity = allocation.quantity
        factor = quantity / dec(old['quantity'])
        detail = old['detail']
        new = current_lines[old['quote_item_id']]
        current_factor = quantity / dec(new['quantity'])
        wave_names = {expense['name'] for expense in normalize_logistics_expenses(
            snapshot.get('resolved_expenses', snapshot.get('input', {}).get('expenses', []) or []))
                      if expense.get('scope', 'WAVE') == 'WAVE' and expense.get('include_in_cost', True)}
        old_shared = sum((dec(old.get('expense_details', {}).get(name, '0')) for name in wave_names), dec('0'))
        old_vat = dec('0') if snapshot.get('vat_deductible', snapshot.get('profile', {}).get('vat_deduction_mode', True)) else dec(detail.get('import_vat', '0'))
        new_vat = dec('0') if snapshot.get('vat_deductible', snapshot.get('profile', {}).get('vat_deduction_mode', True)) else dec(new['detail']['import_vat'])
        old_base = (dec(detail['cost']) - old_shared - dec(detail['customs_fee']) - dec(detail['duty']) - old_vat) * factor
        new_shared = dec(new['detail']['international_logistics']) + dec(new['detail']['general_expenses'])
        costs += old_base + (new_shared + dec(new['detail']['customs_fee']) + dec(new['detail']['duty']) + new_vat) * current_factor
        # Execution amounts preserve document rounding, including partial acceptance.
        execution_factor = quantity / execution.quantity
        total = dec(execution.snapshot['total']) * execution_factor
        net = dec(execution.snapshot['net']) * execution_factor
        sales += total
        sale_net += net
        terms = snapshot.get('payment_terms') or {}
        prepaid += total * dec(terms.get('prepayment_percent', '100')) / 100
        deferred += total * dec(terms.get('deferred_percent', '0')) / 100
    profit = sale_net - costs
    return {key: format(value.quantize(dec('.01')), 'f') for key, value in {
        'sales': sales, 'sale_net': sale_net, 'cost': costs, 'profit': profit,
        'prepayment_total': prepaid, 'deferred_total': deferred,
        **profitability_metrics(profit, sale_net, costs),
    }.items()} | {'status': 'current', 'customs_fee_1': current['wave_distribution']['customs_fee_1'],
                 'customs_fee_2': current['wave_distribution']['customs_fee_2'],
                 'basis': 'Принятые и распределённые позиции: цены документов и текущие общие расходы; финансирование и бонусы по сохранённому расчёту'}
