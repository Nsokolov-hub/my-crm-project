from pydantic import BaseModel, Field


class CallResultSetting(BaseModel):
    results: list[str] = Field(default_factory=list, description="Список возможных результатов звонка")

class LossReasonSetting(BaseModel):
    reasons: list[str] = Field(default_factory=list, description="Список причин отказа или потерь")

class ChemicalCategorySetting(BaseModel):
    categories: list[str] = Field(default_factory=list, description="Химические категории продуктов")

class RequiredDocumentsSetting(BaseModel):
    documents: list[str] = Field(default_factory=list, description="Типы обязательных документов (паспорта безопасности и т.д.)")

class ShippingFlagSetting(BaseModel):
    flags: list[str] = Field(default_factory=list, description="Опции логистики (ADR, терморежим и т.д.)")

class CalendarSetting(BaseModel):
    holidays: list[str] = Field(default_factory=list, description="Список дат выходных в формате YYYY-MM-DD")
    workdays: list[str] = Field(default_factory=list, description="Список дат рабочих дней (субботников) в формате YYYY-MM-DD")

class TimezoneSetting(BaseModel):
    timezone: str = Field(default="UTC", description="Базовый часовой пояс организации")

class ChatHistoryPolicySetting(BaseModel):
    retention_days: int = Field(default=365, ge=1, le=3650, description="Сколько дней хранить историю")

class FilePolicySetting(BaseModel):
    max_size_mb: int = Field(default=50, ge=1, le=1024, description="Максимальный размер файла в МБ")
    allowed_extensions: list[str] = Field(default_factory=list, description="Разрешенные расширения")

class TaskRemindersSetting(BaseModel):
    default_reminder_minutes: int = Field(default=15, ge=0, description="За сколько минут напоминать по умолчанию")

class CommercialRulesSetting(BaseModel):
    allow_partial_acceptance: bool = Field(default=False)
    allow_analogues: bool = Field(default=False)
    enforce_multiples: bool = Field(default=True, description="Кратность упаковок")
    allow_multiple_suppliers: bool = Field(default=True)
    prepayment_exceptions: list[str] = Field(default_factory=list, description="Исключения по предоплате")
    sale_criteria: str = Field(default="invoice_paid")
    close_criteria: str = Field(default="delivered")
    numbering_format: str = Field(default="REQ-{YYYY}-{NNNN}")

# Реестр всех настроек (ключ -> (Схема, Требуемое право))
SETTING_SCHEMAS: dict[str, tuple[type[BaseModel], str]] = {
    "call_results": (CallResultSetting, "settings.dictionaries.write"),
    "loss_reasons": (LossReasonSetting, "settings.dictionaries.write"),
    "chemical_categories": (ChemicalCategorySetting, "settings.dictionaries.write"),
    "required_documents": (RequiredDocumentsSetting, "settings.dictionaries.write"),
    "shipping_flags": (ShippingFlagSetting, "settings.dictionaries.write"),
    "calendar": (CalendarSetting, "settings.system.write"),
    "timezone": (TimezoneSetting, "settings.system.write"),
    "chat_history_policy": (ChatHistoryPolicySetting, "settings.system.write"),
    "file_policy": (FilePolicySetting, "settings.system.write"),
    "task_reminders": (TaskRemindersSetting, "settings.system.write"),
    "commercial_rules": (CommercialRulesSetting, "settings.commerce.write"),
}
