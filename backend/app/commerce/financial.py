from copy import deepcopy
from datetime import date

from fastapi import APIRouter
from sqlalchemy import select

from app.core.errors import error
from app.core.security import can, check_request, has_request_permission, require_permission
from app.core.service import advisory, audit, check_version, idem, lock, serialize
from app.crm.models import Request as CRMRequest

from .calculator import calculate, digest, example_profile, validate_profile
from .models import Calculation, CalculationProfile, Product, Quote
from .procurement import DB, Actor, check_quote, product_view, validate_quantity
from .schemas import CalculationIn, ProfileIn

router = APIRouter(tags=["Расчёты и настраиваемые профили"])


def filter_profile_definition(definition: dict, *, purchase: bool, reward: bool, profit: bool) -> dict:
    definition = deepcopy(definition)
    if not reward:
        definition.pop("reward_enabled", None)
        definition.pop("reward_label", None)
        definition.pop("reward_basis", None)
    # Profile variables and formulas are user-defined: their names do not identify
    # the financial information they contain or can be used to reconstruct.
    if not (purchase and reward and profit):
        definition.pop("constants", None)
        definition.pop("formulas", None)
    return definition


def profile_view(db, user, profile: CalculationProfile) -> dict:
    data = serialize(profile)
    data["definition"] = filter_profile_definition(
        data.get("definition", {}),
        purchase=can(db, user, "finance.purchase.read"),
        reward=can(db, user, "finance.reward.read"),
        profit=can(db, user, "finance.profit.read"),
    )
    return data


def filter_calculation_snapshot(db, user, request_id: str, snapshot: dict) -> dict:
    if not snapshot:
        return snapshot
    snapshot = deepcopy(snapshot)

    can_purchase = has_request_permission(db, user, request_id, "finance.purchase.read")
    can_calculations = has_request_permission(db, user, request_id, "finance.calculations.read")
    can_reward = has_request_permission(db, user, request_id, "finance.reward.read")
    can_profit = has_request_permission(db, user, request_id, "finance.profit.read")
    all_finances = can_purchase and can_reward and can_profit

    lines = snapshot.get("lines", [])
    for line in lines:
        if not can_purchase:
            line.pop("quote", None)

        if "detail" in line:
            if not can_calculations:
                line.pop("detail", None)
            elif not all_finances:
                # Expose only known outputs; arbitrary constants/intermediate
                # expressions may alias a denied price, reward or margin.
                allowed = {"quantity", "sale_net", "sale_tax"}
                if can_purchase:
                    allowed.add("purchase")
                if can_reward:
                    allowed.update(("reward", "manager_bonus"))
                if can_profit:
                    allowed.update(("cost", "profit", "margin"))
                line["detail"] = {key: value for key, value in line["detail"].items() if key in allowed}

    if not can_calculations:
        snapshot.pop("profile", None)
    elif "profile" in snapshot:
        snapshot["profile"] = filter_profile_definition(
            snapshot["profile"], purchase=can_purchase, reward=can_reward, profit=can_profit
        )

    if not can_calculations or not all_finances:
        # Inputs include per-line coefficient overrides and expense allocations
        # can disclose the purchase basis, even after detail has been filtered.
        snapshot.pop("input", None)
        snapshot.pop("expense_allocations", None)
        snapshot.pop("rates", None)

    return snapshot


def calculation_view(db, user, calculation: Calculation) -> dict:
    result = serialize(calculation)

    snapshot = result.get("snapshot", {})
    if snapshot:
        result["snapshot"] = filter_calculation_snapshot(db, user, calculation.request_id, snapshot)

    if not has_request_permission(db, user, calculation.request_id, "finance.calculations.read"):
        result.pop("reason", None)

    return result


def build_calculation(db, user, request_id: str, data: CalculationIn) -> dict:
    advisory(db, f"request-commerce:{request_id}")
    req = lock(db, CRMRequest, request_id)
    check_version(req, data.request_version)
    profile = db.get(CalculationProfile, data.profile_id)
    if (
        not profile
        or profile.status != "published"
        or profile.effective_from > date.today()
        or (profile.effective_until and profile.effective_until < date.today())
    ):
        error(
            "PROFILE_NOT_EFFECTIVE", "Выберите активный опубликованный профиль, действующий на дату расчёта"
        )
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
            profile_view(db, user, profile)
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
        return {"id": profile.id}

    def reconstruct(result: dict) -> dict:
        profile = db.get(CalculationProfile, result["id"])
        return profile_view(db, user, profile)

    return idem(
        db, user, data.idempotency_key, "create-profile", data.model_dump(mode="json"), operation, reconstruct
    )


@router.post("/profiles/{profile_id}/publish")
def publish_profile(profile_id: str, db: DB, user: Actor):
    require_permission(db, user, "profiles.write")
    require_permission(db, user, "templates.write")
    profile = db.get(CalculationProfile, profile_id)
    if not profile:
        error("PROFILE_NOT_FOUND", "Профиль не найден", 404)
    if profile.status == "published":
        error("PROFILE_ALREADY_PUBLISHED", "Профиль уже опубликован", 400)

    # Архивируем предыдущий опубликованный профиль, если это новая версия
    if profile.previous_id:
        prev = db.get(CalculationProfile, profile.previous_id)
        if prev and prev.status == "published":
            prev.status = "archived"
            prev.effective_until = date.today()

    profile.status = "published"
    profile.effective_from = date.today()
    db.flush()
    audit(db, user, "calculation_profile", profile.id, "publish", after=serialize(profile))
    return profile_view(db, user, profile)


@router.post("/requests/{request_id}/calculations/preview")
def preview_calculation(request_id: str, data: CalculationIn, db: DB, user: Actor):
    check_request(db, user, request_id, "calculations.write")
    require_permission(db, user, "finance.purchase.read", request_id)
    require_permission(db, user, "finance.calculations.read", request_id)
    result = build_calculation(db, user, request_id, data)
    result = filter_calculation_snapshot(db, user, request_id, result)
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
        return {"id": obj.id}

    def reconstruct(result: dict) -> dict:
        obj = db.get(Calculation, result["id"])
        return calculation_view(db, user, obj)

    return idem(
        db,
        user,
        data.idempotency_key,
        f"save-calculation:{request_id}",
        data.model_dump(mode="json"),
        operation,
        reconstruct,
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
