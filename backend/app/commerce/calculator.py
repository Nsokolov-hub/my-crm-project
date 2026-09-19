"""Restricted Decimal formula interpreter. User expressions are data, never Python code."""

import ast
import hashlib
import json
import re
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, DecimalException, localcontext

from app.core.errors import error

ROUNDING = {"half_up": ROUND_HALF_UP, "half_even": ROUND_HALF_EVEN, "down": ROUND_DOWN}
UNITS = {
    "mg": ("mass", Decimal("0.001")),
    "g": ("mass", Decimal("1")),
    "kg": ("mass", Decimal("1000")),
    "ml": ("volume", Decimal("0.001")),
    "l": ("volume", Decimal("1")),
    "pcs": ("count", Decimal("1")),
}
REQUIRED_OUTPUTS = {
    "customs_base",
    "duty",
    "import_tax",
    "cost",
    "cash_need",
    "reward",
    "sale_net",
    "sale_tax",
}
BUILT_INS = {"purchase", "quantity", "expenses_cost", "expenses_cash"}


def dec(value) -> Decimal:
    if isinstance(value, (float, bool)):
        error("DECIMAL_REQUIRED", "Передайте десятичное число строкой")
    try:
        result = Decimal(str(value))
        if not result.is_finite() or abs(result) > Decimal("1e20") or result.as_tuple().exponent < -48:
            raise ValueError
        return result
    except (DecimalException, ValueError, TypeError):
        error("INVALID_DECIMAL", "Недопустимое десятичное число")


def digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def validate_cas(value: str | None, reason: str = "") -> str | None:
    if not value:
        if not reason.strip():
            error("CAS_REASON_REQUIRED", "Укажите причину отсутствия CAS", field="no_cas_reason")
        return None
    normalized = value.strip()
    if not re.fullmatch(r"[1-9]\d{1,6}-\d{2}-\d", normalized):
        error("INVALID_CAS", "CAS должен иметь формат 64-17-5", field="cas")
    digits = normalized.replace("-", "")
    if sum(int(digit) * weight for weight, digit in enumerate(reversed(digits[:-1]), 1)) % 10 != int(
        digits[-1]
    ):
        error("INVALID_CAS", "Контрольная цифра CAS неверна", field="cas")
    return normalized


def convert(quantity: Decimal, source: str, target: str) -> Decimal:
    if source not in UNITS or target not in UNITS or UNITS[source][0] != UNITS[target][0]:
        error(
            "INCOMPATIBLE_UNITS",
            "Нельзя переводить массу в объём без отдельного коэффициента и основания",
            field="unit",
        )
    return quantity * UNITS[source][1] / UNITS[target][1]


def expression(source: str, variables: dict[str, Decimal]) -> Decimal:
    if len(source) > 1000:
        error("INVALID_FORMULA", "Формула слишком длинная")
    try:
        tree = ast.parse(source, mode="eval")
    except (SyntaxError, ValueError, RecursionError):
        error("INVALID_FORMULA", "Некорректный синтаксис формулы")
    if sum(1 for _ in ast.walk(tree)) > 150:
        error("INVALID_FORMULA", "Формула слишком сложная")

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return dec(ast.get_source_segment(source, node))
        if isinstance(node, ast.Name):
            if node.id not in variables:
                error("FORMULA_VARIABLE", f"Не задан параметр формулы: {node.id}")
            return variables[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if right == 0:
                error("DIVISION_BY_ZERO", "В формуле получено деление на ноль")
            return left / right
        error("UNSAFE_FORMULA", "В формуле разрешены только числа, параметры и действия + − × ÷")

    try:
        with localcontext() as context:
            context.prec = 40
            result = visit(tree)
            if not result.is_finite() or abs(result) > Decimal("1e20"):
                error("FORMULA_OVERFLOW", "Результат формулы выходит за допустимые границы")
            return result
    except DecimalException:
        error("FORMULA_ARITHMETIC", "Не удалось выполнить десятичное вычисление формулы")


def validate_profile(profile: dict) -> None:
    for currency in (profile["management_currency"], profile["sale_currency"]):
        precision = profile["currency_precision"].get(currency)
        if type(precision) is not int or not 0 <= precision <= 6:
            error("CURRENCY_PRECISION", "Укажите точность каждой используемой валюты от 0 до 6")
    constants = profile["constants"]
    if any(not re.fullmatch(r"[a-z][a-z0-9_]{0,59}", name) or name in BUILT_INS for name in constants):
        error("PROFILE_CONSTANT", "Название коэффициента недопустимо или зарезервировано")
    for value in constants.values():
        dec(value)
    names = set(constants) | BUILT_INS
    outputs = set()
    for formula in profile["formulas"]:
        name = formula["name"]
        if name in names:
            error("DUPLICATE_FORMULA", "Формула не может переопределить исходный параметр")
        try:
            tree = ast.parse(formula["expression"], mode="eval")
        except SyntaxError:
            error("INVALID_FORMULA", "Некорректный синтаксис формулы")
        # Unknown expense names are allowed here; validated against actual calculation inputs.
        variables = {node.id: Decimal("1") for node in ast.walk(tree) if isinstance(node, ast.Name)}
        try:
            expression(formula["expression"], variables)
        except Exception as exc:
            if getattr(exc, "code", None) != "DIVISION_BY_ZERO":
                raise
        outputs.add(name)
        names.add(name)
    if not REQUIRED_OUTPUTS <= outputs:
        error(
            "PROFILE_OUTPUTS",
            "Профиль обязан определять customs_base, duty, import_tax, cost, cash_need, reward, sale_net, sale_tax",
        )
    if profile["reward_enabled"] and (
        not profile["reward_label"].strip() or not profile["reward_basis"].strip()
    ):
        error("REWARD_POLICY", "Укажите название и экономическое основание вознаграждения")


def distribute(
    amount: Decimal, bases: dict[str, Decimal], quantum: Decimal, rounding: str
) -> dict[str, Decimal]:
    if not bases or any(value < 0 for value in bases.values()) or sum(bases.values()) <= 0:
        error("ZERO_ALLOCATION_BASE", "База распределения равна нулю; выберите другой метод")
    total = sum(bases.values())
    result = {
        key: (amount * value / total).quantize(quantum, rounding=ROUNDING[rounding])
        for key, value in bases.items()
    }
    winner = min(bases, key=lambda key: (-bases[key], key))
    result[winner] += amount - sum(result.values())
    if any(value < 0 for value in result.values()):
        error("ALLOCATION_PRECISION", "Точность валюты не позволяет распределить статью выбранным способом")
    return result


def calculate(profile: dict, selections: list[dict], expenses: list[dict], rates: list[dict]) -> dict:
    with localcontext() as context:
        context.prec = 48
        return _calculate(profile, selections, expenses, rates)


def _calculate(profile: dict, selections: list[dict], expenses: list[dict], rates: list[dict]) -> dict:
    validate_profile(profile)
    management = profile["management_currency"]
    sale = profile["sale_currency"]
    rounding = ROUNDING[profile["rounding"]]
    management_quantum = Decimal(1).scaleb(-profile["currency_precision"][management])
    sale_quantum = Decimal(1).scaleb(-profile["currency_precision"][sale])
    rate_map = {management: Decimal("1")}
    for rate in rates:
        currency = rate["currency"]
        if currency in rate_map:
            error("DUPLICATE_RATE", "Валюта указана в курсах повторно")
        rate_map[currency] = dec(rate["management_per_unit"]) / dec(rate["quoted_units"])
        if rate_map[currency] <= 0:
            error("INVALID_RATE", "Курс должен быть положительным")
    if sale not in rate_map:
        error("RATE_REQUIRED", "Не задан курс валюты продажи")
    sources = {(line["quote"]["supplier_id"], line["quote"]["currency"]) for line in selections}
    if len(sources) != 1:
        error("QUOTE_GROUP", "Одно КП должно содержать одного поставщика и одну исходную валюту")
    if profile["require_same_sale_currency"] and next(iter(sources))[1] != sale:
        error("SALE_CURRENCY", "Профиль требует совпадения исходной валюты и валюты продажи")
    if len({line["quote"]["id"] for line in selections}) != len(selections):
        error("DUPLICATE_SELECTION", "Квота выбрана повторно")
    contexts = {}
    for line in selections:
        quote = line["quote"]
        if quote["currency"] not in rate_map:
            error("RATE_REQUIRED", "Не задан курс валюты закупки")
        amount = convert(dec(line["quantity"]), line["unit"], quote["price_unit"])
        contexts[quote["id"]] = {
            "purchase": amount * dec(quote["price"]) * rate_map[quote["currency"]],
            "quantity": amount,
            "expenses_cost": Decimal("0"),
            "expenses_cash": Decimal("0"),
        }
    expense_names = set()
    allocations = []
    for expense in expenses:
        name = expense["name"]
        if (
            name in expense_names
            or name in BUILT_INS
            or name in profile["constants"]
            or name in {f["name"] for f in profile["formulas"]}
        ):
            error("EXPENSE_NAME", "Название статьи повторяется или зарезервировано")
        expense_names.add(name)
        if expense["currency"] not in rate_map:
            error("RATE_REQUIRED", "Не задан курс валюты статьи расходов")
        amount = (dec(expense["amount"]) * rate_map[expense["currency"]]).quantize(
            management_quantum, rounding=rounding
        )
        method = expense["method"]
        if method == "manual":
            parts = {key: dec(value) for key, value in expense["manual"].items()}
            if (
                set(parts) != set(contexts)
                or any(value < 0 or value != value.quantize(management_quantum) for value in parts.values())
                or sum(parts.values()) != amount
            ):
                error(
                    "MANUAL_ALLOCATION",
                    "Ручное распределение в управленческой валюте должно точно совпадать с суммой статьи",
                )
        else:
            bases = {
                line["quote"]["id"]: contexts[line["quote"]["id"]]["purchase"]
                if method == "purchase"
                else dec(line.get(method) or "0")
                for line in selections
            }
            parts = distribute(amount, bases, management_quantum, profile["rounding"])
        allocations.append(
            {"name": name, "amount": str(amount), "parts": {key: str(value) for key, value in parts.items()}}
        )
        for key, value in parts.items():
            contexts[key][name] = value
            if expense["include_in_cost"]:
                contexts[key]["expenses_cost"] += value
            if expense["include_in_cash"]:
                contexts[key]["expenses_cash"] += value
    rows = []
    for selection in selections:
        quote = selection["quote"]
        variables = {name: dec(value) for name, value in profile["constants"].items()}
        if not set(selection.get("variables", {})) <= set(variables):
            error("UNKNOWN_PARAMETER", "Строка может изменять только объявленные коэффициенты профиля")
        variables.update({name: dec(value) for name, value in selection.get("variables", {}).items()})
        variables.update(contexts[quote["id"]])
        for formula in profile["formulas"]:
            variables[formula["name"]] = expression(formula["expression"], variables)
        if any(variables[name] < 0 for name in REQUIRED_OUTPUTS):
            error("NEGATIVE_RESULT", "Формула вернула отрицательную финансовую сумму")
        if not profile["reward_enabled"] and variables["reward"] != 0:
            error("REWARD_DISABLED", "Вознаграждение отключено в профиле")
        net = (variables["sale_net"] / rate_map[sale]).quantize(sale_quantum, rounding=rounding)
        tax = (variables["sale_tax"] / rate_map[sale]).quantize(sale_quantum, rounding=rounding)
        rows.append(
            {
                "line_id": quote["id"],
                "item_id": quote["item_id"],
                "quote_id": quote["id"],
                "quote_revision": quote["revision"],
                "product": selection["product"],
                "quantity": str(selection["quantity"]),
                "unit": selection["unit"],
                "net": str(net),
                "tax": str(tax),
                "total": str(net + tax),
                "unit_price": str(
                    (net / dec(selection["quantity"])).quantize(Decimal("0.00000001"), rounding=rounding)
                ),
                "tax_category": profile["tax_category"],
                "detail": {name: str(value) for name, value in variables.items()},
                "quote": quote,
            }
        )
    return {
        "lines": rows,
        "totals": {name: str(sum(dec(row[name]) for row in rows)) for name in ("net", "tax", "total")},
        "currency": sale,
        "management_currency": management,
        "expense_allocations": allocations,
        "rates": rates,
        "profile": profile,
        "algorithm_version": "decimal-dsl-1",
    }


def example_profile() -> dict:
    return {
        "management_currency": "RUB",
        "sale_currency": "RUB",
        "currency_precision": {"RUB": 2},
        "rounding": "half_up",
        "country_of_import": "Условная страна — тест",
        "tax_regime": "Синтетический пример A08, не действующие ставки",
        "constants": {
            "duty_rate": "0.05",
            "import_tax_rate": "0.10",
            "markup": "0.25",
            "sale_tax_rate": "0.15",
        },
        "formulas": [
            {"name": "customs_base", "expression": "purchase + international"},
            {"name": "duty", "expression": "customs_base * duty_rate"},
            {"name": "import_tax", "expression": "(customs_base + duty) * import_tax_rate"},
            {"name": "cost", "expression": "purchase + expenses_cost + duty"},
            {"name": "cash_need", "expression": "purchase + expenses_cash + duty + import_tax"},
            {"name": "reward", "expression": "0"},
            {"name": "sale_net", "expression": "cost * (1 + markup)"},
            {"name": "sale_tax", "expression": "sale_net * sale_tax_rate"},
        ],
        "tax_category": "Условный налог A08",
        "funding_ratio": "1",
        "require_same_sale_currency": False,
        "allow_partial_acceptance": True,
        "allow_multiple_suppliers": True,
        "allow_samples": False,
        "reward_enabled": False,
        "reward_label": "",
        "reward_basis": "",
        "template": {"title": "Коммерческое предложение", "show_cas": True, "show_manufacturer": True},
    }
