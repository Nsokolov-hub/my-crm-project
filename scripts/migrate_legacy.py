#!/usr/bin/env python3
"""Legacy data migration utility with strict input contract and discrepancy reporting (R13)."""

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

# Ensure backend modules can be imported
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import func, select

from app.commerce.models import (
    Calculation,
    CalculationProfile,
    CommercialDocument,
    Payment,
    PaymentAllocation,
)
from app.core.db import SessionLocal
from app.core.models import User
from app.core.security import password_hasher
from app.crm.models import Counterparty, Request, Seller

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("migrate_legacy")


def parse_date(date_str: str | None) -> date | None:
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        # Accepts 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS...'
        if "T" in date_str:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
        return date.fromisoformat(date_str)
    except ValueError:
        return None


def parse_datetime(dt_str: str | None) -> datetime | None:
    if not dt_str or not isinstance(dt_str, str):
        return None
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def migrate_data(db, data: dict, dry_run: bool = True) -> dict:
    stats = defaultdict(int)
    for k in [
        "users_created", "users_reused",
        "sellers_created", "sellers_reused",
        "counterparties_created", "counterparties_reused",
        "requests_created", "requests_reused",
        "calculations_created",
        "invoices_created", "invoices_reused",
        "payments_created", "payments_reused",
        "allocations_created", "allocations_reused",
        "payments_as_advances",
    ]:
        stats[k] = 0
    discrepancies = []

    # 1. Historical & Active Users
    user_map = {}
    # Fetch existing users
    existing_users = {u.email.lower(): u.id for u in db.scalars(select(User)).all()}

    for u in data.get("users", []):
        legacy_id = u.get("legacy_id")
        email = (u.get("email") or "").strip().lower()
        name = (u.get("name") or "").strip()

        if not legacy_id or not email or not name:
            discrepancies.append({
                "entity": "user",
                "legacy_id": legacy_id,
                "reason": "Missing required fields (legacy_id, email, or name)",
            })
            continue

        if email in existing_users:
            user_map[legacy_id] = existing_users[email]
            stats["users_reused"] += 1
        else:
            new_user = User(
                email=email,
                name=name,
                password_hash=password_hasher.hash("Legacy-Password-Disabled-1234"),
                active=u.get("active", False),
            )
            db.add(new_user)
            db.flush()
            existing_users[email] = new_user.id
            user_map[legacy_id] = new_user.id
            stats["users_created"] += 1

    # Fallback to existing admin user if available
    admin_user = db.scalar(select(User).where(User.active.is_(True)).limit(1))
    fallback_user_id = admin_user.id if admin_user else next(iter(user_map.values()), None)

    # 2. Sellers (Our legal entities)
    seller_map = {}
    for s in data.get("sellers", []):
        legacy_id = s.get("legacy_id")
        name = (s.get("name") or "").strip()
        currency = (s.get("currency") or "RUB").strip().upper()
        ext_id = s.get("external_id")

        if not legacy_id or not name:
            discrepancies.append({
                "entity": "seller",
                "legacy_id": legacy_id,
                "reason": "Missing required fields (legacy_id or name)",
            })
            continue

        seller = db.scalar(select(Seller).where(Seller.name == name))
        if not seller:
            seller = Seller(name=name, currency=currency, details={"external_id": ext_id} if ext_id else {})
            db.add(seller)
            db.flush()
            stats["sellers_created"] += 1
        else:
            stats["sellers_reused"] += 1
        seller_map[legacy_id] = seller.id

    # 3. Counterparties (Clients/Suppliers)
    client_map = {}
    for c in data.get("counterparties", []):
        legacy_id = c.get("legacy_id")
        name = (c.get("name") or "").strip()
        ext_id = (c.get("external_id") or "").strip() or None

        if not legacy_id or not name:
            discrepancies.append({
                "entity": "counterparty",
                "legacy_id": legacy_id,
                "reason": "Missing required fields (legacy_id or name)",
            })
            continue

        client = None
        if ext_id:
            client = db.scalar(select(Counterparty).where(Counterparty.external_id == ext_id))
        if not client:
            owner_id = user_map.get(c.get("owner_legacy_id")) or fallback_user_id
            client = Counterparty(name=name, external_id=ext_id, owner_id=owner_id)
            db.add(client)
            db.flush()
            stats["counterparties_created"] += 1
        else:
            stats["counterparties_reused"] += 1
        client_map[legacy_id] = client.id

    # 4. Calculation Profile for Legacy Archive
    dummy_profile = db.scalar(select(CalculationProfile).where(CalculationProfile.name == "LEGACY_ARCHIVE"))
    if not dummy_profile:
        dummy_profile = CalculationProfile(
            name="LEGACY_ARCHIVE",
            status="published",
            effective_from=date(1970, 1, 1),
            definition={},
            reason="Auto-generated for legacy migrations",
            author_id=fallback_user_id,
        )
        db.add(dummy_profile)
        db.flush()

    # 5. Requests & Invoices
    request_map = {}
    invoice_map = {}

    for r in data.get("requests", []):
        legacy_id = r.get("legacy_id")
        number = (r.get("number") or "").strip()
        title = (r.get("title") or "").strip()
        client_legacy_id = r.get("client_legacy_id")

        if not legacy_id or not number or not title or not client_legacy_id:
            discrepancies.append({
                "entity": "request",
                "legacy_id": legacy_id,
                "number": number,
                "reason": "Missing required fields (legacy_id, number, title, or client_legacy_id)",
            })
            continue

        client_id = client_map.get(client_legacy_id)
        if not client_id:
            discrepancies.append({
                "entity": "request",
                "legacy_id": legacy_id,
                "number": number,
                "reason": f"Unknown client_legacy_id '{client_legacy_id}'",
            })
            continue

        created_at = parse_datetime(r.get("created_at"))
        if not created_at:
            discrepancies.append({
                "entity": "request",
                "legacy_id": legacy_id,
                "number": number,
                "reason": "Invalid or missing created_at ISO timestamp (never replace with synthetic date)",
            })
            continue

        req = db.scalar(select(Request).where(Request.number == number))
        if req:
            request_map[legacy_id] = req.id
            stats["requests_reused"] += 1
        else:
            owner_id = user_map.get(r.get("owner_legacy_id")) or fallback_user_id
            seller_id = seller_map.get(r.get("seller_legacy_id"))
            req = Request(
                number=number,
                title=title,
                client_id=client_id,
                seller_id=seller_id,
                owner_id=owner_id,
                commercial_stage=r.get("stage", "closed"),
                archived=r.get("archived", False),
                created_at=created_at,
            )
            db.add(req)
            db.flush()
            stats["requests_created"] += 1
            request_map[legacy_id] = req.id

        # Legacy calculation & Invoice
        lc = r.get("legacy_calculation")
        if lc:
            calc = db.scalar(select(Calculation).where(Calculation.digest == f"legacy-{number}"))
            if not calc:
                calc = Calculation(
                    request_id=req.id,
                    profile_id=dummy_profile.id,
                    snapshot={"is_legacy_archive": True, "legacy_totals": lc.get("totals", {})},
                    digest=f"legacy-{number}",
                    reason="Legacy Import",
                    author_id=req.owner_id,
                )
                db.add(calc)
                db.flush()
                stats["calculations_created"] += 1

            inv_data = lc.get("invoice")
            if inv_data:
                inv_num = (inv_data.get("number") or "").strip()
                inv_seller_id = seller_map.get(inv_data.get("seller_legacy_id")) or req.seller_id
                if not inv_seller_id:
                    discrepancies.append({
                        "entity": "invoice",
                        "request_number": number,
                        "invoice_number": inv_num,
                        "reason": f"Unknown seller_legacy_id '{inv_data.get('seller_legacy_id')}' for invoice",
                    })
                    continue

                inv = db.scalar(select(CommercialDocument).where(CommercialDocument.number == inv_num))
                if not inv:
                    try:
                        total_dec = Decimal(str(inv_data.get("total", "0")))
                    except InvalidOperation:
                        discrepancies.append({
                            "entity": "invoice",
                            "number": inv_num,
                            "reason": f"Invalid total amount '{inv_data.get('total')}'",
                        })
                        continue

                    inv_date = parse_date(inv_data.get("date")) or created_at.date()
                    inv_valid = parse_date(inv_data.get("valid_until")) or (inv_date + timedelta(days=30))

                    inv = CommercialDocument(
                        request_id=req.id,
                        seller_id=inv_seller_id,
                        client_id=client_id,
                        kind="invoice",
                        number=inv_num,
                        calculation_id=calc.id,
                        total=total_dec,
                        currency=inv_data.get("currency", "RUB").upper(),
                        status="issued",
                        valid_until=inv_valid,
                        snapshot={"is_legacy": True, "date": str(inv_date)},
                        files={},
                        author_id=req.owner_id,
                    )
                    db.add(inv)
                    db.flush()
                    stats["invoices_created"] += 1
                else:
                    stats["invoices_reused"] += 1

                invoice_map[inv_num] = inv.id
                if inv_data.get("legacy_id"):
                    invoice_map[inv_data["legacy_id"]] = inv.id

    # 6. Payments and Allocations
    payment_map = {}
    for p in data.get("payments", []):
        ext_id = (p.get("external_id") or "").strip()
        client_legacy_id = p.get("client_legacy_id")
        seller_legacy_id = p.get("seller_legacy_id")

        if not ext_id or not client_legacy_id or not seller_legacy_id:
            discrepancies.append({
                "entity": "payment",
                "external_id": ext_id,
                "reason": "Missing required fields (external_id, client_legacy_id, or seller_legacy_id)",
            })
            continue

        client_id = client_map.get(client_legacy_id)
        seller_id = seller_map.get(seller_legacy_id)
        if not client_id:
            discrepancies.append({
                "entity": "payment",
                "external_id": ext_id,
                "reason": f"Unknown client_legacy_id '{client_legacy_id}'",
            })
            continue
        if not seller_id:
            discrepancies.append({
                "entity": "payment",
                "external_id": ext_id,
                "reason": f"Unknown seller_legacy_id '{seller_legacy_id}'",
            })
            continue

        # Strict rule: DO NOT invent fixed dates if missing
        p_date = parse_date(p.get("payment_date"))
        if not p_date:
            discrepancies.append({
                "entity": "payment",
                "external_id": ext_id,
                "reason": "Missing or invalid payment_date (cannot replace unknown date with fixed constant)",
            })
            continue

        try:
            amount = Decimal(str(p.get("amount", "0")))
            if amount <= Decimal("0"):
                raise InvalidOperation()
        except InvalidOperation:
            discrepancies.append({
                "entity": "payment",
                "external_id": ext_id,
                "reason": f"Invalid payment amount '{p.get('amount')}' (must be > 0)",
            })
            continue

        currency = (p.get("currency") or "RUB").strip().upper()
        req_legacy_id = p.get("request_legacy_id")
        req_id = request_map.get(req_legacy_id) if req_legacy_id else None

        # Check existing payment within seller scope (seller_id, external_id)
        existing_pmt = db.scalar(
            select(Payment).where(Payment.seller_id == seller_id, Payment.external_id == ext_id)
        )
        if existing_pmt:
            stats["payments_reused"] += 1
            pmt_id = existing_pmt.id
        else:
            new_pmt = Payment(
                external_id=ext_id,
                amount=amount,
                currency=currency,
                client_id=client_id,
                seller_id=seller_id,
                status="confirmed",
                payment_date=p_date,
                number=p.get("number") or ext_id,
                comment=p.get("comment", "Migrated legacy payment"),
                declared_by=fallback_user_id,
                confirmed_by=fallback_user_id,
                confirmed_at=datetime.combine(p_date, datetime.min.time(), tzinfo=timezone.utc),
                request_id=req_id,
            )
            db.add(new_pmt)
            db.flush()
            stats["payments_created"] += 1
            pmt_id = new_pmt.id

        payment_map[ext_id] = pmt_id

        # Allocations: Only create allocation if explicit invoice link is present
        # Do NOT declare confirmed payment an invoice payment without explicit source relation!
        inv_ref = p.get("invoice_legacy_id") or p.get("invoice_number")
        if inv_ref:
            target_inv_id = invoice_map.get(inv_ref)
            if not target_inv_id:
                # Try finding invoice by number
                target_inv_id = db.scalar(
                    select(CommercialDocument.id).where(
                        CommercialDocument.number == inv_ref,
                        CommercialDocument.kind == "invoice",
                    )
                )

            if target_inv_id:
                existing_alloc = db.scalar(
                    select(PaymentAllocation).where(
                        PaymentAllocation.payment_id == pmt_id,
                        PaymentAllocation.invoice_id == target_inv_id,
                    )
                )
                if not existing_alloc:
                    alloc_amount = Decimal(str(p.get("allocated_amount", amount)))
                    alloc = PaymentAllocation(
                        payment_id=pmt_id,
                        invoice_id=target_inv_id,
                        amount=alloc_amount,
                        author_id=fallback_user_id,
                    )
                    db.add(alloc)
                    db.flush()
                    stats["allocations_created"] += 1
                else:
                    stats["allocations_reused"] += 1
            else:
                discrepancies.append({
                    "entity": "payment_allocation",
                    "payment_external_id": ext_id,
                    "invoice_ref": inv_ref,
                    "reason": f"Referenced invoice '{inv_ref}' not found; payment left as unallocated advance",
                })
        else:
            stats["payments_as_advances"] += 1

    # 7. Financial Control Totals (Reconciliation by currency on migrated records)
    financial_summary = {}
    migrated_inv_ids = list(set(invoice_map.values()))
    migrated_pay_ids = list(set(payment_map.values()))

    currencies = set()
    if migrated_inv_ids:
        currencies.update(
            db.scalars(
                select(CommercialDocument.currency)
                .where(CommercialDocument.id.in_(migrated_inv_ids))
                .distinct()
            ).all()
        )
    if migrated_pay_ids:
        currencies.update(
            db.scalars(
                select(Payment.currency)
                .where(Payment.id.in_(migrated_pay_ids))
                .distinct()
            ).all()
        )

    for cur in sorted(currencies):
        inv_sum = Decimal("0")
        if migrated_inv_ids:
            inv_sum = (
                db.scalar(
                    select(func.sum(CommercialDocument.total)).where(
                        CommercialDocument.id.in_(migrated_inv_ids),
                        CommercialDocument.kind == "invoice",
                        CommercialDocument.currency == cur,
                    )
                )
                or Decimal("0")
            )
        pay_sum = Decimal("0")
        if migrated_pay_ids:
            pay_sum = (
                db.scalar(
                    select(func.sum(Payment.amount)).where(
                        Payment.id.in_(migrated_pay_ids),
                        Payment.currency == cur,
                        Payment.status == "confirmed",
                    )
                )
                or Decimal("0")
            )
        alloc_sum = Decimal("0")
        if migrated_pay_ids:
            alloc_sum = (
                db.scalar(
                    select(func.sum(PaymentAllocation.amount))
                    .join(Payment, PaymentAllocation.payment_id == Payment.id)
                    .where(Payment.id.in_(migrated_pay_ids), Payment.currency == cur)
                )
                or Decimal("0")
            )

        receivables = inv_sum - alloc_sum
        advances = pay_sum - alloc_sum

        financial_summary[cur] = {
            "invoiced": str(inv_sum),
            "paid": str(pay_sum),
            "allocated": str(alloc_sum),
            "outstanding_receivables": str(receivables),
            "unallocated_advances": str(advances),
        }

    # Logging summary
    logger.info("================ MIGRATION REPORT ================")
    logger.info(f"Mode: {'DRY-RUN (Changes will be rolled back)' if dry_run else 'COMMIT (Changes saved to database)'}")
    logger.info("--- Statistics ---")
    for k, v in sorted(stats.items()):
        logger.info(f"  {k}: {v}")

    if discrepancies:
        logger.warning(f"--- Discrepancies & Errors ({len(discrepancies)}) ---")
        for d in discrepancies:
            logger.warning(f"  [{d.get('entity')}] ID={d.get('legacy_id') or d.get('external_id')}: {d.get('reason')}")
    else:
        logger.info("--- Discrepancies: 0 (All records cleanly matched) ---")

    logger.info("--- Financial Totals by Currency ---")
    for cur, data_c in financial_summary.items():
        logger.info(f"  {cur}: Invoiced={data_c['invoiced']}, Paid={data_c['paid']}, Allocated={data_c['allocated']}, Receivables={data_c['outstanding_receivables']}, Advances={data_c['unallocated_advances']}")
    logger.info("==================================================")

    if dry_run:
        db.rollback()
        logger.info("DRY-RUN: Database rollback complete. 0 rows changed.")
    else:
        db.commit()
        logger.info("COMMIT: Changes successfully persisted to database.")

    return {
        "dry_run": dry_run,
        "stats": dict(stats),
        "discrepancies": discrepancies,
        "financial_summary": financial_summary,
    }


def main():
    parser = argparse.ArgumentParser(description="Утилита миграции исторических данных (R13)")
    parser.add_argument("data_file", help="Путь к JSON-файлу с историческими данными")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True, help="Пробный прогон без сохранения (по умолчанию)")
    group.add_argument("--commit", action="store_true", help="Фактически применить изменения к базе данных")
    parser.add_argument("--report", help="Путь для выгрузки отчёта в формате JSON")

    args = parser.parse_args()
    is_commit = bool(args.commit)

    data_path = Path(args.data_file)
    if not data_path.exists():
        sys.exit(f"ERROR: Data file '{data_path}' not found.")

    with open(data_path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            sys.exit(f"ERROR: Invalid JSON in {data_path}: {e}")

    with SessionLocal() as db:
        try:
            result = migrate_data(db, data, dry_run=not is_commit)
            if args.report:
                report_path = Path(args.report)
                report_path.parent.mkdir(parents=True, exist_ok=True)
                with open(report_path, "w", encoding="utf-8") as rf:
                    json.dump(result, rf, indent=2, ensure_ascii=False)
                print(f"Migration report written to {report_path}")
        except Exception:
            db.rollback()
            logger.exception("Migration aborted due to fatal error:")
            sys.exit(1)


if __name__ == "__main__":
    main()
