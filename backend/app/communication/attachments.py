"""Attaching files to CRM objects and handing them out: the file endpoints and other domains use it."""

import hashlib
import io
import re
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.communication.access import ENTITY_COLUMNS, FILE_PERMISSIONS, check_entity, check_file, file_view
from app.communication.files import store_quarantine, validate_upload, verified_content
from app.communication.models import FileRecord
from app.core.config import settings
from app.core.errors import DomainError
from app.core.models import AppSetting, OutboxEvent, User
from app.core.security import require_permission
from app.core.service import advisory, audit, idem


def attach_file(db: Session, user: User, upload: UploadFile, entity_type: str, entity_id: str,
                classification: str = 'general', idempotency_key: str | None = None) -> dict:
    """Quarantine an upload for the object and queue its virus scan; repeats with the same key are replayed."""
    require_permission(db, user, 'files.upload')
    source = check_entity(db, user, entity_type, entity_id)
    if classification not in ('general', *FILE_PERMISSIONS):
        raise DomainError('FILE_CLASSIFICATION_INVALID', 'Выберите допустимую категорию доступа', 422, 'classification')
    mandatory = {'quote': 'purchase', 'calculation': 'calculation'}.get(entity_type)
    if mandatory and classification != mandatory:
        raise DomainError('FILE_CLASSIFICATION_REQUIRED', 'Файл наследует финансовую категорию исходного объекта', 422, 'classification')
    if permission := FILE_PERMISSIONS.get(classification):
        source_request_id = entity_id if entity_type == 'request' else getattr(source, 'request_id', None)
        require_permission(db, user, permission, source_request_id)
    policy = db.scalar(select(AppSetting).where(AppSetting.key == 'file_policy', AppSetting.status == 'published'))
    maximum = settings.max_file_size
    if policy and isinstance(policy.value.get('max_file_size'), int):
        maximum = max(1024, min(100 * 1024 * 1024, policy.value['max_file_size']))
    content = upload.file.read(maximum + 1)
    if not content:
        raise DomainError('FILE_EMPTY', 'Файл пуст', 422, 'file')
    if len(content) > maximum:
        raise DomainError('FILE_TOO_LARGE', f'Максимальный размер файла — {maximum // (1024 * 1024)} МБ', 413, 'file')
    filename, media_type = validate_upload(upload.filename or '', upload.content_type, content)
    checksum = hashlib.sha256(content).hexdigest()

    def operation():
        advisory(db, f'file-hash:{checksum}')
        # Copies keep all original access boundaries. Known uploads are referenced in place, never reclassified.
        originals = db.scalars(select(FileRecord).where(FileRecord.sha256 == checksum)).all()
        for original in originals:
            if original.classification != 'general' and original.classification != classification:
                raise DomainError('FILE_CLASSIFICATION_DOWNGRADE', 'Используйте ссылку на исходный финансовый файл', 422, 'classification')
            if (original.classification != 'general' or original.chat_id) and (
                    original.classification != classification or getattr(original, ENTITY_COLUMNS[entity_type]) != entity_id):
                raise DomainError('FILE_SOURCE_PROTECTED', 'Копия защищённого файла сохраняет исходные права. Прикрепите ссылку.', 422, 'file')
        key, digest = store_quarantine(content)
        row = FileRecord(name=filename, media_type=media_type, size=len(content), sha256=digest,
                         storage_key=key, author_id=user.id, classification=classification,
                         **{ENTITY_COLUMNS[entity_type]: entity_id})
        db.add(row)
        db.flush()
        db.add(OutboxEvent(event_key=f'file-scan:{row.id}', kind='file.scan', payload={'file_id': row.id}))
        audit(db, user, 'file', row.id, 'uploaded', after={'classification': classification, 'sha256': digest, 'size': row.size})
        return file_view(row)
    saved = idem(db, user, idempotency_key, 'file:upload', {'entity_type': entity_type, 'entity_id': entity_id,
                 'classification': classification, 'name': filename, 'sha256': checksum}, operation)
    return file_view(check_file(db, user, saved['id']))


def file_download(db: Session, user: User, file_id: str) -> StreamingResponse:
    """A checked, clean file as an attachment the browser cannot render or sniff."""
    row, content = downloadable(db, user, file_id)
    return StreamingResponse(io.BytesIO(content), media_type='application/octet-stream', headers={
        'Content-Disposition': f"attachment; filename*=UTF-8''{quote(row.name, safe='')}",
        'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "sandbox; default-src 'none'",
        'Cache-Control': 'private, no-store', 'Content-Length': str(len(content))})


ARCHIVE_LIMIT = 300 * 1024 * 1024


def files_archive(db: Session, user: User, file_ids: list[str], name: str) -> StreamingResponse:
    """Several clean files as one zip archive.

    Business remark of 22 October 2026: certificates, usually 5–10 PDF files, are forwarded to the client as one
    archive. Every file passes the same access and download checks as when it is downloaded alone.
    """
    ids = list(dict.fromkeys(value.strip() for value in file_ids if value.strip()))
    if not ids or len(ids) > 100:
        raise DomainError('FILES_REQUIRED', 'Выберите от 1 до 100 проверенных файлов', 422, 'ids')
    buffer, names, total = io.BytesIO(), set(), 0
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for file_id in ids:
            row, content = downloadable(db, user, file_id)
            total += len(content)
            if total > ARCHIVE_LIMIT:
                raise DomainError('ARCHIVE_TOO_LARGE', 'Выбранные файлы больше 300 МБ. Скачайте их частями', 413, 'ids')
            stem, suffix = Path(row.name).stem, Path(row.name).suffix
            entry, number = row.name, 1
            while entry.casefold() in names:
                number += 1
                entry = f'{stem} ({number}){suffix}'
            names.add(entry.casefold())
            archive.writestr(entry, content)
    title = re.sub(r'[\\/:*?"<>|\x00-\x1f]', ' ', name).strip()[:120] or 'Вложения'
    data = buffer.getvalue()
    return StreamingResponse(io.BytesIO(data), media_type='application/zip', headers={
        'Content-Disposition': f"attachment; filename=\"files.zip\"; filename*=UTF-8''{quote(title + '.zip', safe='')}",
        'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store', 'Content-Length': str(len(data))})


def downloadable(db: Session, user: User, file_id: str) -> tuple[FileRecord, bytes]:
    """A file the user may download, after its virus check, with its verified content; the download is audited."""
    row = check_file(db, user, file_id)
    request_id = row.request_id
    if not request_id:
        entity_type, source_id = next(
            (kind, getattr(row, column)) for kind, column in ENTITY_COLUMNS.items() if getattr(row, column)
        )
        source = check_entity(db, user, entity_type, source_id)
        request_id = getattr(source, 'request_id', None)
    require_permission(db, user, 'exports.download', request_id)
    if row.status != 'clean':
        raise DomainError('FILE_NOT_READY', 'Скачивание доступно только после успешной проверки файла', 409)
    content = verified_content(row.storage_key, row.sha256, row.size)
    audit(db, user, 'file', row.id, 'downloaded', after={'sha256': row.sha256})
    return row, content
