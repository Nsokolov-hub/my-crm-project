#!/usr/bin/env python3
import argparse
import random
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import insert, text
from app.core.db import SessionLocal
from app.core.models import User
from app.core.db import Base
from app.crm.models import Counterparty, Request, Seller, RequestItem
from app.commerce.models import SupplierRequest, Quote, Product, Substance, Manufacturer

def truncate_db(db):
    print("Truncating tables...")
    for table in reversed(Base.metadata.sorted_tables):
        db.execute(table.delete())
    db.commit()
    print("Dropping unique constraints that break large null inserts...")
    try:
        db.execute(text('ALTER TABLE quotes DROP CONSTRAINT IF EXISTS quotes_previous_id_key'))
        db.commit()
    except Exception as e:
        print(f"Constraint might not exist: {e}")
        db.rollback()

def seed_data(db, scale: float = 1.0):
    num_users = max(1, int(50 * scale))
    num_clients = int(100_000 * scale)
    num_requests = int(50_000 * scale)
    num_quotes = int(500_000 * scale)
    
    print(f"Generating {num_users} users...")
    users = [{"id": str(uuid.uuid4()), "email": f"user{i}_{uuid.uuid4().hex[:6]}@test.local", "name": f"User {i}", "password_hash": "hash", "active": True, "created_at": datetime.now(timezone.utc), "version": 1} for i in range(num_users)]
    if users:
        db.execute(insert(User), users)
    
    print(f"Generating {num_clients} counterparties...")
    clients = []
    for i in range(num_clients):
        clients.append({
            "id": str(uuid.uuid4()),
            "name": f"Client Company {i}",
            "kind": "client",
            "owner_id": users[i % len(users)]["id"] if users else None,
            "created_at": datetime.now(timezone.utc),
            "version": 1
        })
    if clients:
        for chunk in range(0, len(clients), 10000):
            db.execute(insert(Counterparty), clients[chunk:chunk+10000])

    print(f"Generating {num_requests} requests...")
    requests = []
    for i in range(num_requests):
        requests.append({
            "id": str(uuid.uuid4()),
            "number": f"REQ-{i}",
            "title": f"Large request {i}",
            "client_id": clients[i % len(clients)]["id"] if clients else None,
            "owner_id": users[i % len(users)]["id"] if users else None,
            "commercial_stage": random.choice(["new", "in_progress", "closed"]),
            "created_at": datetime.now(timezone.utc) - timedelta(days=random.randint(0, 365)),
            "version": 1
        })
    if requests:
        for chunk in range(0, len(requests), 10000):
            db.execute(insert(Request), requests[chunk:chunk+10000])

    print(f"Generating request items...")
    items = []
    for r in requests:
        items.append({
            "id": str(uuid.uuid4()),
            "request_id": r["id"],
            "description": "Item",
            "quantity": Decimal("10"),
            "unit": "kg",
            "revision": 1,
            "created_at": datetime.now(timezone.utc),
            "version": 1
        })
    if items:
        for chunk in range(0, len(items), 10000):
            db.execute(insert(RequestItem), items[chunk:chunk+10000])
            
    print(f"Generating products...")
    substances = [{"id": str(uuid.uuid4()), "cas": str(uuid.uuid4())[:15], "name": "Test", "created_at": datetime.now(timezone.utc), "version": 1} for _ in range(10)]
    db.execute(insert(Substance), substances)
    manufacturers = [{"id": str(uuid.uuid4()), "name": f"Mfg_{uuid.uuid4().hex[:6]}", "normalized_name": f"mfg_{uuid.uuid4().hex[:6]}", "created_at": datetime.now(timezone.utc), "version": 1} for _ in range(10)]
    db.execute(insert(Manufacturer), manufacturers)
    products = [{"id": str(uuid.uuid4()), "substance_id": substances[0]["id"], "manufacturer_id": manufacturers[0]["id"], "fingerprint": str(uuid.uuid4()), "name": "Prod", "unit": "kg", "created_at": datetime.now(timezone.utc), "version": 1} for _ in range(10)]
    db.execute(insert(Product), products)

    print(f"Generating {num_quotes} quotes...")
    sellers = [str(uuid.uuid4()) for _ in range(10)]
    for s in sellers:
        db.execute(insert(Counterparty), [{"id": s, "name": f"Seller {s}", "currency": "RUB", "owner_id": users[0]["id"], "kind": "seller", "created_at": datetime.now(timezone.utc), "version": 1}])

    rfqs = [{"id": str(uuid.uuid4()), "request_id": requests[i % len(requests)]["id"], "supplier_id": clients[i % len(clients)]["id"], "snapshot": {}, "file_key": "dummy", "sha256": "dummy", "status": "active", "author_id": users[0]["id"] if users else None, "created_at": datetime.now(timezone.utc), "version": 1} for i in range(len(requests))]
    if rfqs:
        for chunk in range(0, len(rfqs), 10000):
            db.execute(insert(SupplierRequest), rfqs[chunk:chunk+10000])
            

    quotes = []
    prev_id = None
    for i in range(num_quotes):
        qid = str(uuid.uuid4())
        quotes.append({
            "id": qid,
            "request_id": requests[i % len(requests)]["id"] if requests else None,
            "item_id": items[i % len(items)]["id"] if items else None,
            "item_revision": 1,
            "revision": 1,
            "product_id": products[i % len(products)]["id"],
            "price_unit": "kg",
            "available_quantity": Decimal("100"),
            "valid_until": datetime.now(timezone.utc) + timedelta(days=30),
            "revision_reason": "",
            "supplier_id": sellers[i % len(sellers)],
            "supplier_request_id": rfqs[i % len(rfqs)]["id"] if rfqs else None,
            "previous_id": prev_id,
            "price": Decimal(random.randint(100, 10000)),
            "currency": "RUB",
            "terms": {},
            "attachments": [],
            "is_analogue": False,
            "sample": False,
            "requires_confirmation": False,
            "multiple": Decimal("1"),
            "minimum_quantity": Decimal("0"),
            "author_id": users[0]["id"] if users else None,
            "created_at": datetime.now(timezone.utc),
            "version": 1
        })
        prev_id = qid
        
    if quotes:
        for chunk in range(0, len(quotes), 5000):
            db.execute(insert(Quote), quotes[chunk:chunk+5000])

            
    db.commit()
    print("Seed complete.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=float, default=0.01, help="1.0 = 100k clients, 50k reqs, 500k quotes")
    parser.add_argument("--truncate", action="store_true")
    args = parser.parse_args()
    
    with SessionLocal() as db:
        if args.truncate:
            truncate_db(db)
        seed_data(db, args.scale)

if __name__ == "__main__":
    main()
