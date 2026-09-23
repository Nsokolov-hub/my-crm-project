from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import error
from app.core.models import User
from app.core.security import check_request, current_user, has_request_permission, require_permission
from app.core.service import advisory, audit, check_version, idem, lock, serialize
from app.crm.models import Counterparty, RequestItem

from .calculator import convert, dec, digest, validate_cas
from .files import put_file, read_file, workbook
from .models import Manufacturer, Product, Quote, Substance, SupplierRequest
from .schemas import ProductIn, QuoteIn, RfqIn, SentIn, VerifyIn

router = APIRouter(tags=["Закупки и каталог"])
DB = Annotated[Session, Depends(get_db)]
Actor = Annotated[User, Depends(current_user)]


def normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def product_view(db: Session, product: Product) -> dict:
    data = serialize(product)
    substance = db.get(Substance, product.substance_id)
    manufacturer = db.get(Manufacturer, product.manufacturer_id)
    data.update(cas=substance.cas, no_cas_reason=substance.no_cas_reason, manufacturer=manufacturer.name)
    return data


def ensure_product(db: Session, data: ProductIn) -> Product:
    cas = validate_cas(data.cas, data.no_cas_reason)
    substance_key = cas or normalized(data.name)
    advisory(db, f"substance:{substance_key}")
    substance = (
        db.scalar(select(Substance).where(Substance.cas == cas))
        if cas
        else db.scalar(select(Substance).where(Substance.cas.is_(None), Substance.name == data.name.strip()))
    )
    if substance is None:
        substance = Substance(name=data.name.strip(), cas=cas, no_cas_reason=data.no_cas_reason)
        db.add(substance)
        db.flush()
    maker_name = normalized(data.manufacturer)
    advisory(db, f"manufacturer:{maker_name}")
    maker = db.scalar(select(Manufacturer).where(Manufacturer.normalized_name == maker_name))
    if maker is None:
        maker = Manufacturer(name=data.manufacturer.strip(), normalized_name=maker_name)
        db.add(maker)
        db.flush()
    fingerprint = digest(
        {
            "substance_id": substance.id,
            "manufacturer_id": maker.id,
            "article": normalized(data.article),
            "purity": normalized(data.purity),
            "packaging": normalized(data.packaging),
            "unit": data.unit,
            "package_quantity": str(data.package_quantity.normalize()) if data.package_quantity else None,
        }
    )
    advisory(db, f"product:{fingerprint}")
    product = db.scalar(select(Product).where(Product.fingerprint == fingerprint))
    if product is None:
        product = Product(
            substance_id=substance.id,
            manufacturer_id=maker.id,
            fingerprint=fingerprint,
            name=data.name.strip(),
            article=data.article,
            purity=data.purity,
            packaging=data.packaging,
            unit=data.unit,
            package_quantity=data.package_quantity,
            specification=data.specification,
            verified=False,
        )
        db.add(product)
        db.flush()
    return product


def supplier(db: Session, supplier_id: str) -> Counterparty:
    obj = db.get(Counterparty, supplier_id)
    if not obj or obj.kind not in ("supplier", "both") or obj.archived:
        error("INVALID_SUPPLIER", "Выберите действующего поставщика", field="supplier_id")
    return obj


def quote_view(db: Session, user: User, quote: Quote) -> dict:
    value = serialize(quote)
    value["product"] = product_view(db, db.get(Product, quote.product_id))
    value["expired"] = quote.valid_until is None or quote.valid_until < date.today()
    item = db.get(RequestItem, quote.item_id)
    value["requires_review"] = item.revision != quote.item_revision
    if not has_request_permission(db, user, quote.request_id, "finance.purchase.read"):
        for name in ("price", "sample", "revision_reason"):
            value.pop(name, None)
        # Supplier terms and source files may contain purchase prices.
        value["terms"] = {
            key: val
            for key, val in quote.terms.items()
            if key in ("delivery_days", "delivery_place", "delivery_basis", "ready_date")
        }
    return value


def check_quote(
    db: Session, quote: Quote, *, require_current: bool = True, require_verified: bool = True
) -> RequestItem:
    item = db.get(RequestItem, quote.item_id)
    if item.archived or item.revision != quote.item_revision:
        error("ITEM_REVISION_CONFLICT", "Потребность изменена; требуется новая квота и проверка состава", 409)
    if quote.requires_confirmation or not quote.valid_until or quote.valid_until < date.today():
        error("QUOTE_EXPIRED", "Условия квоты истекли или требуют подтверждения поставщика")
    if require_current and db.scalar(select(Quote.id).where(Quote.previous_id == quote.id)):
        error("QUOTE_REVISION_CONFLICT", "Создана новая версия квоты. Обновите выбор", 409)
    product = db.get(Product, quote.product_id)
    if require_verified and not product.verified:
        error("PRODUCT_REVIEW", "Товарный вариант требует проверки ответственным")
    return item


def validate_quantity(quote: Quote, product: Product, quantity, unit: str) -> None:
    amount = convert(dec(quantity), unit, quote.price_unit)
    if amount <= 0 or amount > quote.available_quantity:
        error("QUOTE_AVAILABILITY", "Количество превышает доступное количество квоты")
    if amount < quote.minimum_quantity or amount % quote.multiple != 0:
        error("QUOTE_MULTIPLE", "Количество не соответствует минимальному заказу или кратности")
    if product.package_quantity:
        packages = convert(dec(quantity), unit, product.unit) / product.package_quantity
        if packages != packages.to_integral_value():
            error("PACKAGE_MULTIPLE", "Количество должно соответствовать целому числу упаковок")


@router.get("/catalog/products")
def list_products(
    db: DB,
    user: Actor,
    search: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    require_permission(db, user, "catalog.read")
    query = select(Product).order_by(Product.created_at.desc())
    if search:
        query = query.where(Product.name.ilike(f"%{search}%"))
    items = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [product_view(db, item) for item in items], "page": page, "page_size": page_size}


@router.post("/catalog/products")
def create_product(data: ProductIn, db: DB, user: Actor):
    require_permission(db, user, "catalog.write")
    product = ensure_product(db, data)
    audit(db, user, "product", product.id, "upsert", after=product_view(db, product))
    return product_view(db, product)


@router.post("/catalog/products/{product_id}/verify")
def verify_product(product_id: str, data: VerifyIn, db: DB, user: Actor):
    require_permission(db, user, "catalog.write")

    def operation():
        product = lock(db, Product, product_id)
        check_version(product, data.version)
        product.verified = True
        product.verification_reason = data.reason
        product.version += 1
        audit(db, user, "product", product.id, "verify", reason=data.reason)
        return product_view(db, product)

    return idem(
        db,
        user,
        data.idempotency_key,
        f"verify-product:{product_id}",
        data.model_dump(mode="json"),
        operation,
    )


def create_quote(
    db: Session, user: User, request_id: str, data: QuoteIn, previous: Quote | None = None
) -> dict:
    advisory(db, f"request-commerce:{request_id}")
    item = lock(db, RequestItem, data.item_id)
    if item.request_id != request_id or item.archived:
        error("ITEM_NOT_FOUND", "Позиция заявки не найдена", 404)
    if item.revision != data.item_revision:
        error("ITEM_REVISION_CONFLICT", "Позиция изменена. Обновите данные", 409)
    supplier(db, data.supplier_id)
    if (data.product is None) == (data.product_id is None):
        error("PRODUCT_REQUIRED", "Выберите товар или заполните новый товар")
    if data.product:
        require_permission(db, user, "catalog.write")
        product = ensure_product(db, data.product)
    else:
        product = db.get(Product, data.product_id)
    if not product:
        error("PRODUCT_NOT_FOUND", "Товар не найден", 404)
    convert(dec("1"), product.unit, data.price_unit)
    if data.price == 0 and not data.sample:
        error("ZERO_PRICE", "Нулевая цена разрешена только для явно обозначенного образца")
    if not data.valid_until and not data.requires_confirmation:
        error("VALIDITY_REQUIRED", "Укажите срок действия или необходимость подтверждения")
    if data.supplier_request_id:
        rfq = db.get(SupplierRequest, data.supplier_request_id)
        if (
            not rfq
            or rfq.request_id != request_id
            or rfq.supplier_id != data.supplier_id
            or not any(
                row["id"] == item.id and row["revision"] == item.revision for row in rfq.snapshot["items"]
            )
        ):
            error("RFQ_REVISION", "Запрос поставщику не соответствует позиции, редакции или поставщику")
    if previous:
        if data.item_id != previous.item_id or not data.revision_reason.strip():
            error("REVISION_REASON", "Новая версия должна относиться к той же позиции и содержать основание")
        if db.scalar(select(Quote.id).where(Quote.previous_id == previous.id)):
            error("QUOTE_REVISION_CONFLICT", "Для этой квоты уже создана следующая версия", 409)
    values = data.model_dump(exclude={"idempotency_key", "product", "product_id"})
    values["terms"] = data.terms
    quote = Quote(
        **values,
        request_id=request_id,
        product_id=product.id,
        author_id=user.id,
        previous_id=previous.id if previous else None,
        revision=previous.revision + 1 if previous else 1,
    )
    db.add(quote)
    db.flush()
    audit(
        db,
        user,
        "quote",
        quote.id,
        "revise" if previous else "create",
        after=serialize(quote),
        reason=data.revision_reason,
    )
    return {"id": quote.id}


@router.get("/requests/{request_id}/quotes")
def list_quotes(
    request_id: str,
    db: DB,
    user: Actor,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
):
    check_request(db, user, request_id)
    items = db.scalars(
        select(Quote)
        .where(Quote.request_id == request_id)
        .order_by(Quote.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"items": [quote_view(db, user, quote) for quote in items], "page": page, "page_size": page_size}


@router.post("/requests/{request_id}/quotes")
def add_quote(request_id: str, data: QuoteIn, db: DB, user: Actor):
    check_request(db, user, request_id, "quotes.write")
    
    def reconstruct(result: dict) -> dict:
        return quote_view(db, user, db.get(Quote, result["id"]))
        
    return idem(
        db,
        user,
        data.idempotency_key,
        f"quote:{request_id}",
        data.model_dump(mode="json"),
        lambda: create_quote(db, user, request_id, data),
        reconstruct,
    )


@router.post("/quotes/{quote_id}/revise")
def revise_quote(quote_id: str, data: QuoteIn, db: DB, user: Actor):
    previous = db.get(Quote, quote_id)
    if not previous:
        error("NOT_FOUND", "Квота не найдена", 404)
    check_request(db, user, previous.request_id, "quotes.write")
    
    def reconstruct(result: dict) -> dict:
        return quote_view(db, user, db.get(Quote, result["id"]))
        
    return idem(
        db,
        user,
        data.idempotency_key,
        f"quote-revise:{quote_id}",
        data.model_dump(mode="json"),
        lambda: create_quote(db, user, previous.request_id, data, previous),
        reconstruct,
    )


def rfq_view(db: Session, rfq: SupplierRequest) -> dict:
    value = serialize(rfq)
    requires_review = False
    for snapshot_item in rfq.snapshot.get("items", []):
        current = db.get(RequestItem, snapshot_item["id"])
        if current and current.revision != snapshot_item.get("revision", 0):
            requires_review = True
            break
    value["requires_review"] = requires_review
    return value


@router.get("/requests/{request_id}/rfqs")
def list_rfqs(request_id: str, db: DB, user: Actor):
    check_request(db, user, request_id)
    return {
        "items": [
            rfq_view(db, row)
            for row in db.scalars(
                select(SupplierRequest)
                .where(SupplierRequest.request_id == request_id)
                .order_by(SupplierRequest.created_at.desc())
            ).all()
        ]
    }


@router.post("/requests/{request_id}/rfqs")
def create_rfq(request_id: str, data: RfqIn, db: DB, user: Actor):
    check_request(db, user, request_id, "quotes.write")

    def operation():
        advisory(db, f"request-commerce:{request_id}")
        recipient = supplier(db, data.supplier_id)
        if len(set(data.item_ids)) != len(data.item_ids):
            error("DUPLICATE_ITEM", "Позиция указана повторно")
        items = db.scalars(
            select(RequestItem)
            .where(
                RequestItem.id.in_(data.item_ids),
                RequestItem.request_id == request_id,
                RequestItem.archived.is_(False),
            )
            .order_by(RequestItem.id)
        ).all()
        if len(items) != len(data.item_ids) or any(not row.quantity or not row.unit for row in items):
            error("RFQ_ITEMS", "Укажите количество и единицу всех выбранных позиций")
        previous = db.get(SupplierRequest, data.parent_id) if data.parent_id else None
        if data.parent_id and (
            not previous or previous.request_id != request_id or previous.supplier_id != data.supplier_id
        ):
            error("RFQ_PARENT", "Исходный запрос поставщику не найден", 404)
        snapshot = {
            "supplier": {"id": recipient.id, "name": recipient.name, "email": recipient.email},
            "items": [serialize(item) for item in items],
            "response_due": data.response_due.isoformat(),
            "comment": data.comment,
        }
        content = workbook(
            ["№", "Наименование", "CAS", "Качество", "Фасовка", "Количество", "Единица", "Комментарий"],
            [
                [
                    str(i),
                    item.description,
                    item.cas or "",
                    item.purity or "",
                    item.packaging or "",
                    str(item.quantity),
                    item.unit,
                    item.comment or "",
                ]
                for i, item in enumerate(items, 1)
            ],
            "Запрос поставщику",
        )
        metadata = put_file(content, "xlsx")
        obj = SupplierRequest(
            request_id=request_id,
            supplier_id=data.supplier_id,
            parent_id=data.parent_id,
            revision=previous.revision + 1 if previous else 1,
            snapshot=snapshot,
            file_key=metadata["key"],
            sha256=metadata["sha256"],
            author_id=user.id,
        )
        db.add(obj)
        db.flush()
        audit(db, user, "supplier_request", obj.id, "create", after=snapshot)
        return serialize(obj)

    return idem(db, user, data.idempotency_key, f"rfq:{request_id}", data.model_dump(mode="json"), operation)


@router.get("/rfqs/{rfq_id}/file")
def download_rfq(rfq_id: str, db: DB, user: Actor):
    obj = db.get(SupplierRequest, rfq_id)
    if not obj:
        error("NOT_FOUND", "Запрос не найден", 404)
    check_request(db, user, obj.request_id)
    require_permission(db, user, "exports.download", obj.request_id)
    audit(db, user, "supplier_request", obj.id, "download")
    return Response(
        read_file({"key": obj.file_key, "sha256": obj.sha256}),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="rfq-{obj.id}.xlsx"'},
    )


@router.post("/rfqs/{rfq_id}/sent")
def sent_rfq(rfq_id: str, data: SentIn, db: DB, user: Actor):
    obj = db.get(SupplierRequest, rfq_id)
    if not obj:
        error("NOT_FOUND", "Запрос не найден", 404)
    check_request(db, user, obj.request_id, "quotes.write")

    def operation():
        current = lock(db, SupplierRequest, rfq_id)
        check_version(current, data.version)
        if current.sent_at:
            error("ALREADY_SENT", "Отправка уже отмечена; уточнение требует нового запроса", 409)
        current.sent_at, current.sent_channel = data.sent_at, data.channel
        current.version += 1
        audit(
            db,
            user,
            "supplier_request",
            current.id,
            "sent",
            after={"channel": data.channel, "sent_at": data.sent_at},
        )
        return serialize(current)

    return idem(db, user, data.idempotency_key, f"rfq-sent:{rfq_id}", data.model_dump(mode="json"), operation)
