import copy
from datetime import date

from fastapi import APIRouter
from sqlalchemy import select

from app.core.errors import DomainError, error
from app.core.security import check_request, require_permission
from app.core.service import advisory, audit, check_version, idem, lock, serialize
from app.crm.models import Request as CRMRequest

from .calculator import calculate, digest, example_profile, validate_profile
from .models import Calculation, CalculationProfile, Product, Quote
from .procurement import DB, Actor, check_quote, product_view, validate_quantity
from .schemas import CalculationIn, ProfileIn

router = APIRouter(tags=["Расчёты и настраиваемые профили"])


def financial_access(db, user, request_id: str, permission: str) -> bool:
    try:
        check_request(db, user, request_id, permission)
        return True
    except DomainError:
        return False


def public_calculation(snapshot: dict) -> dict:
    rows = [
        {key: copy.deepcopy(value) for key, value in row.items() if key not in ("detail", "quote")}
        for row in snapshot["lines"]
    ]
    return {
        "lines": rows,
        "totals": snapshot["totals"],
        "currency": snapshot["currency"],
        "algorithm_version": snapshot["algorithm_version"],
    }


def calculation_view(db, user, calculation: Calculation) -> dict:
    result = serialize(calculation)
    if not all(
        financial_access(db, user, calculation.request_id, code)
        for code in ("finance.purchase.read", "finance.calculations.read", "finance.reward.read")
    ):
        result["snapshot"] = public_calculation(calculation.snapshot)
        result.pop("reason", None)
    return result


def build_calculation(db, user, request_id: str, data: CalculationIn) -> dict:
    advisory(db, f"request-commerce:{request_id}")
    req = lock(db, CRMRequest, request_id)
    check_version(req, data.request_version)
    profile = db.get(CalculationProfile, data.profile_id)
    if (
        not profile
        or profile.effective_from > date.today()
        or (profile.effective_until and profile.effective_until < date.today())
    ):
        error("PROFILE_NOT_EFFECTIVE", "Выберите профиль, действующий на дату расчёта")
    selections = []
    for selection in data.selections:
        quote = lock(db, Quote, selection.quote_id)
        if quote.request_id != request_id:
            error("QUOTE_NOT_FOUND", "Квота не найдена в заявке", 404)
        if quote.revision != selection.quote_revision:
            error("QUOTE_REVISION_CONFLICT", "Редакция квоты изменилась", 409)
        check_quote(db, quote, require_verified=False)
        product = db.get(Product, quote.product_id)
        validate_quantity(quote, product, selection.quantity, selection.unit)
        if quote.sample and not profile.definition["allow_samples"]:
            error("SAMPLES_DISABLED", "Профиль не разрешает нулевую стоимость образцов")
        selections.append(
            {
                **selection.model_dump(mode="json"),
                "quote": serialize(quote),
                "product": product_view(db, product),
            }
        )
    snapshot = calculate(
        profile.definition,
        selections,
        [expense.model_dump(mode="json") for expense in data.expenses],
        [{**rate.model_dump(mode="json"), "author_id": user.id} for rate in data.rates],
    )
    snapshot["input"] = data.model_dump(mode="json")
    snapshot["request_version"] = req.version
    snapshot["profile_id"] = profile.id
    snapshot["profile_created_at"] = profile.created_at.isoformat()
    return snapshot


@router.get("/profiles/example")
def sample_profile(db: DB, user: Actor):
    require_permission(db, user, "profiles.write")
    return {
        "is_test": True,
        "notice": "Условный арифметический пример A08. Пользователь задаёт собственные действующие формулы и ставки.",
        "definition": example_profile(),
    }


@router.get("/profiles")
def list_profiles(db: DB, user: Actor):
    require_permission(db, user, "finance.calculations.read")
    return {
        "items": [
            serialize(profile)
            for profile in db.scalars(
                select(CalculationProfile).order_by(CalculationProfile.created_at.desc())
            ).all()
        ]
    }


@router.post("/profiles")
def create_profile(data: ProfileIn, db: DB, user: Actor):
    require_permission(db, user, "profiles.write")
    require_permission(db, user, "templates.write")

    def operation():
        definition = data.definition.model_dump(mode="json")
        validate_profile(definition)
        if data.effective_until and data.effective_until < data.effective_from:
            error("PROFILE_DATES", "Дата окончания должна быть не раньше даты начала")
        if data.previous_id and not db.get(CalculationProfile, data.previous_id):
            error("PROFILE_NOT_FOUND", "Исходный профиль не найден", 404)
        profile = CalculationProfile(
            name=data.name,
            previous_id=data.previous_id,
            effective_from=data.effective_from,
            effective_until=data.effective_until,
            definition=definition,
            reason=data.reason,
            author_id=user.id,
        )
        db.add(profile)
        db.flush()
        audit(
            db,
            user,
            "calculation_profile",
            profile.id,
            "create",
            after=serialize(profile),
            reason=data.reason,
        )
        return serialize(profile)

    return idem(db, user, data.idempotency_key, "create-profile", data.model_dump(mode="json"), operation)


@router.post("/requests/{request_id}/calculations/preview")
def preview_calculation(request_id: str, data: CalculationIn, db: DB, user: Actor):
    check_request(db, user, request_id, "calculations.write")
    require_permission(db, user, "finance.purchase.read", request_id)
    require_permission(db, user, "finance.calculations.read", request_id)
    result = build_calculation(db, user, request_id, data)
    if not financial_access(db, user, request_id, "finance.reward.read"):
        result = public_calculation(result)
    return {"saved": False, "snapshot": result}


@router.post("/requests/{request_id}/calculations")
def save_calculation(request_id: str, data: CalculationIn, db: DB, user: Actor):
    check_request(db, user, request_id, "calculations.write")
    require_permission(db, user, "finance.purchase.read", request_id)
    require_permission(db, user, "finance.calculations.read", request_id)

    def operation():
        snapshot = build_calculation(db, user, request_id, data)
        if data.previous_id:
            previous = db.get(Calculation, data.previous_id)
            if not previous or previous.request_id != request_id:
                error("CALCULATION_NOT_FOUND", "Предыдущая версия расчёта не найдена", 404)
        obj = Calculation(
            request_id=request_id,
            profile_id=data.profile_id,
            previous_id=data.previous_id,
            snapshot=snapshot,
            digest=digest(snapshot),
            reason=data.reason,
            author_id=user.id,
        )
        db.add(obj)
        db.flush()
        audit(
            db,
            user,
            "calculation",
            obj.id,
            "save",
            after={"digest": obj.digest, "profile_id": obj.profile_id},
            reason=data.reason,
        )
        return calculation_view(db, user, obj)

    return idem(
        db,
        user,
        data.idempotency_key,
        f"save-calculation:{request_id}",
        data.model_dump(mode="json"),
        operation,
    )


@router.get("/requests/{request_id}/calculations")
def list_calculations(request_id: str, db: DB, user: Actor):
    check_request(db, user, request_id)
    return {
        "items": [
            calculation_view(db, user, item)
            for item in db.scalars(
                select(Calculation)
                .where(Calculation.request_id == request_id)
                .order_by(Calculation.created_at.desc())
            ).all()
        ]
    }
