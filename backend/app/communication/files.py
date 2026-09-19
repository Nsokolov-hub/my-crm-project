"""Private immutable uploads: type validation, quarantine, ClamAV INSTREAM and integrity checks."""
import hashlib
import io
import os
import re
import socket
import struct
import zipfile
from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.core.errors import DomainError

TYPES = {'.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
         '.gif': 'image/gif', '.txt': 'text/plain', '.csv': 'text/csv',
         '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
         '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}


def validate_upload(name: str, claimed_type: str | None, content: bytes) -> tuple[str, str]:
    name = re.sub(r'[\x00-\x1f\x7f]', '', name.replace('\\', '/').split('/')[-1]).strip()[:250]
    extension = Path(name).suffix.lower()
    if not name or extension not in TYPES:
        raise DomainError('FILE_TYPE_INVALID', 'Допустимы PDF, PNG, JPEG, GIF, TXT, CSV, XLSX и DOCX', 422, 'file')
    expected = TYPES[extension]
    if claimed_type and claimed_type.split(';')[0].lower() not in (expected, 'application/octet-stream'):
        raise DomainError('FILE_TYPE_MISMATCH', 'Тип файла не соответствует расширению', 422, 'file')
    valid = False
    if extension == '.pdf':
        valid = content.startswith(b'%PDF-') and b'%%EOF' in content[-4096:]
    elif extension == '.png':
        valid = content.startswith(b'\x89PNG\r\n\x1a\n')
    elif extension in ('.jpg', '.jpeg'):
        valid = content.startswith(b'\xff\xd8\xff') and content.endswith(b'\xff\xd9')
    elif extension == '.gif':
        valid = content.startswith((b'GIF87a', b'GIF89a'))
    elif extension in ('.txt', '.csv'):
        try:
            text = content.decode('utf-8-sig')
            valid = '\x00' not in text and not re.search(r'<\s*(?:!doctype\s+html|html|script|svg|iframe)\b', text, re.I)
        except UnicodeDecodeError:
            valid = False
    elif extension in ('.xlsx', '.docx'):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = archive.namelist()
                expected_member = 'xl/workbook.xml' if extension == '.xlsx' else 'word/document.xml'
                valid = '[Content_Types].xml' in names and expected_member in names
                valid = valid and len(names) <= 5000 and sum(z.file_size for z in archive.infolist()) <= 100 * 1024 * 1024
                valid = valid and not any('vbaproject' in n.lower() or n.startswith('/') or '..' in n.split('/') for n in names)
                valid = valid and not any(z.flag_bits & 1 for z in archive.infolist())
        except (zipfile.BadZipFile, OSError):
            valid = False
    if not valid:
        raise DomainError('FILE_CONTENT_MISMATCH', 'Содержимое не соответствует безопасному формату файла', 422, 'file')
    return name, expected


def store_quarantine(content: bytes) -> tuple[str, str]:
    file_id = uuid4().hex
    key = f'quarantine/{file_id[:2]}/{file_id}'
    target = safe_path(key)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with target.open('xb') as stream:
        os.chmod(target, 0o600)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return key, hashlib.sha256(content).hexdigest()


def safe_path(key: str) -> Path:
    root = settings.storage_dir.resolve()
    target = (root / key).resolve()
    if not target.is_relative_to(root) or target == root:
        raise DomainError('FILE_NOT_FOUND', 'Файл не найден', 404)
    return target


def verified_content(key: str, checksum: str, size: int) -> bytes:
    try:
        target = safe_path(key)
        if target.stat().st_size != size:
            raise DomainError('FILE_INTEGRITY', 'Нарушена целостность файла. Обратитесь к администратору', 503)
        data = target.read_bytes()
    except OSError:
        raise DomainError('FILE_UNAVAILABLE', 'Файл временно недоступен', 503) from None
    if hashlib.sha256(data).hexdigest() != checksum:
        raise DomainError('FILE_INTEGRITY', 'Нарушена целостность файла. Обратитесь к администратору', 503)
    return data


def scan_content(content: bytes) -> bool:
    """True = clean, False = infected. Unavailable/ambiguous scanner never implies clean."""
    with socket.create_connection((settings.clamav_host, settings.clamav_port), timeout=10) as stream:
        stream.settimeout(15)
        stream.sendall(b'zINSTREAM\0')
        for offset in range(0, len(content), 65536):
            block = content[offset:offset + 65536]
            stream.sendall(struct.pack('!I', len(block)) + block)
        stream.sendall(struct.pack('!I', 0))
        response = b''
        while b'\0' not in response and len(response) < 8192:
            part = stream.recv(1024)
            if not part:
                break
            response += part
    result = response.split(b'\0')[0].decode('utf-8', errors='replace').strip()
    if result == 'stream: OK':
        return True
    if result.startswith('stream: ') and result.endswith(' FOUND'):
        return False
    raise RuntimeError('Antivirus returned no definitive verdict')
