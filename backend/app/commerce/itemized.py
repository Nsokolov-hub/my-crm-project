"""Item based RUB calculation for structured supplier quote rows.

All monetary inputs and outputs are Decimal strings. A saved result contains
the complete inputs and intermediate amounts needed to reproduce its result.
"""

from datetime import date
from decimal import Decimal, localcontext

from app.core.errors import error

from .calculator import ROUNDING, dec, distribute


def _money(value: Decimal, rounding: str) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUNDING[rounding])


def _string(value: Decimal) -> str:
    return str(value)


def _bracket_amount(brackets: list[dict], basis: Decimal, *, field: str) -> Decimal:
    matches = []
    today = date.today()
    for bracket in brackets:
        try:
            valid_from = date.fromisoformat(str(bracket["valid_from"])) if bracket.get("valid_from") else None
            valid_to = date.fromisoformat(str(bracket["valid_to"])) if bracket.get("valid_to") else None
        except ValueError:
            error("INVALID_BRACKET", "Укажите даты действия диапазона в формате YYYY-MM-DD", field=field)
        if valid_from and valid_to and valid_to < valid_from:
            error("INVALID_BRACKET", "Дата окончания диапазона раньше даты начала", field=field)
        if (valid_from and today < valid_from) or (valid_to and today > valid_to):
            continue
        lower = dec(bracket.get("from_amount", "0"))
        upper = dec(bracket["to_amount"]) if bracket.get("to_amount") is not None else None
        fee = dec(bracket["fee"])
        if lower < 0 or fee < 0 or (upper is not None and upper < lower):
            error("INVALID_BRACKET", "Некорректный диапазон расхода", field=field)
        if basis >= lower and (upper is None or basis <= upper):
            matches.append(fee)
    if len(matches) != 1:
        error(
            "CUSTOMS_FEE_BRACKET_REQUIRED" if field == "customs_fee_brackets" else "EXPENSE_BRACKET_REQUIRED",
            f"Для суммы {basis} ₽ должен подходить ровно один настроенный диапазон",
            field=field,
        )
    return matches[0]


def validate_itemized_profile(profile: dict) -> None:
    if profile.get("management_currency") != "RUB" or profile.get("sale_currency") != "RUB":
        error("RUB_REQUIRED", "Для этой методики валюта учёта и продажи должна быть RUB", field="sale_currency")
    if not profile.get("import_country_id"):
        error("IMPORT_COUNTRY_REQUIRED", "Выберите страну ввоза из справочника", field="import_country_id")
    if dec(profile.get("vat_rate", "22")) < 0 or dec(profile.get("vat_rate", "22")) > 100:
        error("VAT_RATE", "Ставка НДС должна быть от 0 до 100%", field="vat_rate")
    if dec(profile.get("financing_annual_rate", "0")) < 0:
        error("FINANCING_RATE", "Ставка финансирования не может быть отрицательной")
    if not 1 <= int(profile.get("day_basis", 365)) <= 366:
        error("DAY_BASIS", "Укажите число дней в финансовом году от 1 до 366")
    if dec(profile.get("default_markup_coefficient", "1.5")) < 1:
        error("MARKUP", "Коэффициент наценки должен быть не меньше 1")
    if dec(profile.get("default_bonus_coefficient", "1")) < 1:
        error("BONUS", "Коэффициент бонуса должен быть не меньше 1")
    _rate_map(profile.get("exchange_rates") or [], [])
    rules = profile.get("customs_rules") or []
    if not rules:
        error("CUSTOMS_RULE_REQUIRED", "Настройте таможенные правила по товарным группам", field="customs_rules")
    seen = set()
    for rule in rules:
        if not isinstance(rule, dict):
            error("INVALID_CUSTOMS_RULE", "Укажите товарную группу и правило", field="customs_rules")
        group = rule.get("product_group_slug")
        kind = rule.get("type")
        value = dec(rule.get("value", "0"))
        if not group or group in seen or kind not in ("PERCENTAGE", "FIXED_GROUP", "NONE"):
            error("INVALID_CUSTOMS_RULE", "Таможенное правило группы не определено или повторяется", field="customs_rules")
        if value < 0 or (kind == "PERCENTAGE" and value > 100) or (kind == "NONE" and value != 0):
            error("INVALID_CUSTOMS_RULE", "Недопустимое значение таможенного правила", field="customs_rules")
        seen.add(group)
    brackets = profile.get("customs_fee_brackets") or []
    if not brackets:
        error("CUSTOMS_FEE_BRACKET_REQUIRED", "Настройте диапазоны таможенного сбора", field="customs_fee_brackets")
    normalized = []
    for bracket in brackets:
        if not isinstance(bracket, dict) or not {"from_amount", "to_amount", "fee"} <= bracket.keys():
            error("INVALID_BRACKET", "Для диапазона укажите начало, конец и сбор", field="customs_fee_brackets")
        lower = dec(bracket["from_amount"])
        upper = dec(bracket["to_amount"]) if bracket["to_amount"] is not None else None
        fee = dec(bracket["fee"])
        try:
            valid_from = date.fromisoformat(str(bracket["valid_from"])) if bracket.get("valid_from") else None
            valid_to = date.fromisoformat(str(bracket["valid_to"])) if bracket.get("valid_to") else None
        except ValueError:
            error("INVALID_BRACKET", "Проверьте даты действия диапазона", field="customs_fee_brackets")
        if lower < 0 or fee < 0 or (upper is not None and upper < lower) or (valid_from and valid_to and valid_to < valid_from):
            error("INVALID_BRACKET", "Проверьте границы диапазона", field="customs_fee_brackets")
        for old_lower, old_upper, old_from, old_to in normalized:
            amounts_overlap = (old_upper is None or lower <= old_upper) and (upper is None or old_lower <= upper)
            dates_overlap = (old_to is None or valid_from is None or valid_from <= old_to) and (valid_to is None or old_from is None or old_from <= valid_to)
            if amounts_overlap and dates_overlap:
                error("INVALID_BRACKET", "Диапазоны таможенного сбора пересекаются", field="customs_fee_brackets")
        normalized.append((lower, upper, valid_from, valid_to))


def _rate_map(rates: list[dict], rows: list[dict]) -> dict[str, Decimal]:
    result = {"RUB": Decimal("1")}
    required = {row["currency_code"] for row in rows}
    for rate in rates:
        currency = rate["currency"]
        if currency in result:
            error("DUPLICATE_RATE", f"Курс {currency} указан повторно", field="rates")
        if not rate.get("date") or not rate.get("source"):
            error("RATE_DETAILS_REQUIRED", f"Укажите дату и источник курса {currency}", field="rates")
        quoted_units = dec(rate.get("quoted_units", "1"))
        per_unit = dec(rate["management_per_unit"])
        if quoted_units <= 0 or per_unit <= 0:
            error("INVALID_RATE", f"Курс {currency} должен быть положительным", field="rates")
        result[currency] = per_unit / quoted_units
    missing = required - result.keys()
    if missing:
        code = sorted(missing)[0]
        error("RATE_REQUIRED", f"Для {code} отсутствует курс к RUB", field="rates")
    return result


def _allocation(amount: Decimal, rows: list[dict], method: str, manual: dict, rounding: str) -> dict[str, Decimal]:
    if method == "MANUAL":
        parts = {key: dec(value) for key, value in manual.items()}
        if set(parts) != {row["id"] for row in rows} or any(value < 0 for value in parts.values()):
            error("MANUAL_ALLOCATION", "Укажите сумму для каждой позиции при ручном распределении")
        if any(value != _money(value, rounding) for value in parts.values()) or sum(parts.values()) != amount:
            error("MANUAL_ALLOCATION", "Сумма ручного распределения не совпадает с расходом")
        return parts
    if method == "BY_QUANTITY":
        bases = {row["id"]: row["quantity"] for row in rows}
    elif method == "BY_PURCHASE_VALUE":
        bases = {row["id"]: row["purchase_rub"] for row in rows}
    elif method == "EQUALLY_BY_POSITION":
        bases = {row["id"]: Decimal("1") for row in rows}
    elif method == "BY_WEIGHT":
        bases = {row["id"]: row["weight"] for row in rows}
    else:
        error("DISTRIBUTION_METHOD", "Неизвестный способ распределения расхода", field="expenses")
    return distribute(amount, bases, Decimal("0.01"), rounding)


def calculate_itemized(
    profile: dict,
    selections: list[dict],
    expenses: list[dict],
    rates: list[dict],
    internal_adjustment: dict | None = None,
    payment_terms: dict | None = None,
    wave_existing_quantity: Decimal = Decimal("0"),
) -> dict:
    with localcontext() as context:
        context.prec = 48
        return _calculate_itemized(profile, selections, expenses, rates, internal_adjustment or {},
                                   payment_terms or {}, wave_existing_quantity)


def _calculate_itemized(profile, selections, expenses, rates, internal_adjustment, payment_terms, wave_existing_quantity):
    validate_itemized_profile(profile)
    rounding = profile.get("rounding", "half_up")
    if rounding not in ROUNDING:
        error("ROUNDING", "Неизвестное правило округления")
    if not selections:
        error("SELECTION_REQUIRED", "Выберите хотя бы одну позицию квоты", field="selections")
    wave_existing_quantity = dec(wave_existing_quantity)
    if wave_existing_quantity < 0:
        error("WAVE_QUANTITY", "Количество в волне не может быть отрицательным")
    ids = [selection["quote_item"]["id"] for selection in selections]
    if len(ids) != len(set(ids)):
        error("DUPLICATE_SELECTION", "Позиция квоты выбрана повторно", field="selections")
    fx = _rate_map(rates, selections)
    rows = []
    for index, selection in enumerate(selections):
        quote = selection["quote_item"]
        name = selection["nomenclature"]["name"]
        packing = selection["packing"]["display_name"]
        currency = selection["currency_code"]
        group = selection["product_group"]["slug"]
        quantity = dec(quote["quantity"])
        price = dec(quote["unit_price"])
        if quantity <= 0 or price < 0:
            error("QUOTE_PRICE", f"Проверьте цену и количество позиции {name} {packing}", field=f"selections.{index}")
        coefficient = dec(selection.get("markup_coefficient") or profile.get("default_markup_coefficient", "1.5"))
        bonus_coefficient = dec(selection.get("bonus_coefficient") or profile.get("default_bonus_coefficient", "1"))
        if coefficient < 1:
            error("MARKUP", f"Наценка позиции {name} {packing} должна быть не меньше 1", field=f"selections.{index}.markup_coefficient")
        if bonus_coefficient < 1:
            error("BONUS", f"Бонус позиции {name} {packing} должен быть не меньше 1", field=f"selections.{index}.bonus_coefficient")
        foreign = price * quantity
        rows.append({
            "id": quote["id"], "source": selection, "quantity": quantity, "weight": dec(selection.get("weight") or "0"),
            "currency": currency, "group": group, "markup_coefficient": coefficient,
            "bonus_coefficient": bonus_coefficient,
            "purchase_foreign": foreign, "exchange_rate": fx[currency],
            "purchase_rub": _money(foreign * fx[currency], rounding),
            "international_logistics": Decimal("0"), "general_expenses": Decimal("0"),
            "cash_expenses": Decimal("0"),
            "duty": Decimal("0"), "customs_fee": Decimal("0"),
            "expense_details": {},
        })

    allocations = []
    selected_quantity = sum(row["quantity"] for row in rows)
    wave_total_quantity = selected_quantity + wave_existing_quantity

    def shared_by_quantity(amount: Decimal) -> tuple[dict[str, Decimal], Decimal]:
        bases = {row["id"]: row["quantity"] for row in rows}
        if wave_existing_quantity:
            bases["__wave_existing__"] = wave_existing_quantity
        parts = distribute(amount, bases, Decimal("0.01"), rounding)
        return ({row["id"]: parts[row["id"]] for row in rows},
                parts.get("__wave_existing__", Decimal("0")))

    def expense_basis(kind):
        if kind == "PURCHASE":
            return sum(row["purchase_rub"] for row in rows)
        if kind == "CUSTOMS_BASE":
            return sum(row["purchase_rub"] + row["international_logistics"] for row in rows)
        if kind == "DUTY":
            return sum(row["duty"] for row in rows)
        if kind == "COST":
            return sum(row["purchase_rub"] + row["international_logistics"] + row["duty"] + row["customs_fee"] + row["general_expenses"] for row in rows)
        error("PERCENT_BASE_REQUIRED", "Для процентного расхода выберите базу начисления", field="expenses")

    def apply_expense(expense):
        name = expense["name"].strip()
        currency = expense.get("currency", "RUB")
        if currency not in fx:
            error("RATE_REQUIRED", f"Для расхода {name} отсутствует курс {currency} к RUB", field="expenses")
        kind = expense.get("calculation_type", "FIXED")
        if kind in ("FIXED", "MANUAL"):
            amount = _money(dec(expense.get("amount", "0")) * fx[currency], rounding)
        elif kind == "PERCENTAGE":
            basis = expense_basis(expense.get("percent_base"))
            amount = _money(basis * dec(expense.get("amount", "0")) / 100, rounding)
        elif kind == "BRACKET":
            basis = expense_basis(expense.get("percent_base"))
            amount = _money(_bracket_amount(expense.get("brackets") or [], basis, field="expenses") * fx[currency], rounding)
        else:
            error("EXPENSE_TYPE", f"Неизвестный тип расхода {name}", field="expenses")
        if amount < 0:
            error("EXPENSE_AMOUNT", f"Расход {name} не может быть отрицательным", field="expenses")
        method = expense.get("method", "BY_QUANTITY")
        if method == "BY_QUANTITY" and kind in ("FIXED", "BRACKET", "MANUAL"):
            parts, existing_share = shared_by_quantity(amount)
        else:
            parts = _allocation(amount, rows, method, expense.get("manual") or {}, rounding)
            existing_share = Decimal("0")
        stage = expense.get("stage", "GENERAL")
        for row in rows:
            part = parts[row["id"]]
            row["expense_details"][name] = part
            if expense.get("include_in_cash", True):
                row["cash_expenses"] += part
            if expense.get("include_in_cost", True):
                key = "international_logistics" if stage == "INTERNATIONAL_LOGISTICS" else "general_expenses"
                row[key] += part
        allocations.append({
            "name": name, "calculation_type": kind, "method": method,
            "stage": stage, "amount": _string(amount),
            "currency": currency, "parts": {key: _string(value) for key, value in parts.items()},
            "existing_wave_share": _string(existing_share),
            "wave_total_quantity": _string(wave_total_quantity),
            "basis": expense.get("basis", ""), "percent_base": expense.get("percent_base"),
        })

    names = [expense["name"].strip() for expense in expenses]
    if any(not name for name in names) or len(names) != len(set(names)):
        error("EXPENSE_NAME", "Названия расходов должны быть непустыми и уникальными", field="expenses")
    for expense in expenses:
        if expense.get("stage", "GENERAL") == "INTERNATIONAL_LOGISTICS":
            apply_expense(expense)

    rules = {rule["product_group_slug"]: rule for rule in profile["customs_rules"]}
    groups = {row["group"] for row in rows}
    missing = groups - rules.keys()
    if missing:
        error("CUSTOMS_RULE_REQUIRED", f"Не удалось определить таможенное правило для группы {sorted(missing)[0]}", field="customs_rules")
    for row in rows:
        row["customs_base"] = row["purchase_rub"] + row["international_logistics"]
    for group in groups:
        group_rows = [row for row in rows if row["group"] == group]
        rule = rules[group]
        value = dec(rule.get("value", "0"))
        if rule["type"] == "PERCENTAGE":
            for row in group_rows:
                row["duty"] = _money(row["customs_base"] * value / 100, rounding)
        elif rule["type"] == "FIXED_GROUP":
            # The old CRM charges this amount once for all columns in the
            # calculation and divides it by their quantity.
            group_quantity = sum(row["quantity"] for row in group_rows)
            parts = distribute(_money(value, rounding),
                               {row["id"]: row["quantity"] for row in group_rows},
                               Decimal("0.01"), rounding)
            for row in group_rows:
                row["duty"] = parts[row["id"]]
                row["fixed_group_quantity"] = group_quantity
    customs_basis = sum(row["customs_base"] for row in rows)
    fee = _money(_bracket_amount(profile["customs_fee_brackets"], customs_basis, field="customs_fee_brackets"), rounding)
    fee_parts, existing_fee_share = shared_by_quantity(fee)
    for row in rows:
        row["customs_fee"] = fee_parts[row["id"]]
    for expense in expenses:
        if expense.get("stage", "GENERAL") != "INTERNATIONAL_LOGISTICS":
            apply_expense(expense)

    vat_rate = dec(profile.get("vat_rate", "22")) / 100
    deduct_vat = bool(profile.get("vat_deduction_mode", True))
    prepayment = dec(payment_terms.get("prepayment_percent", "100"))
    deferred = dec(payment_terms.get("deferred_percent", "0"))
    try:
        days = int(payment_terms.get("deferred_days", 0))
        if str(payment_terms.get("deferred_days", 0)) not in (str(days), f"{days}.0"):
            raise ValueError
    except (TypeError, ValueError):
        error("PAYMENT_TERMS", "Срок отсрочки укажите целым числом дней", field="payment_terms.deferred_days")
    if prepayment < 0 or deferred < 0 or prepayment + deferred != 100 or days < 0:
        error("PAYMENT_TERMS", "Предоплата и отсрочка должны суммарно составлять 100%", field="payment_terms")
    start_event = payment_terms.get("deferred_start_event") or profile.get("financing_start_event")
    if deferred and start_event not in ("delivery", "shipment", "invoice"):
        error("FINANCING_START_EVENT", "Выберите событие начала отсрочки", field="payment_terms.deferred_start_event")
    annual_rate = dec(profile.get("financing_annual_rate", "0")) / 100
    day_basis = Decimal(int(profile.get("day_basis", 365)))
    adjustment_enabled = bool(internal_adjustment.get("enabled", False))
    adjustment_type = internal_adjustment.get("type", "PERCENTAGE")
    adjustment_value = dec(internal_adjustment.get("value", "0"))
    service_percent = dec(internal_adjustment.get("service_fee_percent", "0"))
    if adjustment_value < 0 or service_percent < 0 or (adjustment_enabled and adjustment_type not in ("PERCENTAGE", "MULTIPLIER", "FIXED")):
        error("INTERNAL_ADJUSTMENT", "Недопустимые параметры внутренней комиссии", field="internal_adjustment")
    fixed_adjustment = {}
    if adjustment_enabled and adjustment_type == "FIXED":
        fixed_adjustment = _allocation(_money(adjustment_value, rounding), rows, "BY_QUANTITY", {}, rounding)

    output = []
    for row in rows:
        source = row["source"]
        quote = source["quote_item"]
        qty = row["quantity"]
        incoming_vat = _money((row["customs_base"] + row["duty"]) * vat_rate, rounding)
        expenses_total = row["international_logistics"] + row["general_expenses"] + row["customs_fee"]
        cost_before_financing = row["purchase_rub"] + expenses_total + row["duty"] + (Decimal("0") if deduct_vat else incoming_vat)
        financed_amount = _money(cost_before_financing * deferred / 100, rounding)
        financing_cost = _money(financed_amount * annual_rate * Decimal(days) / day_basis, rounding)
        cost_before_adjustment = cost_before_financing + financing_cost
        internal_bonus = _money(cost_before_adjustment * (row["bonus_coefficient"] - 1), rounding)
        service_fee = Decimal("0")
        if adjustment_enabled:
            if adjustment_type == "PERCENTAGE":
                internal_bonus += _money(cost_before_adjustment * adjustment_value / 100, rounding)
            elif adjustment_type == "MULTIPLIER":
                if adjustment_value < 1:
                    error("INTERNAL_ADJUSTMENT", "Внутренний множитель должен быть не меньше 1", field="internal_adjustment")
                internal_bonus += _money(cost_before_adjustment * (adjustment_value - 1), rounding)
            else:
                internal_bonus += fixed_adjustment[row["id"]]
            service_fee = _money(cost_before_adjustment * service_percent / 100, rounding)
        cost = cost_before_adjustment + internal_bonus + service_fee
        sale_net = _money(cost * row["markup_coefficient"], rounding)
        sale_tax = _money(sale_net * vat_rate, rounding)
        sale_total = sale_net + sale_tax
        profit = sale_net - cost
        vat_payable = max(Decimal("0"), sale_tax - (incoming_vat if deduct_vat else Decimal("0")))
        cash_need = row["purchase_rub"] + row["cash_expenses"] + row["customs_fee"] + row["duty"] + incoming_vat + financing_cost + internal_bonus + service_fee
        detail = {
            "purchase_foreign": row["purchase_foreign"], "exchange_rate": row["exchange_rate"],
            "purchase_rub": row["purchase_rub"], "international_logistics": row["international_logistics"],
            "customs_base": row["customs_base"], "duty": row["duty"], "customs_fee": row["customs_fee"],
            "duty_per_unit": row["duty"] / qty,
            "fixed_group_quantity": row.get("fixed_group_quantity", Decimal("0")),
            "general_expenses": row["general_expenses"], "expenses_total": expenses_total,
            "cash_expenses": row["cash_expenses"],
            "import_vat": incoming_vat, "cost_before_financing": cost_before_financing,
            "financed_amount": financed_amount, "financing_cost": financing_cost,
            "cost_before_adjustment": cost_before_adjustment, "internal_bonus": internal_bonus,
            "bonus_coefficient": row["bonus_coefficient"],
            "service_fee": service_fee, "cost": cost, "markup_coefficient": row["markup_coefficient"],
            "markup_amount": sale_net - cost, "sale_net": sale_net, "sale_tax": sale_tax,
            "sale_total": sale_total, "profit": profit, "vat_payable": vat_payable,
            "cash_need": cash_need,
            "profitability_percent": profit / cost * 100 if cost else Decimal("0"),
            "expense_share_percent": expenses_total / cost * 100 if cost else Decimal("0"),
            "investment_efficiency_percent": profit / cash_need * 100 if cash_need else Decimal("0"),
        }
        name = source["nomenclature"]["name"]
        packing = source["packing"]["display_name"]
        output.append({
            "line_id": row["id"], "item_id": quote.get("source_request_item_id"),
            "quote_item_id": row["id"], "quote_id": quote["quote_id"],
            "product": {
                "name": name, "article": source["nomenclature"].get("article"),
                "cas": source["nomenclature"].get("cas"), "packaging": packing,
            },
            "description": f"{name} {packing}", "packing": packing,
            "supplier_id": quote["supplier_id"], "product_group": source["product_group"],
            "customs_rule": rules[row["group"]],
            "quantity": _string(qty), "unit": "pcs", "purchase_currency": row["currency"],
            "delivery_days": quote.get("delivery_days"),
            "net": _string(sale_net), "tax": _string(sale_tax), "total": _string(sale_total),
            "unit_price": _string((sale_net / qty).quantize(Decimal("0.00000001"), rounding=ROUNDING[rounding])),
            "tax_category": profile.get("tax_category") or "НДС",
            "detail": {key: _string(value) for key, value in detail.items()},
            "expense_details": {key: _string(value) for key, value in row["expense_details"].items()},
            "quote_item": quote,
        })
    total_keys = (
        "purchase_rub", "international_logistics", "customs_base", "duty",
        "customs_fee", "general_expenses", "expenses_total", "cash_expenses", "import_vat", "cost_before_financing",
        "financed_amount", "financing_cost", "internal_bonus", "service_fee", "cost", "markup_amount",
        "sale_net", "sale_tax", "sale_total", "profit", "vat_payable", "cash_need",
    )
    totals = {key: _string(sum(dec(line["detail"][key]) for line in output)) for key in total_keys}
    totals.update(net=totals["sale_net"], tax=totals["sale_tax"], total=totals["sale_total"])
    if dec(totals["cost"]):
        totals["markup_coefficient"] = _string(dec(totals["sale_net"]) / dec(totals["cost"]))
        totals["profitability_percent"] = _string(dec(totals["profit"]) / dec(totals["cost"]) * 100)
        totals["expense_share_percent"] = _string(dec(totals["expenses_total"]) / dec(totals["cost"]) * 100)
    else:
        totals.update(markup_coefficient="0", profitability_percent="0", expense_share_percent="0")
    if dec(totals["cash_need"]):
        totals["investment_efficiency_percent"] = _string(dec(totals["profit"]) / dec(totals["cash_need"]) * 100)
    else:
        totals["investment_efficiency_percent"] = "0"
    return {
        "lines": output, "totals": totals, "currency": "RUB", "management_currency": "RUB",
        "expense_allocations": allocations, "rates": rates, "profile": profile,
        "wave_distribution": {"existing_quantity": _string(wave_existing_quantity),
                              "selected_quantity": _string(selected_quantity),
                              "total_quantity": _string(wave_total_quantity),
                              "existing_customs_fee_share": _string(existing_fee_share),
                              "customs_fee": _string(fee)},
        "internal_adjustment": internal_adjustment,
        "payment_terms": {**payment_terms, "deferred_start_event": start_event},
        "algorithm_version": "itemized-v2",
    }


def itemized_profile() -> dict:
    """Starter configuration. Every rate remains editable in a published profile."""
    return {
        "methodology": "itemized_v2", "management_currency": "RUB", "sale_currency": "RUB",
        "currency_precision": {"RUB": 2}, "rounding": "half_up", "import_country_id": None,
        "vat_rate": "22", "vat_deduction_mode": True,
        "financing_annual_rate": "0", "day_basis": 365, "financing_start_event": "delivery",
        "default_markup_coefficient": "1.5",
        "default_bonus_coefficient": "1",
        "default_expenses": [],
        "customs_rules": [
            {"product_group_slug": "reference_standards", "type": "PERCENTAGE", "value": "5"},
            {"product_group_slug": "reagents", "type": "PERCENTAGE", "value": "5"},
            {"product_group_slug": "columns", "type": "FIXED_GROUP", "value": "73800"},
            {"product_group_slug": "lab_glassware", "type": "NONE", "value": "0"},
            {"product_group_slug": "other", "type": "NONE", "value": "0"},
        ],
        "customs_fee_brackets": [{"from_amount": "0", "to_amount": "500000", "fee": "4997"}],
        "tax_category": "НДС 22%", "template": {"title": "Коммерческое предложение", "show_cas": True, "show_manufacturer": False},
    }
