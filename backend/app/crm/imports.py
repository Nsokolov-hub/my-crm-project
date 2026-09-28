import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile
from fastapi.responses import Response
from openpyxl import Workbook, load_workbook
from pydantic import Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.errors import DomainError
from app.core.models import OutboxEvent, User
from app.core.routes import Input
from app.core.security import can, check_client, client_predicate, current_user, require_permission
from app.core.service import advisory, audit, idem, lock, serialize
from app.core.service import page as paginate
from app.crm.models import Contact, Counterparty, ImportBatch, ImportRow

router = APIRouter(tags=["Импорт клиентской базы"])
COLUMNS = {
    "external_id": "Внешний ID",
    "name": "Организация",
    "kind": "Тип контрагента",
    "country": "Страна",
    "city": "Город",
    "tax_id": "ИНН",
    "contact": "Контактное лицо",
    "phone": "Телефон",
    "email": "Электронная почта",
    "source": "Источник",
    "comment": "Комментарий",
    "owner_id": "Ответственный",
}
CALLS_COLUMNS = {
    "name": "Название",
    "tax_id": "ИНН",
    "profile": "ПРОФИЛЬ",
    "category": "Тип/категория",
    "federal_district": "Федеральный округ",
    "city": "Регион/город",
    "registration_number": "ОГРН",
    "business_profile": "Основной профиль",
    "okved": "ОКВЭД",
    "revenue": "Выручка (посл. изв. год)",
    "website": "Сайт",
    "email": "E-mail",
    "phone": "Телефон",
    "comment": "Примечание",
    "procurement_phone": "Телефон отдела закупок/снабжения",
    "procurement_email": "E-mail отдела закупок/снабжения",
}
FIELD_LIMITS = {
    "external_id": 250,
    "name": 250,
    "kind": 30,
    "country": 100,
    "city": 150,
    "tax_id": 100,
    "contact": 250,
    "phone": 100,
    "email": 254,
    "source": 250,
    "comment": 2000,
    "owner_id": 36,
    "profile": 250,
    "category": 250,
    "federal_district": 100,
    "registration_number": 100,
    "business_profile": 1000,
    "okved": 100,
    "revenue": 250,
    "website": 500,
    "procurement_phone": 250,
    "procurement_email": 500,
    "raw_email": 500,
}
KIND_ALIASES = {
    "клиент": "client",
    "заказчик": "client",
    "client": "client",
    "поставщик": "supplier",
    "supplier": "supplier",
    "клиент и поставщик": "both",
    "клиент/поставщик": "both",
    "оба": "both",
    "both": "both",
}


def xlsx(rows: list[list[Any]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Клиентская база"
    for row in rows:
        sheet.append([str(v) if v is not None else "" for v in row])
        for cell in sheet[sheet.max_row]:
            cell.data_type = "s"
            cell.number_format = "@"
    for col in sheet.columns:
        sheet.column_dimensions[col[0].column_letter].width = 25
    sheet.freeze_panes = "A2"
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def string_value(cell: Any) -> str:
    if cell.value is None:
        return ""
    if isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool):
        if isinstance(cell.value, int) or cell.value.is_integer():
            result = str(int(cell.value))
            if re.fullmatch(r"0+", cell.number_format or ""):
                return result.zfill(len(cell.number_format))
            return result
    return str(cell.value).strip()


def literal_like(value: str) -> str:
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def parse_rows(data: bytes, mapping: dict[str, str], mode: str = "general") -> list[dict[str, Any]]:
    try:
        archive = ZipFile(io.BytesIO(data))
        infos = archive.infolist()
        if (
            len(infos) > 10000
            or sum(i.file_size for i in infos) > 100 * 1024 * 1024
            or any("vbaProject" in i.filename for i in infos)
        ):
            raise DomainError("IMPORT_UNSAFE", "Архив слишком большой или содержит макросы")
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
    except (BadZipFile, ValueError, KeyError):
        raise DomainError(
            "IMPORT_INVALID_XLSX", "Не удалось прочитать XLSX. Используйте шаблон импорта."
        ) from None
    try:
        sheet = workbook["Данные"] if mode == "calls" and "Данные" in workbook else workbook.active
        if sheet.max_row and sheet.max_row > 10001:
            raise DomainError("IMPORT_ROW_LIMIT", "В одной партии допускается до 10 000 строк")
        raw = iter(sheet.iter_rows())
        headers = [string_value(c) for c in next(raw, [])]
        if len(headers) > 100:
            raise DomainError("IMPORT_COLUMN_LIMIT", "В файле слишком много колонок")
        labels = CALLS_COLUMNS if mode == "calls" else COLUMNS
        index = {
            key: headers.index(mapping.get(key, label))
            for key, label in labels.items()
            if mapping.get(key, label) in headers
        }
        if mode == "calls" and ("name" not in index or "tax_id" not in index):
            raise DomainError(
                "IMPORT_MAPPING_REQUIRED", "Сопоставьте столбцы «Название» и «ИНН»", field="mapping"
            )
        if mode != "calls" and "name" not in index and "contact" not in index:
            raise DomainError(
                "IMPORT_MAPPING_REQUIRED", "Сопоставьте колонку организации или контакта", field="mapping"
            )
        output = []
        for number, cells in enumerate(raw, 2):
            if number > 10001:
                raise DomainError("IMPORT_ROW_LIMIT", "В одной партии допускается до 10 000 строк")
            if not any(c.value is not None for c in cells):
                continue
            row = {key: string_value(cells[i]) if i < len(cells) else "" for key, i in index.items()}
            if mode == "calls":
                row = {
                    key: "" if value.casefold() in {"не найдено", "нет данных", "—", "-"} else value
                    for key, value in row.items()
                }
                row["source"] = "База обзвона"
            errors = []
            if any(c.data_type == "f" for c in cells):
                errors.append({"field": "row", "message": "Формулы не допускаются: вставьте значения"})
            if mode == "calls" and not row.get("name"):
                errors.append({"field": "name", "message": "Укажите название организации"})
            if mode == "calls" and not row.get("tax_id"):
                errors.append({"field": "tax_id", "message": "Укажите ИНН"})
            if mode != "calls" and not row.get("name") and not row.get("contact"):
                errors.append({"field": "name", "message": "Укажите организацию или контакт"})
            kind = row.get("kind", "").strip().casefold()
            if kind and kind not in KIND_ALIASES:
                errors.append({"field": "kind", "message": "Тип: клиент, поставщик или клиент и поставщик"})
            row["kind"] = KIND_ALIASES.get(kind, "")
            row["email"] = row.get("email", "").strip().lower()
            if mode == "calls" and row["email"] and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", row["email"]):
                row["raw_email"] = row["email"]
                row["email"] = ""
            if mode != "calls":
                row["phone"] = re.sub(r"[^\d+]", "", row.get("phone", ""))
            if mode != "calls" and row["email"] and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", row["email"]):
                errors.append({"field": "email", "message": "Некорректная электронная почта"})
            for field, value in row.items():
                if len(value) > FIELD_LIMITS[field]:
                    errors.append(
                        {"field": field, "message": f"Допускается не более {FIELD_LIMITS[field]} символов"}
                    )
            output.append({"row_number": number, "data": row, "errors": errors})
        return output
    finally:
        workbook.close()


@router.get("/imports/template.xlsx")
def template(user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    require_permission(db, user, "imports.write")
    return Response(
        xlsx([list(COLUMNS.values())]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="clients-template-v1.xlsx"'},
    )


@router.get("/imports/template-calls.xlsx")
def calls_template(user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    require_permission(db, user, "calls.write")
    require_permission(db, user, "clients.write")
    return Response(
        xlsx([list(CALLS_COLUMNS.values())]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="calls-template.xlsx"'},
    )


@router.post("/imports/preview")
def preview(
    file: UploadFile = File(),
    mapping: str = Form("{}"),
    mode: str = Form("general"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if mode not in ("general", "calls"):
        raise DomainError("IMPORT_MODE_INVALID", "Неизвестный режим импорта")
    require_permission(db, user, "calls.write" if mode == "calls" else "imports.write")
    require_permission(db, user, "clients.write")
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise DomainError("IMPORT_TYPE_INVALID", "Загрузите файл XLSX без макросов")
    data = file.file.read(settings.max_file_size + 1)
    if len(data) > settings.max_file_size:
        raise DomainError("FILE_TOO_LARGE", "Размер файла превышает разрешённый предел", 413)
    try:
        columns = json.loads(mapping)
        if not isinstance(columns, dict) or any(
            k not in (CALLS_COLUMNS if mode == "calls" else COLUMNS) or not isinstance(v, str)
            for k, v in columns.items()
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise DomainError(
            "MAPPING_INVALID", "Сопоставление колонок должно быть JSON-объектом", field="mapping"
        ) from None
    
    parsed = parse_rows(data, columns, mode)
    key = f"imports/{uuid4().hex}.xlsx"
    path = settings.storage_dir / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    batch = ImportBatch(
        author_id=user.id,
        source_name=Path(file.filename or "import.xlsx").name[:250],
        file_key=key,
        file_hash=hashlib.sha256(data).hexdigest(),
        mapping={**columns, "__mode__": mode},
    )
    db.add(batch)
    db.flush()
    seen_external: set[str] = set()
    seen_tax_id: set[str] = set()
    seen_name_country: set[tuple[str, str]] = set()
    for raw in parsed:
        values = raw["data"]
        row = ImportRow(batch_id=batch.id, **raw)
        external = values.get("external_id")
        tax_id = values.get("tax_id", "").strip()
        name_country = (values.get("name", "").casefold(), values.get("country", "").casefold())
        if external and external in seen_external:
            row.errors = [
                *row.errors,
                {"field": "external_id", "message": "Внешний ID повторяется внутри файла"},
            ]
        if external:
            seen_external.add(external)
        if tax_id and tax_id in seen_tax_id:
            row.errors = [
                *row.errors,
                {"field": "tax_id", "message": "ИНН повторяется внутри файла"},
            ]
        if tax_id:
            seen_tax_id.add(tax_id)
        if not tax_id and all(name_country) and name_country in seen_name_country:
            row.errors = [
                *row.errors,
                {"field": "name", "message": "Организация и страна повторяются внутри файла"},
            ]
        if not tax_id and all(name_country):
            seen_name_country.add(name_country)
        conditions = []
        if external:
            conditions.append(Counterparty.external_id == external)
        if tax_id:
            conditions.append(Counterparty.tax_id == tax_id)
        if all(name_country):
            conditions.append(
                Counterparty.name.ilike(literal_like(values["name"]), escape='\\') &
                Counterparty.country.ilike(literal_like(values["country"]), escape='\\')
            )
        if conditions:
            row.candidate_ids = list(
                db.scalars(
                    select(Counterparty.id)
                    .where(client_predicate(db, user, "clients.write"), or_(*conditions))
                    .limit(20)
                )
            )
            if row.candidate_ids:
                row.action = "skip" if mode == "calls" else "conflict"
        owner_id = values.get("owner_id")
        if owner_id:
            target = db.get(User, owner_id)
            if not target or not target.active:
                row.errors = [*row.errors, {"field": "owner_id", "message": "Ответственный не найден"}]
            elif owner_id != user.id:
                require_permission(db, user, "requests.assign")
        if row.errors:
            row.action = "error"
        db.add(row)
    db.flush()
    batch.summary = summary(db, batch.id)
    audit(db, user, "import", batch.id, "preview", after=batch.summary)
    return {
        **serialize(batch),
        "rows": paginate(
            db, select(ImportRow).where(ImportRow.batch_id == batch.id).order_by(ImportRow.row_number), 1, 100
        )["items"],
    }


def summary(db: Session, batch_id: str) -> dict[str, int]:
    rows = db.scalars(select(ImportRow).where(ImportRow.batch_id == batch_id)).all()
    return {
        **{
            k: sum(r.action == k for r in rows)
            for k in ("create", "update", "error", "conflict", "skip", "created", "updated")
        },
        "total": len(rows),
        "valid": sum(not r.errors for r in rows),
    }


def owned_batch(db: Session, user: User, entity_id: str) -> ImportBatch:
    row = db.get(ImportBatch, entity_id)
    if row is None or row.author_id != user.id:
        raise DomainError("NOT_FOUND", "Партия импорта недоступна", 404)
    require_permission(db, user, "calls.write" if row.mapping.get("__mode__") == "calls" else "imports.write")
    return row


@router.get("/imports")
def batches(
    page: int = 1, page_size: int = 25, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    if not can(db, user, "imports.write") and not can(db, user, "calls.write"):
        require_permission(db, user, "imports.write")
    return paginate(
        db,
        select(ImportBatch).where(ImportBatch.author_id == user.id).order_by(ImportBatch.created_at.desc()),
        page,
        page_size,
    )


@router.get("/imports/{entity_id}")
def batch_detail(
    entity_id: str,
    page: int = 1,
    page_size: int = 100,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = owned_batch(db, user, entity_id)
    return {
        **serialize(row),
        "rows": paginate(
            db,
            select(ImportRow).where(ImportRow.batch_id == entity_id).order_by(ImportRow.row_number),
            page,
            page_size,
        )["items"],
    }


class Resolution(Input):
    row_id: str
    action: Literal["create", "update", "skip"]
    match_id: str | None = None


class Confirmation(Input):
    decisions: list[Resolution] = Field(default_factory=list)


@router.post("/imports/{entity_id}/confirm")
def confirm(
    entity_id: str,
    body: Confirmation,
    idempotency_key: str | None = Header(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    owned_batch(db, user, entity_id)
    require_permission(db, user, "clients.write")

    def operation() -> dict[str, Any]:
        batch = lock(db, ImportBatch, entity_id)
        if batch.status != "preview":
            return serialize(batch)
        for decision in body.decisions:
            row = db.get(ImportRow, decision.row_id)
            if row is None or row.batch_id != batch.id or row.errors:
                raise DomainError(
                    "IMPORT_RESOLUTION_INVALID", "Решение не относится к корректной строке партии"
                )
            if decision.action == "update":
                if not decision.match_id:
                    raise DomainError("MATCH_REQUIRED", "Для обновления выберите существующую карточку")
                check_client(db, user, decision.match_id, "clients.write")
            row.action = decision.action
            row.match_id = decision.match_id if decision.action == "update" else None
        db.flush()
        if db.scalar(
            select(ImportRow.id).where(ImportRow.batch_id == batch.id, ImportRow.action == "conflict")
        ):
            raise DomainError(
                "IMPORT_CONFLICT_UNRESOLVED", "Сопоставьте конфликтующие строки или пропустите их", 409
            )
        batch.status = "queued"
        db.add(
            OutboxEvent(
                event_key=f"import:{batch.id}",
                kind="import.confirm",
                payload={"batch_id": batch.id, "user_id": user.id},
            )
        )
        audit(db, user, "import", batch.id, "queued", after=summary(db, batch.id))
        return serialize(batch)

    return idem(db, user, idempotency_key, f"imports.confirm:{entity_id}", body.model_dump(), operation)


def process_import(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    user = db.get(User, payload["user_id"])
    if user is None or not user.active:
        raise DomainError("IMPORT_ACCESS_REVOKED", "Доступ инициатора импорта отозван")
    batch = lock(db, ImportBatch, payload["batch_id"])
    require_permission(db, user, "calls.write" if batch.mapping.get("__mode__") == "calls" else "imports.write")
    require_permission(db, user, "clients.write")
    if batch.status == "completed":
        return serialize(batch)
    advisory(db, "import.counterparties")
    for row in db.scalars(
        select(ImportRow).where(ImportRow.batch_id == batch.id).order_by(ImportRow.row_number)
    ):
        if row.action not in ("create", "update"):
            continue
        data = row.data
        external = data.get("external_id") or None
        if row.action == "create":
            duplicate = None
            if external:
                duplicate = db.scalar(select(Counterparty.id).where(Counterparty.external_id == external))
            if not duplicate and data.get("tax_id"):
                duplicate = db.scalar(select(Counterparty.id).where(Counterparty.tax_id == data["tax_id"]))
            if not duplicate and not data.get("tax_id") and data.get("name") and data.get("country"):
                duplicate = db.scalar(select(Counterparty.id).where(
                    Counterparty.name.ilike(literal_like(data["name"]), escape='\\'),
                    Counterparty.country.ilike(literal_like(data["country"]), escape='\\'),
                ))
            if duplicate:
                row.action = "skip"
                row.errors = [*row.errors, {"field": "tax_id" if data.get("tax_id") else "name", "message": "Карточка уже существует; строка не перезаписана"}]
                continue
        client = check_client(db, user, row.match_id, "clients.write") if row.action == "update" else None
        if client and external and client.external_id not in (None, external):
            row.action = "skip"
            row.errors = [*row.errors, {"field": "external_id", "message": "Карточка связана с другим внешним ID"}]
            continue
        if client and data.get("tax_id"):
            other = db.scalar(select(Counterparty.id).where(
                Counterparty.tax_id == data["tax_id"], Counterparty.id != client.id,
            ))
            if other:
                row.action = "skip"
                row.errors = [*row.errors, {"field": "tax_id", "message": "ИНН уже принадлежит другой карточке"}]
                continue
        before = serialize(client) if client else None
        if not client:
            client = Counterparty(
                name=data.get("name") or data.get("contact"),
                owner_id=data.get("owner_id") or user.id,
                external_id=external,
                kind=data.get("kind") or "client",
                details={},
            )
            db.add(client)
        for field in ("name", "country", "tax_id", "email", "phone"):
            if data.get(field):
                setattr(client, field, data[field])
        if data.get("kind"):
            client.kind = data["kind"]
        # The original acquisition source survives later imports, like the call history.
        if data.get("source") and not client.source:
            client.source = data["source"]
        if external:
            client.external_id = external
        details = dict(client.details or {})
        for field in (
            "city", "comment", "profile", "category", "federal_district", "registration_number",
            "business_profile", "okved", "revenue", "website", "procurement_phone",
            "procurement_email", "raw_email",
        ):
            if data.get(field):
                details[field] = data[field]
        client.details = details
        if before:
            client.version += 1
        db.flush()
        if data.get("contact"):
            existing = db.scalar(
                select(Contact).where(Contact.client_id == client.id, Contact.name == data["contact"])
            )
            if not existing:
                db.add(
                    Contact(
                        client_id=client.id,
                        name=data["contact"],
                        email=data.get("email") or None,
                        phone=data.get("phone") or None,
                    )
                )
        row.match_id = client.id
        row.action = "updated" if before else "created"
        audit(
            db,
            user,
            "counterparty",
            client.id,
            "imported",
            before,
            serialize(client),
            f"Партия {batch.id}, строка {row.row_number}",
        )
    db.flush()
    batch.status = "completed"
    batch.summary = summary(db, batch.id)
    audit(db, user, "import", batch.id, "completed", after=batch.summary)
    return serialize(batch)


@router.get("/imports/{entity_id}/errors.xlsx")
def errors_file(
    entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    owned_batch(db, user, entity_id)
    require_permission(db, user, "exports.download")
    rows = [["Строка", "Поле", "Ошибка"]]
    for row in db.scalars(select(ImportRow).where(ImportRow.batch_id == entity_id)):
        rows += [[row.row_number, e["field"], e["message"]] for e in row.errors]
    audit(db, user, "import", entity_id, "errors_exported")
    return Response(
        xlsx(rows),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="import-errors.xlsx"'},
    )
