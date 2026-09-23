from pydantic import BaseModel, Field
from dataclasses import dataclass

@dataclass
class SettingMeta:
    schema: type[BaseModel]
    permission: str
    consumer: str | None
    effect: str
    is_supported: bool



class CallResultSetting(BaseModel):
    results: list[str] = Field(default_factory=list, description="Список возможных результатов звонка")


class LossReasonSetting(BaseModel):
    reasons: list[str] = Field(default_factory=list, description="Список причин отказа или потерь")


class ChemicalCategorySetting(BaseModel):
    categories: list[str] = Field(default_factory=list, description="Химические категории продуктов")


class RequiredDocumentsSetting(BaseModel):
    documents: list[str] = Field(
        default_factory=list, description="Типы обязательных документов (паспорта безопасности и т.д.)"
    )


class ShippingFlagSetting(BaseModel):
    flags: list[str] = Field(default_factory=list, description="Опции логистики (ADR, терморежим и т.д.)")


class CalendarSetting(BaseModel):
    holidays: list[str] = Field(default_factory=list, description="Список дат выходных в формате YYYY-MM-DD")
    workdays: list[str] = Field(
        default_factory=list, description="Список дат рабочих дней (субботников) в формате YYYY-MM-DD"
    )


class TimezoneSetting(BaseModel):
    timezone: str = Field(default="UTC", description="Базовый часовой пояс организации")


class ChatHistoryPolicySetting(BaseModel):
    retention_days: int = Field(default=365, ge=1, le=3650, description="Сколько дней хранить историю")


class FilePolicySetting(BaseModel):
    max_size_mb: int = Field(default=50, ge=1, le=1024, description="Максимальный размер файла в МБ")
    allowed_extensions: list[str] = Field(default_factory=list, description="Разрешенные расширения")


class TaskRemindersSetting(BaseModel):
    default_reminder_minutes: int = Field(
        default=15, ge=0, description="За сколько минут напоминать по умолчанию"
    )


class CommercialRulesSetting(BaseModel):
    allow_analogues: bool = Field(default=False)
    enforce_multiples: bool = Field(default=True, description="Кратность упаковок")
    prepayment_exceptions: list[str] = Field(default_factory=list, description="Исключения по предоплате")
    sale_criteria: str = Field(default="invoice_paid")
    close_criteria: str = Field(default="delivered")
    numbering_format: str = Field(default="REQ-{YYYY}-{NNNN}")


# Реестр всех настроек (ключ -> Метаданные)
SETTING_META: dict[str, SettingMeta] = {
    "call_results": SettingMeta(CallResultSetting, "settings.dictionaries.write", "UI: Форма звонка", "Определяет список исходов звонка в CRM", True),
    "loss_reasons": SettingMeta(LossReasonSetting, "settings.dictionaries.write", "UI: Закрытие сделки", "Список причин отказа", True),
    "chemical_categories": SettingMeta(ChemicalCategorySetting, "settings.dictionaries.write", "UI: Каталог", "Химические категории", True),
    "required_documents": SettingMeta(RequiredDocumentsSetting, "settings.dictionaries.write", "UI: Комплаенс", "Типы обязательных документов", True),
    "shipping_flags": SettingMeta(ShippingFlagSetting, "settings.dictionaries.write", "UI: Логистика", "Опции логистики", True),
    "calendar": SettingMeta(CalendarSetting, "settings.system.write", None, "Учет рабочих дней (сейчас не поддерживается сервером)", False),
    "timezone": SettingMeta(TimezoneSetting, "settings.system.write", None, "Базовый часовой пояс (не применяется)", False),
    "chat_history_policy": SettingMeta(ChatHistoryPolicySetting, "settings.system.write", None, "Сколько дней хранить историю (заглушка)", False),
    "file_policy": SettingMeta(FilePolicySetting, "settings.system.write", None, "Ограничения на файлы (не применяется)", False),
    "task_reminders": SettingMeta(TaskRemindersSetting, "settings.system.write", None, "Напоминания по задачам (в разработке)", False),
    "commercial_rules": SettingMeta(CommercialRulesSetting, "settings.commerce.write", None, "Правила коммерции (не применяется на сервере, используются настройки из профиля)", False),
}
