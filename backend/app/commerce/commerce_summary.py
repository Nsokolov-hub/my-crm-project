"""Small, list-only commercial summaries built from already filtered snapshots."""

from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def calculation_commerce_summary(
    snapshot: dict, supplier_name: Callable[[str], str | None] | None = None
) -> dict:
    """Return list columns without recalculating or changing the saved calculation.

    The caller passes an access-filtered snapshot. Consequently a denied supplier, purchase or
    profit simply has no source value here and stays hidden in the list projection as well.
    """
    lines = snapshot.get("lines") or []
    totals = snapshot.get("totals") or {}
    suppliers = []
    unresolved_suppliers = []
    delivery_days = []
    purchase_total = Decimal("0")
    profit_total = Decimal("0")
    sale_net_total = Decimal("0")
    line_purchase_found = False
    line_profit_found = False

    for line in lines:
        supplier = line.get("supplier_name") or (line.get("quote") or {}).get("supplier_name")
        if supplier and supplier not in suppliers:
            suppliers.append(supplier)
        elif not supplier:
            supplier_id = line.get("supplier_id") or (line.get("quote") or {}).get("supplier_id")
            if supplier_id and supplier_id not in unresolved_suppliers:
                unresolved_suppliers.append(str(supplier_id))
        if line.get("delivery_days") is not None:
            delivery_days.append(line["delivery_days"])
        detail = line.get("detail") or {}
        purchase = _decimal(detail.get("purchase_rub") if "purchase_rub" in detail else detail.get("purchase"))
        profit = _decimal(detail.get("profit"))
        sale_net = _decimal(line.get("net")) or Decimal("0")
        if purchase is not None:
            line_purchase_found = True
            purchase_total += purchase
        if profit is None and detail.get("cost") not in (None, ""):
            cost = _decimal(detail.get("cost"))
            profit = sale_net - cost if cost is not None else None
        if profit is not None:
            line_profit_found = True
            profit_total += profit
        sale_net_total += sale_net

    if supplier_name:
        for supplier_id in unresolved_suppliers:
            supplier = supplier_name(supplier_id)
            if supplier and supplier not in suppliers:
                suppliers.append(supplier)

    purchase = _decimal(totals.get("purchase_rub"))
    if purchase is None and line_purchase_found:
        purchase = purchase_total
    profit = _decimal(totals.get("profit"))
    if profit is None and line_profit_found:
        profit = profit_total
    sale_net_value = _decimal(totals.get("sale_net") or totals.get("net"))
    if sale_net_value is None:
        sale_net_value = sale_net_total
    sale = totals.get("sale_total") or totals.get("total")
    profitability = profit / sale_net_value * 100 if profit is not None and sale_net_value else None

    return {
        "supplier_names": suppliers,
        "delivery_days": max(delivery_days) if delivery_days else snapshot.get("delivery_days"),
        "profitability_percent": _money(profitability),
        "purchase_amount": _money(purchase),
        "purchase_currency": (snapshot.get("management_currency") or "RUB") if purchase is not None else None,
        "sale_amount": str(sale) if sale not in (None, "") else None,
        "sale_currency": (snapshot.get("currency") or "RUB") if sale not in (None, "") else None,
    }
