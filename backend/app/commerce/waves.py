"""Planned and working waves.

A planned wave holds one supplier's planned volume; calculations divide the shared costs by it. Real orders
never stay in a plan: a confirmed order goes to the plan's open working wave, a copy of the plan made when
the first such order arrives. Archived waves are kept for history and take no new work.
"""

from copy import deepcopy
from datetime import date

from sqlalchemy import select

from app.core.errors import error
from app.core.service import advisory, audit, lock, notify, serialize

from .models import Execution, Wave, WaveAllocation

OPEN_WAVES = {"planned", "assembling"}
# What a working wave takes over from its plan: route, dates, owner and budget, but not the planned volume.
COPIED_FIELDS = (
    "supplier_id", "route", "origin_country", "owner_id", "details",
    "close_date", "close_week", "close_year", "departure_date", "departure_week", "departure_year",
    "arrival_date", "arrival_week", "arrival_year", "budget_profile_id", "budget_expenses", "budget_rates",
)


def usable(wave: Wave, field: str | None = None) -> Wave:
    if wave.archived:
        error("WAVE_ARCHIVED", f"Волна «{wave.number}» в архиве. Верните её из архива или выберите другую волну",
              field=field)
    return wave


def active_plan(db, supplier_id: str | None) -> Wave | None:
    return db.scalar(select(Wave).where(Wave.supplier_id == supplier_id, Wave.kind == "plan",
                                        Wave.archived.is_(False)))


def working_number(db, plan: Wave) -> str:
    """«План — рабочая 2»: the name shows which plan a working wave came from."""
    prefix = f"{plan.number[:60].strip()} — рабочая"
    sequence = len(db.scalars(select(Wave.id).where(Wave.plan_id == plan.id)).all()) + 1
    while True:
        number = f"{prefix} {sequence}"
        advisory(db, f"wave-number:{number}")
        if not db.scalar(select(Wave.id).where(Wave.number == number)):
            return number
        sequence += 1


def working_wave(db, user, wave: Wave, reason: str) -> Wave:
    """The wave that takes a real order. The caller holds the lock of `wave`, so one plan makes one copy."""
    if wave.kind == "work":
        return wave
    current = db.scalar(
        select(Wave).where(Wave.plan_id == wave.id, Wave.archived.is_(False), Wave.status.in_(OPEN_WAVES))
        .order_by(Wave.close_date, Wave.created_at, Wave.id).limit(1)
    )
    if current:
        advisory(db, f"wave:{current.id}")
        return lock(db, Wave, current.id)
    copy = Wave(kind="work", plan_id=wave.id, status="assembling", number=working_number(db, wave),
                **{field: deepcopy(getattr(wave, field)) for field in COPIED_FIELDS})
    db.add(copy)
    db.flush()
    audit(db, user, "wave", copy.id, "create", after=serialize(copy), reason=reason)
    notify(db, copy.owner_id, f"wave-working:{copy.id}", f"Создана рабочая волна {copy.number}"[:250],
           "wave", copy.id)
    return copy


def wave_requests(db, wave: Wave) -> set[str]:
    """Requests with orders in the wave or, for a plan, in its working waves."""
    waves = [wave.id] + (list(db.scalars(select(Wave.id).where(Wave.plan_id == wave.id))) if wave.kind == "plan" else [])
    return set(db.scalars(select(Execution.request_id).join(WaveAllocation, WaveAllocation.execution_id == Execution.id)
                          .where(WaveAllocation.wave_id.in_(waves), WaveAllocation.active.is_(True))))


def finish_wave(db, user, wave: Wave, reason: str) -> None:
    """«Поставка выполнена»: a working wave is completed and archived; a plan takes its working waves along.

    The caller holds the lock of `wave`. The delivery events of the allocations are not required: the delivery
    is confirmed by the leader or by every order of the wave delivered to its client.
    """
    from app.crm.models import Request as CRMRequest

    owners = {db.get(CRMRequest, request_id).owner_id: request_id for request_id in wave_requests(db, wave)}
    targets = [wave]
    if wave.kind == "plan":
        for row_id in db.scalars(select(Wave.id).where(Wave.plan_id == wave.id, Wave.archived.is_(False))
                                 .order_by(Wave.id)):
            advisory(db, f"wave:{row_id}")
            targets.append(lock(db, Wave, row_id))
    for row in targets:
        before = serialize(row)
        if row.kind == "work" and row.status != "cancelled":
            row.status = "completed"
        row.archived = True
        row.version += 1
        audit(db, user, "wave", row.id, "completed", before=before, after=serialize(row), reason=reason)
    for owner_id, request_id in owners.items():
        notify(db, owner_id, f"wave-completed:{wave.id}", f"Поставка по волне {wave.number} выполнена"[:250],
               "request", request_id)


def finish_delivered_waves(db, user, execution_ids: list[str]) -> list[str]:
    """Working waves whose every live order is delivered to its client go to the archive as delivered.

    The plan follows once its intake week is over and none of its working waves is left open.
    """
    from app.business.models import SupplierOrderLine

    finished = []
    wave_ids = set(db.scalars(select(WaveAllocation.wave_id).where(
        WaveAllocation.execution_id.in_(execution_ids), WaveAllocation.active.is_(True)))) if execution_ids else set()
    for wave_id in sorted(wave_ids):
        advisory(db, f"wave:{wave_id}")
        wave = lock(db, Wave, wave_id)
        if wave.archived or wave.kind != "work" or wave.status == "cancelled":
            continue
        live = [execution for execution in db.scalars(
            select(Execution).join(WaveAllocation, WaveAllocation.execution_id == Execution.id)
            .where(WaveAllocation.wave_id == wave.id, WaveAllocation.active.is_(True)))
            if execution.quantity > execution.cancelled_quantity]
        delivered = set(db.scalars(select(SupplierOrderLine.execution_id).where(
            SupplierOrderLine.execution_id.in_([execution.id for execution in live]),
            SupplierOrderLine.status == "delivered"))) if live else set()
        if not live or any(execution.id not in delivered for execution in live):
            continue
        finish_wave(db, user, wave, "Все заказы волны доставлены клиентам")
        finished.append(wave.id)
        plan = db.get(Wave, wave.plan_id) if wave.plan_id else None
        if plan and not plan.archived and plan.close_date < date.today() and not db.scalar(
                select(Wave.id).where(Wave.plan_id == plan.id, Wave.archived.is_(False)).limit(1)):
            advisory(db, f"wave:{plan.id}")
            finish_wave(db, user, lock(db, Wave, plan.id), "Приём заказов закрыт, все заказы доставлены клиентам")
            finished.append(plan.id)
    return finished

