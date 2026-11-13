"""History of a wave (business remark of 13 November 2026): every change of the wave with the comment it was made
with — edits of the wave itself, its planned volume, orders handed into it, requests that joined or left it.

Only names, weeks, states and quantities are shown: the budget and the prices of the planned volume stay in the
wave card, where the rights to see money apply."""

from fastapi import APIRouter, Query
from sqlalchemy import and_, or_, select

from app.core.db import DB
from app.core.errors import error
from app.core.models import AuditEvent, User
from app.core.security import Actor, require_permission
from app.core.service import prefetch
from app.crm.models import Counterparty, ProductGroup
from app.crm.models import Request as CRMRequest

from .models import Execution, Wave, WaveAllocation

router = APIRouter(tags=["Согласования и поставки"])

STATUSES = {"planned": "Планируется", "assembling": "Комплектуется", "closed": "Закрыта", "shipped": "Отправлена",
            "arrived": "Прибыла", "arrived_ru": "Прибыло в РФ", "delivered": "Доставлено", "completed": "Выполнена", "cancelled": "Отменена"}
KINDS = {"plan": "Плановая", "work": "Рабочая"}
TITLES = {
    ("wave", "create"): "Волна создана",
    ("wave", "update"): "Волна изменена",
    ("wave", "archived"): "Волна убрана в архив",
    ("wave", "restored"): "Волна возвращена из архива",
    ("wave", "completed"): "Поставка выполнена",
    ("wave", "budget"): "Изменён бюджет волны",
    ("wave_forecast", "create"): "Добавлен плановый объём",
    ("wave_forecast", "update"): "Изменён плановый объём",
    ("wave_forecast", "removed"): "Убран плановый объём",
    ("wave_allocation", "allocate"): "Количество распределено в волну",
    ("execution", "procurement_handoff"): "Заказ передан в закупки в эту волну",
}
# Fields of the wave whose change is listed, in the order of the card.
FIELDS = (("number", "Название"), ("kind", "Вид"), ("supplier_id", "Поставщик"), ("route", "Маршрут"),
          ("origin_country", "Страна отправления"), ("owner_id", "Ответственный"), ("status", "Состояние"),
          ("close", "Приём до"), ("departure", "Отправка"), ("arrival", "Прибытие"))
LINKED = ("wave_forecast", "wave_allocation", "execution")


def pieces(value) -> str:
    text = str(value or "0")
    return text.rstrip("0").rstrip(".") if "." in text else text


class Names:
    """Names of the users, suppliers, groups, waves and requests the events refer to, read once."""

    def __init__(self, db, events: list[AuditEvent]):
        def ids(*keys: str) -> set[str]:
            found: set[str] = set()
            for event in events:
                for side in (event.before or {}, event.after or {}):
                    found.update(str(side[key]) for key in keys if side.get(key))
            return found

        self.users = prefetch(db, User, {*(e.actor_id for e in events if e.actor_id), *ids("owner_id")})
        self.counterparties = prefetch(db, Counterparty, ids("supplier_id"))
        self.groups = prefetch(db, ProductGroup, ids("product_group_id"))
        self.waves = prefetch(db, Wave, ids("wave_id"))
        allocations = prefetch(db, WaveAllocation, (e.entity_id for e in events if e.entity_type == "wave_allocation"))
        self.executions = prefetch(db, Execution, [*(e.entity_id for e in events if e.entity_type == "execution"),
                                                   *(row.execution_id for row in allocations.values())])
        self.allocations = allocations
        self.requests = prefetch(db, CRMRequest, [*(e.entity_id for e in events if e.entity_type == "request"),
                                                  *(row.request_id for row in self.executions.values())])

    def value(self, key: str, side: dict) -> str | None:
        if key in ("close", "departure", "arrival"):
            week, year = side.get(f"{key}_week"), side.get(f"{key}_year")
            return f"неделя {week}, {year}" if week and year else (str(side.get(f"{key}_date") or "") or None)
        raw = side.get(key)
        if raw in (None, ""):
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
        return str(raw)

    def request_number(self, execution_id: str | None) -> str | None:
        execution = self.executions.get(execution_id or "")
        request = self.requests.get(execution.request_id) if execution else None
        return f"Заявка №{request.number}" if request else None


def describe(event: AuditEvent, wave_id: str, names: Names) -> dict:
    before, after = event.before or {}, event.after or {}
    title = TITLES.get((event.entity_type, event.action), "Изменение")
    changes: list[dict] = []
    details = None
    if event.entity_type == "wave" and event.action == "update":
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
    elif event.entity_type == "wave_allocation":
        allocation = names.allocations.get(event.entity_id)
        number = names.request_number(allocation.execution_id if allocation else after.get("execution_id"))
        details = " · ".join(value for value in (number, f"{pieces(after.get('quantity'))} ед.") if value)
    elif event.entity_type == "execution":
        execution = names.executions.get(event.entity_id)
        number = names.request_number(event.entity_id)
        unit = execution.unit if execution else ""
        description = (execution.snapshot or {}).get("description") if execution else None
        details = " · ".join(value for value in (number, description,
                                                 f"{pieces(after.get('quantity'))} {unit}".strip()) if value)
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
    return {"id": event.id, "created_at": event.created_at.isoformat(), "actor_name": actor.name if actor else None,
            "title": title, "changes": changes, "details": details or None, "comment": event.reason or None}


@router.get("/waves/{wave_id}/history")
def wave_history(wave_id: str, db: DB, user: Actor, limit: int = Query(200, ge=1, le=500)):
    require_permission(db, user, "requests.read")
    if not db.get(Wave, wave_id):
        error("NOT_FOUND", "Волна не найдена", 404)
    old, new = AuditEvent.before["wave_id"].as_string(), AuditEvent.after["wave_id"].as_string()
    linked = or_(new == wave_id, old == wave_id)
    events = list(db.scalars(
        select(AuditEvent)
        .where(or_(and_(AuditEvent.entity_type == "wave", AuditEvent.entity_id == wave_id),
                   and_(AuditEvent.entity_type.in_(LINKED), linked),
                   # Every edit of a request records its wave; only the edits that changed it belong here.
                   and_(AuditEvent.entity_type == "request", linked, old.is_distinct_from(new))))
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(limit)
    ))
    names = Names(db, events)
    return {"items": [describe(event, wave_id, names) for event in events]}
