#!/usr/bin/env python3
"""Fill a database with a large synthetic CRM for load tests (scripts/load_test.py).

Scale 1.0 gives 100 000 clients, 50 000 requests with three lines each, quotes for every line,
20 000 tasks and 20 000 planned payments. Rows go in with bulk inserts, so the values that ORM
events maintain (internal codes, counterparty search text) are filled in here.

Run it on a fresh, migrated database made for the load test; it refuses ENVIRONMENT=production.
Recorded history cannot be deleted (database triggers forbid it), so start over by recreating the
database rather than emptying it.
"""

import argparse
import random
import sys
import uuid
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import func, insert, select  # noqa: E402

import app.models  # noqa: E402,F401
from app.business.models import CalendarEntry  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.core.models import User  # noqa: E402
from app.crm.models import (  # noqa: E402
    Counterparty,
    Currency,
    Nomenclature,
    Packing,
    ProductGroup,
    QuoteItem,
    QuoteSheet,
    Request,
    RequestItem,
    Task,
)
from app.crm.text_search import search_values  # noqa: E402

BATCH = 5000
CURRENCIES = {"RUB": "Российский рубль", "USD": "Доллар США", "EUR": "Евро"}
STAGES = ("new", "clarification", "collecting_quotes", "quote_given", "calculation", "proposal_sent")
CITIES = ("Москва", "Санкт-Петербург", "Казань", "Екатеринбург", "Новосибирск")


def bulk(db, model, rows: list[dict]) -> None:
    for start in range(0, len(rows), BATCH):
        db.execute(insert(model), rows[start:start + BATCH])
    print(f"  {model.__tablename__}: {len(rows)}")


def ids(rows: Iterable[dict]) -> list[str]:
    return [row["id"] for row in rows]


def counterparty(index: int, code: int, owner_id: str, kind: str, now: datetime) -> dict:
    name = f"{'Клиент' if kind == 'client' else 'Поставщик'} {index} {random.choice(('ООО', 'АО', 'ПАО'))}"
    details = {"city": random.choice(CITIES), "profile": random.choice(("химия", "фармацевтика", "лаборатория"))}
    phone, email, tax_id = f"+7 (495) {index % 1000:03d}-{index % 100:02d}-{index % 97:02d}", f"info{index}@example.com", f"77{index:08d}"
    text, fields = search_values(name=name, tax_id=tax_id, phone=phone, email=email, internal_code=code,
                                 details=details)
    return {"id": str(uuid.uuid4()), "internal_code": code, "name": name, "kind": kind, "owner_id": owner_id,
            "tax_id": tax_id, "phone": phone, "email": email, "details": details, "search_text": text,
            "search_fields": fields, "created_at": now, "version": 1}


def seed(db, scale: float) -> None:
    run = uuid.uuid4().hex[:6]
    now = datetime.now(timezone.utc)
    counts = {"users": max(2, int(50 * scale)), "clients": max(1, int(100_000 * scale)),
              "suppliers": max(1, int(2_000 * scale)), "groups": max(1, int(100 * scale)),
              "nomenclatures": max(1, int(10_000 * scale)), "requests": max(1, int(50_000 * scale)),
              "tasks": max(1, int(20_000 * scale)), "payments": max(1, int(20_000 * scale))}
    print(f"Seeding run {run}: {counts}")

    users = [{"id": str(uuid.uuid4()), "email": f"load-{run}-{index}@example.com", "name": f"Сотрудник {index}",
              "password_hash": "!", "active": True, "created_at": now, "version": 1}
             for index in range(counts["users"])]
    bulk(db, User, users)
    user_ids = ids(users)

    known = {code: row_id for code, row_id in db.execute(select(Currency.code, Currency.id)
                                                          .where(Currency.code.in_(list(CURRENCIES))))}
    missing = [{"id": str(uuid.uuid4()), "code": code, "name": name, "created_at": now, "version": 1}
               for code, name in CURRENCIES.items() if code not in known]
    if missing:
        bulk(db, Currency, missing)
    currency_ids = [*known.values(), *ids(missing)]

    code = db.scalar(select(func.coalesce(func.max(Counterparty.internal_code), 0)))
    clients = [counterparty(index, code + index + 1, user_ids[index % len(user_ids)], "client", now)
               for index in range(counts["clients"])]
    code += len(clients)
    suppliers = [counterparty(index, code + index + 1, user_ids[0], "supplier", now)
                 for index in range(counts["suppliers"])]
    bulk(db, Counterparty, clients + suppliers)
    client_ids, supplier_ids = ids(clients), ids(suppliers)

    group_code = db.scalar(select(func.coalesce(func.max(ProductGroup.internal_code), 0)))
    groups = [{"id": str(uuid.uuid4()), "name": f"Группа {run}-{index}", "slug": f"load-{run}-{index}",
               "internal_code": group_code + index + 1, "active": True, "created_at": now, "version": 1}
              for index in range(counts["groups"])]
    bulk(db, ProductGroup, groups)
    nomenclatures = [{"id": str(uuid.uuid4()), "name": f"Реактив {index}", "article": f"ART-{run}-{index}",
                      "manufacturer": f"Производитель {index % 50}", "product_group_id": groups[index % len(groups)]["id"],
                      "cas": f"{index % 9000 + 1000}-{index % 90 + 10}-{index % 9}", "created_at": now,
                      "updated_at": now, "version": 1}
                     for index in range(counts["nomenclatures"])]
    bulk(db, Nomenclature, nomenclatures)
    packings = [{"id": str(uuid.uuid4()), "nomenclature_id": row["id"], "value": Decimal("1"), "unit": "kg",
                 "display_name": "1 кг", "created_at": now, "version": 1} for row in nomenclatures]
    bulk(db, Packing, packings)

    requests = [{"id": str(uuid.uuid4()), "number": f"LOAD-{run}-{index}", "title": f"Заявка {index}",
                 "client_id": client_ids[index % len(client_ids)], "owner_id": user_ids[index % len(user_ids)],
                 "commercial_stage": random.choice(STAGES),
                 "created_at": now - timedelta(days=random.randint(0, 365)), "version": 1}
                for index in range(counts["requests"])]
    bulk(db, Request, requests)

    items, sheets, quotes = [], [], []
    for index, request in enumerate(requests):
        supplier_id = supplier_ids[index % len(supplier_ids)]
        sheet = {"id": str(uuid.uuid4()), "number": f"Q-{run}-{index}", "request_id": request["id"],
                 "supplier_id": supplier_id, "author_id": request["owner_id"], "created_at": now, "version": 1}
        sheets.append(sheet)
        for line in range(3):
            position = (index * 3 + line) % len(nomenclatures)
            nomenclature, packing = nomenclatures[position], packings[position]
            currency_id = currency_ids[(index + line) % len(currency_ids)]
            item = {"id": str(uuid.uuid4()), "request_id": request["id"], "description": nomenclature["name"],
                    "nomenclature_id": nomenclature["id"], "packing_id": packing["id"],
                    "product_group_id": nomenclature["product_group_id"], "supplier_id": supplier_id,
                    "purchase_currency_id": currency_id, "purchase_price": Decimal(random.randint(100, 10_000)),
                    "quantity": Decimal(random.randint(1, 50)), "unit": "kg", "created_at": now, "version": 1}
            items.append(item)
            quotes.append({"id": str(uuid.uuid4()), "quote_id": sheet["id"], "supplier_id": supplier_id,
                           "nomenclature_id": nomenclature["id"], "packing_id": packing["id"],
                           "quantity": item["quantity"], "unit_price": item["purchase_price"],
                           "currency_id": currency_id, "delivery_days": random.randint(5, 60), "quoted_at": now,
                           "valid_until": now + timedelta(days=30), "source_request_item_id": item["id"],
                           "author_id": request["owner_id"], "created_at": now, "version": 1})
    bulk(db, RequestItem, items)
    bulk(db, QuoteSheet, sheets)
    bulk(db, QuoteItem, quotes)

    # Due dates from a month ago to a month ahead, so reminders and escalations have work to do.
    bulk(db, Task, [{"id": str(uuid.uuid4()), "title": f"Задача {index}", "entity_type": "request",
                     "entity_id": requests[index % len(requests)]["id"], "assignee_id": user_ids[index % len(user_ids)],
                     "author_id": user_ids[0], "due_at": now + timedelta(hours=random.randint(-720, 720)),
                     "status": random.choice(("assigned", "in_progress", "done")), "created_at": now, "version": 1}
                    for index in range(counts["tasks"])])
    today = date.today()
    bulk(db, CalendarEntry, [{"id": str(uuid.uuid4()), "direction": random.choice(("income", "expense")),
                              "planned_date": today + timedelta(days=random.randint(-60, 120)),
                              "amount": Decimal(random.randint(1_000, 1_000_000)), "currency": "RUB",
                              "purpose": f"Оплата по заявке {index}",
                              "request_id": requests[index % len(requests)]["id"],
                              "status": random.choice(("draft", "pending", "approved")),
                              "responsible_id": user_ids[index % len(user_ids)], "author_id": user_ids[0],
                              "created_at": now, "version": 1}
                             for index in range(counts["payments"])])
    db.commit()
    print("Seed complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Заполнить базу синтетическими данными для нагрузочного теста")
    parser.add_argument("--scale", type=float, default=0.01, help="1.0 = 100 000 клиентов и 50 000 заявок")
    parser.add_argument("--seed", type=int, default=1, help="начальное значение генератора случайных чисел")
    args = parser.parse_args()
    if settings.environment == "production":
        raise SystemExit("ENVIRONMENT=production: заполняйте отдельную базу для нагрузочного теста.")
    random.seed(args.seed)
    with SessionLocal() as db:
        seed(db, args.scale)


if __name__ == "__main__":
    main()
