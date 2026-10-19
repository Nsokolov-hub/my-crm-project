"""Rubles per unit of a currency, by the exchange rates of the published calculation profile."""

from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from .models import CalculationProfile


def published_profile(db) -> CalculationProfile | None:
    """The calculation profile in force today for the itemized methodology, the latest published first."""
    today = date.today()
    return db.scalar(select(CalculationProfile).where(
        CalculationProfile.status == "published", CalculationProfile.effective_from <= today,
        (CalculationProfile.effective_until.is_(None)) | (CalculationProfile.effective_until >= today),
        CalculationProfile.definition["methodology"].as_string() == "itemized_v2",
    ).order_by(CalculationProfile.created_at.desc(), CalculationProfile.id.desc()).limit(1))


def profile_rates(db) -> dict:
    """{"rates": {currency: rubles per unit}, "profile": name or None}; a broken rate is left out."""
    profile = published_profile(db)
    rates = {"RUB": Decimal("1")}
    for rate in (profile.definition.get("exchange_rates") or []) if profile else []:
        try:
            units = Decimal(str(rate.get("quoted_units") or "1"))
            per_unit = Decimal(str(rate["management_per_unit"]))
        except (InvalidOperation, KeyError, TypeError):
            continue
        if units > 0 and per_unit > 0 and rate.get("currency"):
            rates[str(rate["currency"]).upper()] = per_unit / units
    return {"rates": rates, "profile": profile.name if profile else None}


def in_rubles(amount, currency: str | None, rates: dict[str, Decimal]) -> str | None:
    rate = rates.get((currency or "").upper())
    if rate is None or amount is None:
        return None
    return str((Decimal(str(amount)) * rate).quantize(Decimal("0.01")))
