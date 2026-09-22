#!/usr/bin/env python3
import argparse
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, func

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.db import SessionLocal
from app.core.models import User
from app.crm.models import Counterparty, Request, Seller
from app.commerce.models import Calculation, CommercialDocument, Payment

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def migrate_data(db, data: dict, dry_run: bool = True):
    stats = defaultdict(int)
    errors = []
    
    admin = db.scalar(select(User).limit(1))
    if not admin:
        admin = User(email="admin@migrate.local", name="Migrator", password_hash="hash", active=True)
        db.add(admin)
        db.flush()
    fallback_user_id = admin.id

    client_map = {}
    for c in data.get("counterparties", []):
        client = db.scalar(select(Counterparty).where(Counterparty.external_id == c["external_id"]))
        if not client:
            client = Counterparty(name=c["name"], external_id=c["external_id"], owner_id=fallback_user_id)
            db.add(client)
            db.flush()
            stats["counterparties_created"] += 1
        client_map[c["legacy_id"]] = client.id

    seller_map = {}
    for s in data.get("sellers", []):
        seller = db.scalar(select(Seller).where(Seller.name == s["name"]))
        if not seller:
            seller = Seller(name=s["name"], currency="RUB", details={"external_id": s["external_id"]})
            db.add(seller)
            db.flush()
            stats["sellers_created"] += 1
        seller_map[s["legacy_id"]] = seller.id
        
    request_map = {}
    for r in data.get("requests", []):
        req = db.scalar(select(Request).where(Request.number == r["number"]))
        if req:
            request_map[r["legacy_id"]] = req.id
            continue
            
        client_id = client_map.get(r["client_legacy_id"])
        
        req = Request(
            number=r["number"],
            title=r["title"],
            client_id=client_id,
            owner_id=fallback_user_id,
            commercial_stage=r.get("stage", "new"),
            archived=r.get("archived", False),
            created_at=datetime.fromisoformat(r["created_at"]).astimezone(timezone.utc)
        )
        db.add(req)
        db.flush()
        stats["requests_created"] += 1
        request_map[r["legacy_id"]] = req.id

        if "legacy_calculation" in r:
            from app.commerce.models import CalculationProfile
            from datetime import date
            dummy_profile = db.scalar(select(CalculationProfile).where(CalculationProfile.name == "LEGACY_ARCHIVE"))
            if not dummy_profile:
                dummy_profile = CalculationProfile(
                    name="LEGACY_ARCHIVE",
                    status="published",
                    effective_from=date(1970, 1, 1),
                    definition={},
                    reason="Auto-generated for legacy migrations",
                    author_id=fallback_user_id
                )
                db.add(dummy_profile)
                db.flush()
                
            lc = r["legacy_calculation"]
            calc = Calculation(
                request_id=req.id,
                profile_id=dummy_profile.id,
                snapshot={"is_legacy_archive": True, "legacy_totals": lc["totals"]},
                digest="legacy-" + r["number"],
                reason="Legacy Import",
                author_id=fallback_user_id
            )
            db.add(calc)
            db.flush()
            stats["calculations_created"] += 1

            if "invoice" in lc:
                inv = lc["invoice"]
                seller_id = seller_map.get(inv["seller_legacy_id"])
                doc = CommercialDocument(
                    request_id=req.id,
                    seller_id=seller_id,
                    client_id=client_id,
                    kind="invoice",
                    number=inv["number"],
                    calculation_id=calc.id,
                    total=Decimal(str(inv["total"])),
                    currency=inv["currency"],
                    status="issued",
                    valid_until=datetime(2099, 12, 31),
                    snapshot={"is_legacy": True},
                    files=[],
                    author_id=fallback_user_id
                )
                db.add(doc)
                db.flush()
                stats["invoices_created"] += 1
                
    for p in data.get("payments", []):
        payment = db.scalar(select(Payment).where(Payment.external_id == p["external_id"]))
        if payment:
            continue
            
        client_id = client_map.get(p["client_legacy_id"])
        seller_id = seller_map.get(p["seller_legacy_id"])
            
        payment = Payment(
            external_id=p["external_id"],
            amount=Decimal(str(p["amount"])),
            currency=p["currency"],
            client_id=client_id,
            seller_id=seller_id,
            status="confirmed",
            payment_date=datetime(2022, 1, 1).date(),
            number=p["external_id"],
            declared_by=fallback_user_id,
            request_id=request_map.get(p.get("request_legacy_id"))
        )
        db.add(payment)
        db.flush()
        stats["payments_created"] += 1

    logger.info("=== Migration Summary ===")
    for k, v in stats.items():
        logger.info(f"  {k}: {v}")
        
    if errors:
        logger.warning("=== Errors ===")
        for e in errors:
            logger.warning(f"  {e}")

    if "payments_created" in stats:
        balances = db.execute(
            select(Payment.currency, func.sum(Payment.amount))
            .where(Payment.external_id.in_([p["external_id"] for p in data.get("payments", [])]))
            .group_by(Payment.currency)
        ).all()
        logger.info("=== Financial Checksums ===")
        for currency, total in balances:
            logger.info(f"  {currency}: {total}")
            
    if dry_run:
        logger.info("DRY-RUN mode. Rolling back changes.")
        db.rollback()
    else:
        logger.info("COMMIT mode. Committing changes.")
        db.commit()

def main():
    parser = argparse.ArgumentParser(description="Утилита миграции исторических данных (S17)")
    parser.add_argument("data_file", help="Путь к JSON файлу с данными")
    parser.add_argument("--commit", action="store_true", help="Фактически сохранить данные в БД")
    args = parser.parse_args()

    with open(args.data_file, "r") as f:
        data = json.load(f)

    with SessionLocal() as db:
        try:
            migrate_data(db, data, dry_run=not args.commit)
        except Exception as e:
            db.rollback()
            logger.exception("Migration failed:")
            sys.exit(1)

if __name__ == "__main__":
    main()
