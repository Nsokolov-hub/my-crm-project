from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

Positive = Annotated[Decimal, Field(gt=0, max_digits=24, decimal_places=6)]
Nonnegative = Annotated[Decimal, Field(ge=0, max_digits=24, decimal_places=8)]
Currency = Annotated[str, BeforeValidator(lambda value: value.strip().upper() if isinstance(value, str) else value), Field(pattern=r"^[A-Z]{3}$")]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def reject_binary_floats(cls, value):
        def check(node):
            if isinstance(node, float):
                raise ValueError("Десятичные значения передавайте строками")
            if isinstance(node, dict):
                for child in node.values():
                    check(child)
            elif isinstance(node, list):
                for child in node:
                    check(child)

        check(value)
        return value


class Command(Input):
    idempotency_key: str = Field(min_length=8, max_length=128)


class VersionCommand(Command):
    version: int = Field(ge=1)


class ProductIn(Input):
    name: str = Field(min_length=1, max_length=250)
    cas: str | None = None
    no_cas_reason: str = ""
    manufacturer: str = Field(min_length=1, max_length=250)
    article: str = ""
    purity: str = ""
    packaging: str = ""
    unit: Literal["g", "kg", "mg", "l", "ml", "pcs"]
    package_quantity: Positive | None = None
    specification: dict = Field(default_factory=dict)


class VerifyIn(VersionCommand):
    reason: str = Field(min_length=3)


class QuoteIn(Command):
    item_id: str
    item_revision: int = Field(ge=1)
    supplier_id: str
    product_id: str | None = None
    product: ProductIn | None = None
    supplier_request_id: str | None = None
    price: Nonnegative
    currency: Currency
    price_unit: Literal["g", "kg", "mg", "l", "ml", "pcs"]
    available_quantity: Positive
    minimum_quantity: Nonnegative = Decimal("0")
    multiple: Positive = Decimal("0.000001")
    valid_until: date | None = None
    requires_confirmation: bool = False
    is_analogue: bool = False
    sample: bool = False
    attachments: list = Field(default_factory=list)
    terms: dict = Field(default_factory=dict)
    revision_reason: str = ""


class RfqIn(Command):
    supplier_id: str | None = None
    item_ids: list[str] = Field(min_length=1, max_length=10000)
    parent_id: str | None = None
    response_due: date
    comment: str = ""


class SentIn(VersionCommand):
    channel: str = Field(min_length=1, max_length=200)
    sent_at: datetime


class Formula(Input):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,59}$")
    expression: str = Field(min_length=1, max_length=1000)


class ProfileDefinition(Input):
    methodology: Literal["legacy_formula", "itemized_v2"] = "legacy_formula"
    management_currency: Currency = "RUB"
    sale_currency: Currency = "RUB"
    currency_precision: dict[str, int] = Field(default_factory=lambda: {"RUB": 2})
    rounding: Literal["half_up", "half_even", "down"] = "half_up"
    country_of_import: str = ""
    import_country_id: str | None = None
    tax_regime: str = ""
    constants: dict[str, str] = Field(default_factory=dict)
    formulas: list[Formula] = Field(default_factory=list, max_length=40)
    tax_category: str = ""
    customs_rules: list[dict] = Field(default_factory=list)
    customs_fee_brackets: list[dict] = Field(default_factory=list)
    customs_fee_overrides: list[dict] = Field(default_factory=list)
    vat_rate: Nonnegative = Decimal("22")
    vat_deduction_mode: bool = True
    financing_annual_rate: Nonnegative = Decimal("0")
    day_basis: Annotated[int, Field(ge=1, le=366)] = 365
    financing_start_event: Literal["delivery", "shipment", "invoice"] = "delivery"
    default_markup_coefficient: Positive = Decimal("1.5")
    default_bonus_coefficient: Positive = Decimal("1")
    bonus_withdrawal_percent: Annotated[Decimal, Field(ge=0, le=100)] = Decimal("16")
    round_sale_up_to_ruble: bool = False
    default_expenses: list[dict] = Field(default_factory=list, max_length=30)
    exchange_rates: list[dict] = Field(default_factory=list, max_length=30)
    funding_ratio: Annotated[Decimal, Field(ge=0, le=1, decimal_places=8)] = Decimal("1")
    require_same_sale_currency: bool = False
    allow_partial_acceptance: bool = True
    allow_multiple_suppliers: bool = True
    allow_samples: bool = False
    reward_enabled: bool = False
    reward_label: str = ""
    reward_basis: str = ""
    template: dict[str, str | bool] = Field(
        default_factory=lambda: {
            "title": "Коммерческое предложение",
            "show_cas": True,
            "show_manufacturer": True,
        }
    )


class ProfileIn(Command):
    name: str = Field(min_length=1, max_length=200)
    previous_id: str | None = None
    effective_from: date
    effective_until: date | None = None
    reason: str = Field(min_length=3)
    definition: ProfileDefinition


class Rate(Input):
    currency: Currency
    management_per_unit: Annotated[Decimal, Field(gt=0, decimal_places=8, max_digits=24)]
    quoted_units: Annotated[Decimal, Field(gt=0, decimal_places=8)] = Decimal("1")
    date: date
    source: str = Field(min_length=1)
    reason: str = "Ручной ввод"


class Selection(Input):
    quote_id: str | None = None
    quote_item_id: str | None = None
    quote_revision: int | None = Field(default=None, ge=1)
    quantity: Positive | None = None
    unit: Literal["g", "kg", "mg", "l", "ml", "pcs"] | None = None
    markup_coefficient: Positive | None = None
    bonus_coefficient: Positive | None = None
    weight: Nonnegative | None = None
    mass: Nonnegative | None = None
    volume: Nonnegative | None = None
    variables: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_source(self):
        if bool(self.quote_id) == bool(self.quote_item_id):
            raise ValueError("Выберите ровно одну позицию квоты")
        if self.quote_id and (self.quote_revision is None or self.quantity is None or self.unit is None):
            raise ValueError("Для прежней квоты укажите редакцию, количество и единицу")
        if self.quote_item_id and (
            self.quote_revision is not None or self.quantity is not None or self.unit is not None
            or self.variables or self.mass is not None or self.volume is not None
        ):
            raise ValueError("Количество, цену и фасовку позиции табличной квоты сервер берёт из квоты")
        return self


class ExpenseBracket(Input):
    from_amount: Nonnegative
    to_amount: Nonnegative | None = None
    fee: Nonnegative
    valid_from: date | None = None
    valid_to: date | None = None

    @model_validator(mode="after")
    def check_range(self):
        if self.to_amount is not None and self.to_amount < self.from_amount:
            raise ValueError("Конец диапазона должен быть не меньше начала")
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("Дата окончания раньше даты начала")
        return self


class Expense(Input):
    name: str = Field(min_length=1, max_length=120)
    amount: Nonnegative = Decimal("0")
    currency: Currency = "RUB"
    method: Literal[
        "purchase", "mass", "volume", "manual", "BY_QUANTITY", "BY_PURCHASE_VALUE",
        "EQUALLY_BY_POSITION", "BY_WEIGHT", "MANUAL",
    ] = "BY_QUANTITY"
    basis: str = "Расход сделки"
    calculation_type: Literal["FIXED", "PERCENTAGE", "BRACKET", "MANUAL"] = "FIXED"
    percent_base: Literal["PURCHASE", "CUSTOMS_BASE", "DUTY", "COST"] | None = None
    brackets: list[ExpenseBracket] = Field(default_factory=list)
    stage: Literal["INTERNATIONAL_LOGISTICS", "GENERAL"] = "GENERAL"
    scope: Literal["WAVE", "REQUEST"] = "WAVE"
    minimum_amount: Nonnegative = Decimal("0")
    minimum_currency: Currency = "RUB"
    manual: dict[str, str] = Field(default_factory=dict)
    include_in_cost: bool = True
    include_in_cash: bool = True


class ExpenseTypeIn(Command):
    name: str = Field(min_length=1, max_length=120)
    calculation_type: Literal["FIXED", "PERCENTAGE", "BRACKET", "MANUAL"] = "FIXED"
    default_value: Nonnegative = Decimal("0")
    currency_id: str
    distribution_method: Literal["BY_QUANTITY", "BY_PURCHASE_VALUE", "EQUALLY_BY_POSITION", "BY_WEIGHT", "MANUAL"] = "BY_QUANTITY"
    stage: Literal["INTERNATIONAL_LOGISTICS", "GENERAL"] = "GENERAL"
    percent_base: Literal["PURCHASE", "CUSTOMS_BASE", "DUTY", "COST"] | None = None
    brackets: list[ExpenseBracket] = Field(default_factory=list)
    include_in_cost: bool = True
    include_in_cash: bool = True


class ExpenseTypePatch(Input):
    version: int = Field(ge=1)
    active: bool


class CalculationIn(Command):
    delivery_city: str | None = Field(default=None, max_length=150)
    delivery_required: bool = False
    request_version: int = Field(ge=1)
    profile_id: str
    selections: list[Selection] = Field(min_length=1, max_length=100)
    rates: list[Rate] = Field(default_factory=list)
    expenses: list[Expense] | None = Field(default=None, max_length=30)
    internal_adjustment: dict = Field(default_factory=dict)
    payment_terms: dict = Field(default_factory=dict)
    previous_id: str | None = None
    reason: str = ""
    vat_deductible: bool | None = None
    delivery_days: int | None = Field(default=None, ge=0, le=3650)
    expected_wave_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class RequestWaveIn(Command):
    request_version: int = Field(ge=1)
    wave_id: str | None = None


class ProposalIn(Command):
    calculation_id: str
    valid_until: date
    previous_id: str | None = None
    terms: str = Field(min_length=1)


class AcceptanceLine(Input):
    line_id: str
    quantity: Positive
    analogue_reason: str = ""


class AcceptanceIn(VersionCommand):
    lines: list[AcceptanceLine] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=3)


class InvoiceLine(Input):
    execution_id: str
    quantity: Positive


class InvoiceIn(Command):
    proposal_id: str
    lines: list[InvoiceLine] = Field(min_length=1, max_length=100)
    due_date: date
    terms: str = Field(min_length=1)


class PaymentIn(Command):
    invoice_id: str | None = None
    amount: Positive
    currency: Currency
    payment_date: date
    number: str = Field(min_length=1, max_length=200)
    external_id: str | None = None
    comment: str = ""
    evidence_file_id: str | None = None


class PaymentAllocationLine(Input):
    invoice_id: str
    amount: Positive


class PaymentAllocateIn(VersionCommand):
    allocations: list[PaymentAllocationLine] = Field(min_length=1, max_length=100)


class PaymentReverseIn(VersionCommand):
    amount: Positive
    allocation_id: str | None = None
    kind: Literal["unallocate", "refund", "correction"]
    reason: str = Field(min_length=3)


class ApprovalIn(Command):
    execution_ids: list[str] = Field(min_length=1, max_length=100)
    reviewer_id: str


class DecisionIn(VersionCommand):
    decision: Literal["approved", "returned", "rejected"]
    reason: str = ""


class WaveIn(Command):
    number: str = Field(default="", max_length=80)
    supplier_id: str
    route: str = Field(min_length=1, max_length=300)
    origin_country: str = Field(min_length=1, max_length=100)
    owner_id: str
    close_date: date | None = None
    departure_date: date | None = None
    arrival_date: date | None = None
    close_week: int | None = Field(default=None, ge=1, le=53)
    close_year: int | None = Field(default=None, ge=1900, le=9998)
    departure_week: int | None = Field(default=None, ge=1, le=53)
    departure_year: int | None = Field(default=None, ge=1900, le=9998)
    arrival_week: int | None = Field(default=None, ge=1, le=53)
    arrival_year: int | None = Field(default=None, ge=1900, le=9998)
    details: dict = Field(default_factory=dict)


class WaveUpdate(VersionCommand):
    status: (
        Literal["planned", "assembling", "closed", "shipped", "arrived", "completed", "cancelled"] | None
    ) = None
    close_date: date | None = None
    departure_date: date | None = None
    arrival_date: date | None = None
    close_week: int | None = Field(default=None, ge=1, le=53)
    close_year: int | None = Field(default=None, ge=1900, le=9998)
    departure_week: int | None = Field(default=None, ge=1, le=53)
    departure_year: int | None = Field(default=None, ge=1900, le=9998)
    arrival_week: int | None = Field(default=None, ge=1, le=53)
    arrival_year: int | None = Field(default=None, ge=1900, le=9998)
    reason: str = Field(min_length=3)


class AllocateWaveIn(Command):
    execution_id: str
    approval_id: str
    quantity: Positive


class TransferIn(VersionCommand):
    target_wave_id: str
    quantity: Positive
    reason: str = Field(min_length=3)


class FulfillmentIn(Command):
    kind: Literal[
        "ordered", "ready", "shipped", "arrived", "delivered", "claim_opened", "claim_resolved", "correction"
    ]
    quantity: Positive
    occurred_at: datetime
    reason: str = Field(min_length=3)
    evidence_file_id: str | None = None
    correction_of: str | None = None

class ExecutionCancelIn(VersionCommand):
    reason: str = Field(..., min_length=3)

class ExecutionReviseIn(VersionCommand):
    quantity: str = Field(..., pattern=r"^\d+(\.\d{1,6})?$")
    reason: str = Field(..., min_length=3)
