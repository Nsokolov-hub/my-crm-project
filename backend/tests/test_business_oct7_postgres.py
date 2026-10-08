"""Archiving is allowed by PostgreSQL's recorded-fact protection, price edits are not."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from test_postgres_commerce import postgres_commerce as postgres_commerce

from app.commerce.schemas import VersionCommand
from app.core.db import utcnow
from app.core.models import User
from app.crm.models import Currency, Nomenclature, Packing, QuoteItem, QuoteSheet
from app.crm.routes import delete_quote_sheet

pytestmark = pytest.mark.postgres


def test_quote_archive_preserves_immutable_prices_on_migrated_postgres(postgres_commerce):
    env = postgres_commerce
    with env["sessions"].begin() as db:
        nomenclature = Nomenclature(name="Sample", article="A1")
        currency = db.scalar(select(Currency).where(Currency.code == "RUB"))
        db.add(nomenclature)
        db.flush()
        packing = Packing(nomenclature_id=nomenclature.id, value=1, unit="g", display_name="1 g")
        sheet = QuoteSheet(number="Q-000001", request_id=env["request_id"],
                           supplier_id=env["supplier_id"], author_id=env["owner_id"])
        db.add_all([packing, sheet])
        db.flush()
        quote = QuoteItem(quote_id=sheet.id, supplier_id=env["supplier_id"],
                          nomenclature_id=nomenclature.id, packing_id=packing.id,
                          quantity=1, unit_price=Decimal("10"), currency_id=currency.id,
                          delivery_days=7, quoted_at=utcnow(), valid_until=utcnow() + timedelta(days=21),
                          source_request_item_id=env["item_id"], author_id=env["owner_id"])
        db.add(quote)
        db.flush()
        sheet_id, quote_id = sheet.id, quote.id
    with env["sessions"].begin() as db:
        result = delete_quote_sheet(sheet_id, VersionCommand(version=1, idempotency_key=str(uuid4())),
                                    db.get(User, env["owner_id"]), db)
        assert result["deleted_ids"] == [quote_id]
    with env["sessions"]() as db:
        assert db.get(QuoteSheet, sheet_id).archived
        assert db.get(QuoteItem, quote_id).archived
        assert db.get(QuoteItem, quote_id).unit_price == 10
    with env["sessions"].begin() as db:
        with pytest.raises((IntegrityError, OperationalError), match="CRM_RECORDED_FACT_IMMUTABLE"):
            with db.begin_nested():
                db.execute(text("UPDATE quote_items SET unit_price=999 WHERE id=:id"), {"id": quote_id})
        with pytest.raises((IntegrityError, OperationalError), match="CRM_RECORDED_FACT_IMMUTABLE"):
            with db.begin_nested():
                db.execute(text("DELETE FROM quote_items WHERE id=:id"), {"id": quote_id})
