"""Customer sale prices from the immutable lines actually approved for procurement."""

from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from app.core.db import DB, as_utc
from app.core.errors import error
from app.core.security import Actor, check_request, request_predicate
from app.crm.models import Nomenclature, QuoteItem, QuoteSheet, RequestItem
from app.crm.models import Request as CRMRequest

from .models import CommercialDocument, Execution

router = APIRouter(tags=["Расчёты и настраиваемые профили"])


class SalePriceHistoryItem(BaseModel):
    execution_id: str
    request_id: str
    request_number: str
    sold_at: datetime
    article: str | None
    nomenclature_id: str | None
    description: str
    packing: str | None
    manufacturer: str | None
    unit: str
    quantity: str
    unit_price: str
    currency: str
    price_includes_vat: bool


class SalePriceHistory(BaseModel):
    items: list[SalePriceHistoryItem]
    limit: int


@router.get("/requests/{request_id}/sale-price-history", response_model=SalePriceHistory)
def sale_price_history(
    request_id: str,
    db: DB,
    user: Actor,
    quote_item_ids: str | None = Query(None, max_length=4000),
    limit: int = Query(50, ge=1, le=100),
):
    """Earlier confirmed sales to this customer, for the articles selected in the calculation.

    The optional comma-separated quote IDs must all belong to this request. Without them the
    lookup uses every active quoted item and demand of the request. Prices, product names and
    VAT treatment come from the released snapshots, never today's catalogue or a draft price.
    """
    current = check_request(db, user, request_id)
    requested_ids = None
    if quote_item_ids is not None:
        requested_ids = list(dict.fromkeys(value.strip() for value in quote_item_ids.split(",") if value.strip()))
        if len(requested_ids) > 100:
            error("HISTORY_SELECTION_LIMIT", "Выберите не более 100 позиций для поиска истории",
                  field="quote_item_ids")
        if not requested_ids:
            return {"items": [], "limit": limit}

    quoted = select(QuoteItem.id, Nomenclature.id, Nomenclature.article).join(
        QuoteSheet, QuoteSheet.id == QuoteItem.quote_id
    ).join(Nomenclature, Nomenclature.id == QuoteItem.nomenclature_id).where(QuoteSheet.request_id == request_id)
    if requested_ids is not None:
        quoted = quoted.where(QuoteItem.id.in_(requested_ids))
    else:
        quoted = quoted.where(QuoteItem.archived.is_(False), QuoteSheet.archived.is_(False))
    products = db.execute(quoted).all()
    if requested_ids is not None and {row[0] for row in products} != set(requested_ids):
        error("QUOTE_ITEM_NOT_FOUND", "Позиция квоты не найдена в этой заявке", 404, "quote_item_ids")

    # The article is the business key. Use catalogue identity only where no article is known;
    # a later edit of a catalogue card must not turn a different old article into a match.
    nomenclatures = {row[1] for row in products if not row[2] or not row[2].strip()}
    articles = {row[2].strip().lower() for row in products if row[2] and row[2].strip()}
    if requested_ids is None:
        for nomenclature_id, demand_article, catalogue_article in db.execute(select(
            RequestItem.nomenclature_id, RequestItem.article, Nomenclature.article
        ).outerjoin(Nomenclature, Nomenclature.id == RequestItem.nomenclature_id).where(
            RequestItem.request_id == request_id, RequestItem.archived.is_(False)
        )):
            known_article = (demand_article or "").strip() or (catalogue_article or "").strip()
            if known_article:
                articles.add(known_article.lower())
            elif nomenclature_id:
                nomenclatures.add(nomenclature_id)
    if not articles and not nomenclatures:
        return {"items": [], "limit": limit}

    # Filter in SQL before LIMIT so unrelated articles cannot hide an older matching sale.
    article = func.lower(func.trim(Execution.snapshot["product"]["article"].as_string()))
    nomenclature_id = Execution.snapshot["quote_item"]["nomenclature_id"].as_string()
    matches = or_(article.in_(articles), nomenclature_id.in_(nomenclatures))
    rows = db.execute(select(Execution, CRMRequest, CommercialDocument).join(
        CRMRequest, CRMRequest.id == Execution.request_id
    ).join(CommercialDocument, CommercialDocument.id == Execution.proposal_id).where(
        CommercialDocument.client_id == current.client_id,
        CRMRequest.id != request_id,
        CRMRequest.is_test == current.is_test,
        CRMRequest.sale_confirmed_at.is_not(None),
        CRMRequest.commercial_stage != "closed_lost",
        request_predicate(db, user),
        Execution.procurement_at.is_not(None),
        Execution.cancelled_quantity < Execution.quantity,
        matches,
    ).order_by(Execution.procurement_at.desc(), Execution.created_at.desc(), Execution.id.desc()).limit(limit)).all()
    items = []
    for execution, request, proposal in rows:
        line = execution.snapshot
        product = line.get("product") or {}
        quote = line.get("quote_item") or {}
        items.append({
            "execution_id": execution.id,
            "request_id": request.id,
            "request_number": request.number,
            "sold_at": as_utc(execution.procurement_at),
            "article": product.get("article"),
            "nomenclature_id": quote.get("nomenclature_id"),
            "description": line.get("description") or product.get("name") or "",
            "packing": line.get("packing") or product.get("packaging"),
            "manufacturer": product.get("manufacturer"),
            "unit": execution.unit,
            "quantity": str(execution.quantity - execution.cancelled_quantity),
            "unit_price": str(line["unit_price"]),
            "currency": proposal.currency,
            "price_includes_vat": bool(proposal.snapshot.get("price_includes_vat", bool(execution.quote_item_id))),
        })
    return {"items": items, "limit": limit}
