"""Planned and working waves.

A planned wave holds one supplier's planned volume; calculations divide the shared costs by it. Real orders
never stay in a plan: a confirmed order goes to the plan's open working wave, a copy of the plan made when
the first such order arrives. Archived waves are kept for history and take no new work.
"""

from copy import deepcopy

from sqlalchemy import select

from app.core.errors import error
from app.core.service import advisory, audit, lock, notify, serialize

from .models import Wave

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
