"""The wave's audit trail, including logistics and moves, with access scopes and keyset pagination.

Financial payloads stay in the wave card: this view exposes operational changes and their comments only.
"""

import base64
import binascii
import json
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import aliased

from app.business.models import SupplierOrderLine
from app.core.db import DB, as_utc
from app.core.errors import DomainError, error
from app.core.models import AuditEvent, User
from app.core.security import Actor, request_predicate, require_permission
from app.core.service import prefetch
from app.crm.models import Counterparty, ProductGroup
from app.crm.models import Request as CRMRequest

from .models import Execution, Wave, WaveAllocation

router = APIRouter(tags=["Согласования и поставки"])

STATUSES = {"planned": "Планируется", "assembling": "Комплектуется", "closed": "Закрыта", "shipped": "Отправлена",
            "arrived": "Прибыла", "arrived_ru": "Прибыло в РФ", "delivered": "Доставлено", "completed": "Выполнена", "cancelled": "Отменена"}
KINDS = {"plan": "Плановая", "work": "Рабочая"}
ORDER_STATUSES = {"waiting": "Ожидается", "delivery": "В пути", "arrived_ru": "Прибыло в РФ",
                  "delivered": "Доставлено клиенту", "cancelled": "Отменено"}
EVENT_TITLES = {"ordered": "Заказ размещён", "ready": "Груз готов", "shipped": "Груз отправлен",
                "arrived": "Груз прибыл", "arrived_ru": "Груз прибыл в РФ", "delivered": "Груз доставлен",
                "claim_opened": "Открыта претензия", "claim_resolved": "Претензия закрыта",
                "correction": "Исправлено логистическое событие"}
TITLES = {
    ("wave", "create"): "Волна создана", ("wave", "update"): "Волна изменена",
    ("wave", "archived"): "Волна убрана в архив", ("wave", "restored"): "Волна возвращена из архива",
    ("wave", "completed"): "Поставка выполнена", ("wave", "budget"): "Изменён бюджет волны",
    ("wave_forecast", "create"): "Добавлен плановый объём",
    ("wave_forecast", "update"): "Изменён плановый объём",
    ("wave_forecast", "removed"): "Убран плановый объём",
    ("wave_allocation", "allocate"): "Количество распределено в волну",
    ("execution", "procurement_handoff"): "Заказ передан в закупки в эту волну",
    ("supplier_order_line", "status"): "Изменён статус заказа поставщику",
    ("supplier_order_line", "cancel"): "Отменена позиция заказа поставщику",
}
FIELDS = (("number", "Название"), ("kind", "Вид"), ("supplier_id", "Поставщик"), ("route", "Маршрут"),
          ("origin_country", "Страна отправления"), ("owner_id", "Ответственный"), ("status", "Состояние"),
          ("archived", "Архив"), ("close", "Приём до"), ("departure", "Отправка"), ("arrival", "Прибытие"))
CONFIRMED_REQUEST = "Подтверждён заказ по заявке №"


def generated_request_number(event: AuditEvent) -> str | None:
    """Legacy working-wave creation comments name a request in an otherwise shared wave event."""
    reason = event.reason or ""
    if event.entity_type == "wave" and event.action == "create" and reason.startswith(CONFIRMED_REQUEST):
        return reason[len(CONFIRMED_REQUEST):]
    return None


def pieces(value) -> str:
    text = str(value or "0")
    return text.rstrip("0").rstrip(".") if "." in text else text


class Names:
    """Read operational references in batches; never return raw audit payloads or financial snapshots."""

    def __init__(self, db, events: list[AuditEvent], user: User):
        def ids(*keys: str) -> set[str]:
            return {str(side[key]) for event in events for side in (event.before or {}, event.after or {})
                    for key in keys if side.get(key)}

        self.users = prefetch(db, User, {*(e.actor_id for e in events if e.actor_id), *ids("owner_id")})
        self.counterparties = prefetch(db, Counterparty, ids("supplier_id"))
        self.groups = prefetch(db, ProductGroup, ids("product_group_id"))
        self.orders = prefetch(db, SupplierOrderLine,
                               (e.entity_id for e in events if e.entity_type == "supplier_order_line"))
        self.allocations = prefetch(db, WaveAllocation,
                                     [*(e.entity_id for e in events if e.entity_type == "wave_allocation"),
                                      *ids("allocation_id")])
        self.executions = prefetch(db, Execution,
                                    [*(e.entity_id for e in events if e.entity_type == "execution"),
                                     *(row.execution_id for row in self.allocations.values()),
                                     *(row.execution_id for row in self.orders.values())])
        self.requests = prefetch(db, CRMRequest,
                                  [*(e.entity_id for e in events if e.entity_type == "request"),
                                   *(row.request_id for row in self.executions.values())])
        generated_numbers = {number for event in events if (number := generated_request_number(event)) is not None}
        self.visible_comment_requests = set(db.scalars(select(CRMRequest.number).where(
            CRMRequest.number.in_(generated_numbers), request_predicate(db, user)))) if generated_numbers else set()
        transfer_waves = {str(row["wave_id"]) for event in events for row in
                          (event.after or {}).get("allocations", []) if isinstance(row, dict) and row.get("wave_id")}
        self.waves = prefetch(db, Wave, {*ids("wave_id", "target_wave_id"), *transfer_waves,
                                         *(row.wave_id for row in self.allocations.values())})

    def value(self, key: str, side: dict) -> str | None:
        if key in ("close", "departure", "arrival"):
            week, year = side.get(f"{key}_week"), side.get(f"{key}_year")
            return f"неделя {week}, {year}" if week and year else (str(side.get(f"{key}_date") or "") or None)
        raw = side.get(key)
        if raw is None or raw == "":
            return None
        if key == "owner_id":
            user = self.users.get(str(raw))
            return user.name if user else None
        if key == "supplier_id":
            supplier = self.counterparties.get(str(raw))
            return supplier.name if supplier else None
        if key == "status":
            return STATUSES.get(str(raw), str(raw))
        if key == "kind":
            return KINDS.get(str(raw), str(raw))
        if key == "archived":
            return "Да" if raw else "Нет"
        return str(raw)

    def position(self, execution_id: str | None, quantity=None) -> str:
        execution = self.executions.get(execution_id or "")
        request = self.requests.get(execution.request_id) if execution else None
        return " · ".join(str(value) for value in (
            f"Заявка №{request.number}" if request else None,
            (execution.snapshot or {}).get("description") if execution else None,
            f"{pieces(quantity)} {execution.unit if execution else 'ед.'}" if quantity is not None else None) if value)

    def comment(self, event: AuditEvent) -> str | None:
        number = generated_request_number(event)
        return None if number is not None and number not in self.visible_comment_requests else (event.reason or None)


def describe(event: AuditEvent, wave_id: str, names: Names) -> dict:
    before, after = event.before or {}, event.after or {}
    title = TITLES.get((event.entity_type, event.action), "Изменение")
    changes: list[dict] = []
    details = None
    if event.entity_type == "wave" and event.action in ("update", "archived", "restored", "completed"):
        for key, label in FIELDS:
            old, new = names.value(key, before), names.value(key, after)
            if old != new:
                changes.append({"label": label, "before": old, "after": new})
    elif event.entity_type == "wave" and event.action == "create":
        details = " · ".join(value for value in (after.get("number"), names.value("supplier_id", after),
                                                 after.get("route")) if value)
    elif event.entity_type == "wave_forecast":
        group = names.groups.get(str(after.get("product_group_id") or before.get("product_group_id") or ""))
        quantity = after.get("target_quantity") or before.get("target_quantity")
        details = " · ".join(value for value in (group.name if group else None,
                                                 f"{pieces(quantity)} шт." if quantity else None) if value)
        for key, label in (("target_quantity", "Плановый объём, шт."), ("active", "Участвует в расчёте")):
            old, new = before.get(key), after.get(key)
            if old != new:
                def display(value):
                    return ("Да" if value else "Нет") if key == "active" else pieces(value)
                changes.append({"label": label, "before": display(old) if old is not None else None,
                                "after": display(new) if new is not None else None})
    elif event.entity_type == "wave_allocation":
        allocation = names.allocations.get(event.entity_id)
        execution_id = allocation.execution_id if allocation else after.get("execution_id")
        if event.action == "transfer":
            source_id = allocation.wave_id if allocation else before.get("wave_id")
            destinations = [row for row in after.get("allocations", []) if row.get("wave_id") != source_id]
            target_id = destinations[0].get("wave_id") if destinations else after.get("target_wave_id")
            source, target = names.waves.get(str(source_id or "")), names.waves.get(str(target_id or ""))
            quantity = sum((Decimal(str(row.get("quantity") or 0)) for row in destinations), Decimal(0))
            title = "Позиция перенесена из волны" if wave_id == source_id else "Позиция перенесена в волну"
            details = " · ".join(value for value in (names.position(execution_id, quantity),
                              f"{source.number if source else 'другая волна'} → {target.number if target else 'другая волна'}") if value)
        else:
            details = names.position(execution_id, after.get("quantity"))
    elif event.entity_type == "fulfillment_event":
        allocation = names.allocations.get(str(after.get("allocation_id") or ""))
        title = EVENT_TITLES.get(event.action, "Изменено логистическое событие")
        details = names.position(allocation.execution_id if allocation else None, after.get("quantity"))
    elif event.entity_type == "supplier_order_line":
        order = names.orders.get(event.entity_id)
        details = names.position(order.execution_id if order else None, after.get("quantity"))
        old, new = before.get("status"), after.get("status")
        if old != new:
            changes.append({"label": "Статус позиции", "before": ORDER_STATUSES.get(str(old), old),
                            "after": ORDER_STATUSES.get(str(new), new)})
    elif event.entity_type == "execution":
        details = names.position(event.entity_id, after.get("quantity"))
    elif event.entity_type == "request":
        request = names.requests.get(event.entity_id)
        number = f"Заявка №{request.number}" if request else "Заявка"
        joined = after.get("wave_id") == wave_id
        other = names.waves.get(str(before.get("wave_id") if joined else after.get("wave_id")) or "")
        title = f"{number} перешла в волну" if joined else f"{number} ушла из волны"
        if joined and event.action == "create":
            title = f"{number} создана в этой волне"
        if other:
            details = f"{'из' if joined else 'в'} волн{'ы' if joined else 'у'} «{other.number}»"
    actor = names.users.get(event.actor_id or "")
    return {"id": event.id, "created_at": as_utc(event.created_at).isoformat(), "actor_name": actor.name if actor else None,
            "title": title, "changes": changes, "details": details or None, "comment": names.comment(event)}


def read_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        values = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(values, list) or len(values) != 2 or not isinstance(values[1], str) or not 1 <= len(values[1]) <= 36:
            raise ValueError("shape")
        return as_utc(datetime.fromisoformat(values[0])), values[1]
    except (ValueError, TypeError, binascii.Error):
        raise DomainError("INVALID_CURSOR", "Не удалось прочитать страницу истории", 422, field="cursor") from None


def event_cursor(event: AuditEvent) -> str:
    value = json.dumps([as_utc(event.created_at).isoformat(), event.id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


@router.get("/waves/{wave_id}/history")
def wave_history(wave_id: str, db: DB, user: Actor, limit: int = Query(200, ge=1, le=500),
                 cursor: str | None = Query(None, max_length=512)):
    require_permission(db, user, "requests.read")
    if not db.get(Wave, wave_id):
        error("NOT_FOUND", "Волна не найдена", 404)
    accessible = select(CRMRequest.id).where(request_predicate(db, user))
    execution_ids = select(Execution.id).where(Execution.request_id.in_(accessible))
    allocations = select(WaveAllocation.id).where(WaveAllocation.wave_id == wave_id,
                                                  WaveAllocation.execution_id.in_(execution_ids))
    # Transfers keep the original allocation immutable and create new rows in both waves. Older audit payloads
    # had no top-level wave_id; previous_id makes them discoverable without rewriting the historical audit.
    source_allocations = select(WaveAllocation.previous_id).where(WaveAllocation.wave_id == wave_id,
                             WaveAllocation.execution_id.in_(execution_ids), WaveAllocation.previous_id.is_not(None))
    old, new = AuditEvent.before["wave_id"].as_string(), AuditEvent.after["wave_id"].as_string()
    linked = or_(new == wave_id, old == wave_id)
    ended = aliased(AuditEvent)
    ended_at = select(func.min(ended.created_at)).where(or_(
        and_(ended.entity_type == "wave_allocation", ended.entity_id == WaveAllocation.id, ended.action == "transfer"),
        and_(ended.entity_type == "wave", ended.entity_id == WaveAllocation.wave_id,
             ended.action == "update", ended.after["status"].as_string() == "cancelled"),
    )).correlate(WaveAllocation).scalar_subquery()
    # A status of an order belongs to the wave it occupied at that moment, not every wave it ever passed through.
    order_in_wave = select(WaveAllocation.id).join(SupplierOrderLine,
                         SupplierOrderLine.execution_id == WaveAllocation.execution_id).where(
                         WaveAllocation.wave_id == wave_id, SupplierOrderLine.id == AuditEvent.entity_id,
                         SupplierOrderLine.execution_id.in_(execution_ids),
                         AuditEvent.created_at >= WaveAllocation.created_at,
                         or_(WaveAllocation.active.is_(True), AuditEvent.created_at <= ended_at))
    query = select(AuditEvent).where(or_(
        and_(AuditEvent.entity_type == "wave", AuditEvent.entity_id == wave_id),
        and_(AuditEvent.entity_type == "wave_forecast", linked),
        and_(AuditEvent.entity_type == "wave_allocation", or_(AuditEvent.entity_id.in_(allocations),
                       and_(AuditEvent.action == "transfer", AuditEvent.entity_id.in_(source_allocations)))),
        and_(AuditEvent.entity_type == "execution", linked, AuditEvent.entity_id.in_(execution_ids)),
        and_(AuditEvent.entity_type == "fulfillment_event", AuditEvent.after["allocation_id"].as_string().in_(allocations)),
        and_(AuditEvent.entity_type == "supplier_order_line", order_in_wave.exists()),
        and_(AuditEvent.entity_type == "request", AuditEvent.entity_id.in_(accessible), linked,
             old.is_distinct_from(new))))
    if cursor:
        at, identifier = read_cursor(cursor)
        query = query.where(or_(AuditEvent.created_at < at, and_(AuditEvent.created_at == at, AuditEvent.id < identifier)))
    rows = list(db.scalars(query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(limit + 1)))
    events = rows[:limit]
    names = Names(db, events, user)
    return {"items": [describe(event, wave_id, names) for event in events], "has_more": len(rows) > limit,
            "next_cursor": event_cursor(events[-1]) if len(rows) > limit else None}
