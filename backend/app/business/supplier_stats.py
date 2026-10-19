"""Who was asked for prices and who answered: supplier responses down to request positions.

A position asked from a supplier is priced once the supplier's quote has it. It is declined when the supplier
answered on that request — a quote with other positions, or a reply without prices recorded by hand — but
left the position without a price. Otherwise the supplier has not answered yet. Answer time runs from the first
mail on the request to the first quote recorded after it or the recorded reply.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from sqlalchemy import select

from app.analytics.brands import item_manufacturers
from app.commerce.models import Manufacturer
from app.commerce.procurement import rfq_table
from app.core.config import settings
from app.core.db import DB, as_utc
from app.core.errors import DomainError
from app.core.security import (
    Actor,
    check_request,
    has_request_permission,
    request_predicate,
    require_permission,
)
from app.core.service import plain, prefetch
from app.crm.models import (
    Counterparty,
    Currency,
    Nomenclature,
    Packing,
    QuoteItem,
    QuoteSheet,
    Request,
    RequestItem,
)

from .models import SupplierMail

router = APIRouter(tags=["Платёжный календарь и заказы поставщикам"])
STATUSES = ("priced", "declined", "waiting")


def hours(start: datetime, finish: datetime | None) -> float | None:
    return round((finish - start).total_seconds() / 3600, 1) if finish else None


def mailed_items(db, mails: list[SupplierMail]) -> dict[str, list[str]]:
    """Positions of each mail. Mails sent before positions were recorded are matched by their table rows."""
    legacy = {mail.request_id for mail in mails if mail.item_ids is None}
    items: dict[str, list[RequestItem]] = defaultdict(list)
    for item in db.scalars(select(RequestItem).where(RequestItem.request_id.in_(legacy),
                                                     RequestItem.quote_only.is_(False))
                           .order_by(RequestItem.created_at, RequestItem.id)) if legacy else []:
        items[item.request_id].append(item)
    rows: dict[str, dict[str, Any]] = {}
    result = {}
    for mail in mails:
        if mail.item_ids is not None:
            result[mail.id] = list(mail.item_ids)
            continue
        lines = mail.body.split("\n")
        header = next((index for index, line in enumerate(lines) if "\t" in line), None)
        if header is None:
            result[mail.id] = []
            continue
        columns, sent = lines[header].split("\t"), set(lines[header + 1:])
        found = []
        for item in items[mail.request_id]:
            if item.id not in rows:
                names, [values] = rfq_table(db, [item])
                rows[item.id] = dict(zip(names, values, strict=True))
            line = "\t".join("" if rows[item.id].get(column) is None else str(rows[item.id][column])
                             for column in columns)
            if line in sent:
                found.append(item.id)
        result[mail.id] = found
    return result


@dataclass
class Pair:
    """One position asked from one supplier."""

    supplier_id: str
    request_id: str
    item_id: str
    requested_at: datetime
    priced_at: datetime | None = None
    priced_earlier: bool = False


class Responses:
    """Positions asked from suppliers by mail and what the suppliers answered."""

    def __init__(self, db, mails: list[SupplierMail]) -> None:
        sent_mails = sorted(((as_utc(mail.sent_at), mail) for mail in mails if mail.sent_at), key=lambda row: row[0])
        self.mails = [mail for _, mail in sent_mails]
        positions = mailed_items(db, self.mails)
        self.pairs: dict[tuple[str, str], Pair] = {}
        self.asked: dict[tuple[str, str], datetime] = {}
        self.answered: dict[tuple[str, str], datetime] = {}
        for sent, mail in sent_mails:
            self.asked.setdefault((mail.supplier_id, mail.request_id), sent)
            for item_id in positions[mail.id]:
                self.pairs.setdefault((mail.supplier_id, item_id),
                                      Pair(mail.supplier_id, mail.request_id, item_id, sent))
            if mail.replied_at:
                self.answer((mail.supplier_id, mail.request_id), as_utc(mail.replied_at))
        requests = {request_id for _, request_id in self.asked}
        for supplier_id, request_id, created in db.execute(select(
                QuoteSheet.supplier_id, QuoteSheet.request_id, QuoteSheet.created_at).where(
                QuoteSheet.request_id.in_(requests), QuoteSheet.archived.is_(False))) if requests else []:
            asked = self.asked.get((supplier_id, request_id))
            if asked and as_utc(created) >= asked:
                self.answer((supplier_id, request_id), as_utc(created))
        items = {item_id for _, item_id in self.pairs}
        for supplier_id, item_id, created in db.execute(select(
                QuoteItem.supplier_id, QuoteItem.source_request_item_id, QuoteItem.created_at).where(
                QuoteItem.source_request_item_id.in_(items), QuoteItem.archived.is_(False))) if items else []:
            pair = self.pairs.get((supplier_id, item_id))
            if not pair:
                continue
            if as_utc(created) >= pair.requested_at:
                pair.priced_at = min(pair.priced_at or as_utc(created), as_utc(created))
            else:
                pair.priced_earlier = True

    def answer(self, key: tuple[str, str], moment: datetime) -> None:
        self.answered[key] = min(self.answered.get(key, moment), moment)

    def status(self, pair: Pair) -> str:
        if pair.priced_at or pair.priced_earlier:
            return "priced"
        return "declined" if (pair.supplier_id, pair.request_id) in self.answered else "waiting"

    def answer_hours(self, key: tuple[str, str]) -> float | None:
        return hours(self.asked[key], self.answered.get(key))


def period(date_from: date | None, date_to: date | None) -> tuple[datetime, datetime]:
    zone = ZoneInfo(settings.company_timezone)
    start = datetime.combine(date_from or date.today() - timedelta(days=90), time.min, zone)
    finish = datetime.combine((date_to or date.today()) + timedelta(days=1), time.min, zone)
    return start.astimezone(timezone.utc), finish.astimezone(timezone.utc)


def visible_mails(db, user, date_from: date | None, date_to: date | None, supplier_id: str | None = None):
    start, finish = period(date_from, date_to)
    query = select(SupplierMail).where(
        SupplierMail.request_id.in_(select(Request.id).where(request_predicate(db, user), Request.is_test.is_(False))),
        SupplierMail.sent_at >= start, SupplierMail.sent_at < finish)
    if supplier_id:
        query = query.where(SupplierMail.supplier_id == supplier_id)
    return list(db.scalars(query)), start, finish


def brand_names(db, pairs: list[Pair]) -> tuple[dict[str, str | None], dict[str, Manufacturer]]:
    items = list(prefetch(db, RequestItem, (pair.item_id for pair in pairs)).values())
    makers = item_manufacturers(db, items)
    return makers, prefetch(db, Manufacturer, makers.values())


def tally(responses: Responses, pairs: list[Pair]) -> dict[str, int]:
    counts = dict.fromkeys(STATUSES, 0)
    for pair in pairs:
        counts[responses.status(pair)] += 1
    return {"positions": len(pairs), **counts}


def supplier_summary(responses: Responses, supplier_id: str, name: str, pairs: list[Pair],
                     makers: dict[str, str | None], entries: dict[str, Manufacturer]) -> dict[str, Any]:
    keys = [key for key in responses.asked if key[0] == supplier_id]
    times = [value for key in keys if (value := responses.answer_hours(key)) is not None]
    brands: dict[str | None, list[Pair]] = defaultdict(list)
    for pair in pairs:
        brands[makers.get(pair.item_id)].append(pair)
    declined: list[dict[str, Any]] = []
    for maker_id, group in brands.items():
        counts = tally(responses, group)
        if maker_id and counts["declined"]:
            declined.append({"manufacturer_id": maker_id, "name": entries[maker_id].name, **counts})
    declined.sort(key=lambda row: (-row["declined"], row["name"].casefold()))
    answered = sum(1 for key in keys if key in responses.answered)
    return {
        "id": supplier_id, "supplier_id": supplier_id, "name": name, "requests": len(keys),
        "answered_requests": answered,
        "answer_rate": round(answered / len(keys) * 100, 1) if keys else None,
        "average_hours": round(sum(times) / len(times), 1) if times else None,
        "median_hours": round(median(times), 1) if times else None,
        **tally(responses, pairs), "declined_brands": declined,
    }


@router.get("/analytics/suppliers")
def supplier_report(db: DB, user: Actor, date_from: date | None = None, date_to: date | None = None):
    """Suppliers asked for prices in the period: how many answered, how fast, and which brands they leave unpriced."""
    require_permission(db, user, "analytics.reports")
    mails, start, finish = visible_mails(db, user, date_from, date_to)
    responses = Responses(db, mails)
    pairs = list(responses.pairs.values())
    makers, entries = brand_names(db, pairs)
    suppliers = prefetch(db, Counterparty, (supplier_id for supplier_id, _ in responses.asked))
    by_supplier: dict[str, list[Pair]] = defaultdict(list)
    for pair in pairs:
        by_supplier[pair.supplier_id].append(pair)
    rows = [supplier_summary(responses, supplier_id, suppliers[supplier_id].name, by_supplier[supplier_id],
                             makers, entries)
            for supplier_id in {key[0] for key in responses.asked}]
    rows.sort(key=lambda row: (-row["requests"], row["name"].casefold()))
    return plain({
        "items": rows, "total": len(rows),
        "period": {"from": start, "to": finish - timedelta(microseconds=1), "timezone": settings.company_timezone},
        "basis": "Письма с запросом цен, отправленные за период. Цена есть — поставщик дал квоту на позицию. "
                 "Отказ — поставщик ответил по заявке (квотой на другие позиции или ответом без цен), "
                 "а на позицию цены нет. Ждём — ответа по заявке ещё нет. Время ответа — от первого письма по "
                 "заявке до первой квоты после него или отмеченного ответа.",
    })


@router.get("/analytics/suppliers/{supplier_id}")
def supplier_detail(supplier_id: str, db: DB, user: Actor, date_from: date | None = None,
                    date_to: date | None = None):
    """One supplier: answers by brand and every position asked from it in the period."""
    require_permission(db, user, "analytics.reports")
    supplier = db.get(Counterparty, supplier_id)
    if not supplier:
        raise DomainError("NOT_FOUND", "Поставщик не найден", 404)
    mails, _, _ = visible_mails(db, user, date_from, date_to, supplier_id)
    responses = Responses(db, mails)
    pairs = sorted(responses.pairs.values(), key=lambda pair: pair.requested_at, reverse=True)
    makers, entries = brand_names(db, pairs)
    brands: dict[str | None, list[Pair]] = defaultdict(list)
    for pair in pairs:
        brands[makers.get(pair.item_id)].append(pair)
    brand_rows: list[dict[str, Any]] = [{"id": maker_id or "none", "manufacturer_id": maker_id,
                   "name": entries[maker_id].name if maker_id else "Производитель не указан",
                   **tally(responses, group)} for maker_id, group in brands.items()]
    brand_rows.sort(key=lambda row: (row["manufacturer_id"] is None, -row["declined"], -row["positions"]))
    requests = prefetch(db, Request, (pair.request_id for pair in pairs))
    items = prefetch(db, RequestItem, (pair.item_id for pair in pairs))
    nomenclatures = prefetch(db, Nomenclature, (item.nomenclature_id for item in items.values()))
    positions = []
    for pair in pairs:
        item = items[pair.item_id]
        nomenclature = nomenclatures.get(item.nomenclature_id) if item.nomenclature_id else None
        maker_id = makers.get(pair.item_id)
        answered = pair.priced_at or responses.answered.get((pair.supplier_id, pair.request_id))
        positions.append({
            "id": f"{pair.request_id}:{pair.item_id}", "request_id": pair.request_id,
            "request_number": requests[pair.request_id].number, "item_id": pair.item_id,
            "description": nomenclature.name if nomenclature else item.description,
            "manufacturer_name": entries[maker_id].name if maker_id else None,
            "requested_at": pair.requested_at, "status": responses.status(pair),
            "answered_at": answered, "hours": hours(pair.requested_at, answered),
        })
    return plain({"supplier": supplier_summary(responses, supplier.id, supplier.name, pairs, makers, entries),
                  "brands": brand_rows, "positions": positions})


def latest_quotes(db, request_id: str) -> dict[tuple[str, str], QuoteItem]:
    """The latest live quote of each supplier for each position of the request."""
    rows = db.scalars(select(QuoteItem).join(QuoteSheet, QuoteSheet.id == QuoteItem.quote_id).where(
        QuoteSheet.request_id == request_id, QuoteSheet.archived.is_(False), QuoteItem.archived.is_(False))
        .order_by(QuoteItem.quoted_at, QuoteItem.created_at, QuoteItem.id))
    return {(row.supplier_id, row.source_request_item_id): row for row in rows}


def quote_cell(db, quote: QuoteItem, show_price: bool, rates: dict) -> dict[str, Any]:
    """Price, its ruble value by the profile rates and the delivery term of a supplier's quote."""
    from app.commerce.rates import in_rubles

    cell: dict[str, Any] = {"delivery_days": quote.delivery_days,
                            "packing_name": db.get(Packing, quote.packing_id).display_name}
    if show_price:
        currency = db.get(Currency, quote.currency_id).code
        cell.update(unit_price=str(quote.unit_price), currency=currency,
                    unit_price_rub=in_rubles(quote.unit_price, currency, rates))
    return cell


@router.get("/requests/{request_id}/supplier-responses")
def request_responses(request_id: str, db: DB, user: Actor):
    """Which suppliers were asked for each position of the request and what each of them answered.

    A priced cell carries the quoted price, its value in rubles by the published profile and the delivery
    term, so the suppliers can be compared before the proposal. Suppliers with more prices come first.
    """
    from app.commerce.rates import profile_rates

    check_request(db, user, request_id)
    require_permission(db, user, "quotes.write")
    show_price = has_request_permission(db, user, request_id, "finance.purchase.read")
    rates = profile_rates(db)
    quotes = latest_quotes(db, request_id)
    mails = list(db.scalars(select(SupplierMail).where(SupplierMail.request_id == request_id)))
    responses = Responses(db, mails)
    suppliers = prefetch(db, Counterparty, (mail.supplier_id for mail in mails))
    latest: dict[str, SupplierMail] = {}
    for mail in responses.mails:
        latest[mail.supplier_id] = mail
    items = list(db.scalars(select(RequestItem).where(
        RequestItem.request_id == request_id, RequestItem.archived.is_(False), RequestItem.quote_only.is_(False))
        .order_by(RequestItem.created_at, RequestItem.id)))
    makers = item_manufacturers(db, items)
    entries = prefetch(db, Manufacturer, makers.values())
    nomenclatures = prefetch(db, Nomenclature, (item.nomenclature_id for item in items))
    columns: list[dict[str, Any]] = []
    for supplier_id, asked in sorted(((key[0], value) for key, value in responses.asked.items()),
                                     key=lambda entry: entry[1]):
        key = (supplier_id, request_id)
        mail = latest[supplier_id]
        columns.append({"supplier_id": supplier_id, "name": suppliers[supplier_id].name, "asked_at": asked,
                        "answered_at": responses.answered.get(key), "hours": responses.answer_hours(key),
                        "mail_id": mail.id, "mail_version": mail.version, "replied_at": mail.replied_at,
                        "reply_note": mail.reply_note})
    rows = []
    for number, item in enumerate(items, 1):
        nomenclature = nomenclatures.get(item.nomenclature_id) if item.nomenclature_id else None
        cells = {}
        for column in columns:
            pair = responses.pairs.get((column["supplier_id"], item.id))
            if pair:
                status = responses.status(pair)
                cells[column["supplier_id"]] = {
                    "status": status, "requested_at": pair.requested_at,
                    "answered_at": pair.priced_at, "hours": hours(pair.requested_at, pair.priced_at)}
                quote = quotes.get((column["supplier_id"], item.id))
                if status == "priced" and quote:
                    cells[column["supplier_id"]].update(quote_cell(db, quote, show_price, rates["rates"]))
                column["priced"] = column.get("priced", 0) + (status == "priced")
                column["declined"] = column.get("declined", 0) + (status == "declined")
        maker_id = makers.get(item.id)
        rows.append({"id": item.id, "number": number,
                     "description": nomenclature.name if nomenclature else item.description,
                     "manufacturer_name": entries[maker_id].name if maker_id else None, "cells": cells})
    # Suppliers who priced more positions first; then those who answered; then by the time they were asked.
    columns.sort(key=lambda column: (-column.get("priced", 0), column["answered_at"] is None, column["asked_at"]))
    return plain({"suppliers": columns, "items": rows, "rates_profile": rates["profile"] if show_price else None})
