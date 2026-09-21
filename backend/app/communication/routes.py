import hashlib
import io
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Header, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.communication.access import (
    ENTITY_COLUMNS,
    FILE_PERMISSIONS,
    check_chat,
    check_chat_entity,
    check_entity,
    check_file,
    file_view,
)
from app.communication.files import store_quarantine, validate_upload, verified_content
from app.communication.models import Chat, ChatMember, FileRecord, Message, MessageFile
from app.core.config import settings
from app.core.db import get_db, utcnow
from app.core.errors import DomainError
from app.core.models import AppSetting, Notification, OutboxEvent, User
from app.core.security import can, current_user, request_predicate, require_permission, scope_for, task_predicate
from app.core.service import advisory, audit, check_version, idem, serialize
from app.core.service import page as paginate

router = APIRouter(tags=['Общение и файлы'])


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class ChatInput(Input):
    title: str = Field(min_length=1, max_length=250)
    kind: Literal['direct', 'group', 'request', 'wave']
    member_ids: list[str] = Field(default_factory=list, max_length=100)
    entity_id: str | None = None

    @model_validator(mode='after')
    def valid_entity(self):
        if (self.kind in ('request', 'wave')) != bool(self.entity_id):
            raise ValueError('Связанный объект обязателен только для чата заявки или волны')
        if len(set(self.member_ids)) != len(self.member_ids):
            raise ValueError('Участники не должны повторяться')
        return self


class MemberInput(Input):
    user_id: str
    version: int = Field(ge=1)
    history_acknowledged: bool = False


class ChatPatch(Input):
    title: str = Field(min_length=1, max_length=250)
    version: int = Field(ge=1)


class DocumentRef(Input):
    kind: Literal['document', 'quote', 'calculation']
    id: str


class MessageInput(Input):
    content: str = Field(default='', max_length=20000)
    file_ids: list[str] = Field(default_factory=list, max_length=20)
    mention_ids: list[str] = Field(default_factory=list, max_length=100)
    document_refs: list[DocumentRef] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def not_empty(self):
        if not self.content and not self.file_ids and not self.document_refs:
            raise ValueError('Введите сообщение или добавьте вложение')
        if len(self.file_ids) != len(set(self.file_ids)) or len(self.mention_ids) != len(set(self.mention_ids)):
            raise ValueError('Вложения и упоминания не должны повторяться')
        return self


class ReadInput(Input):
    through: int | None = Field(default=None, ge=0)


def chat_view(db: Session, chat: Chat, member: ChatMember) -> dict[str, Any]:
    data = serialize(chat)
    data.pop('direct_key', None)
    data['entity_id'] = chat.request_id or chat.wave_id
    rows = db.execute(select(ChatMember, User.name).join(User, User.id == ChatMember.user_id)
                      .where(ChatMember.chat_id == chat.id, ChatMember.active.is_(True)).order_by(User.name)).all()
    data['members'] = [{'id': row.user_id, 'name': name} for row, name in rows]
    data['member_ids'] = [row.user_id for row, _ in rows]
    data['read_sequence'] = member.read_sequence
    data['unread'] = db.scalar(select(func.count()).select_from(Message).where(
        Message.chat_id == chat.id, Message.sequence > member.read_sequence, Message.author_id != member.user_id)) or 0
    return data


def validate_member(db: Session, user_id: str, chat: Chat) -> User:
    target = db.get(User, user_id)
    if target is None or not target.active or not can(db, target, 'chats.use'):
        raise DomainError('MEMBER_UNAVAILABLE', 'Сотрудник не найден или у него нет доступа к общению', 422, 'member_ids')
    try:
        check_chat_entity(db, target, chat)
    except DomainError:
        raise DomainError('MEMBER_NO_ACCESS', 'У сотрудника нет доступа к связанному объекту', 422, 'member_ids') from None
    return target


@router.post('/chats', status_code=201)
def create_chat(body: ChatInput, user: User = Depends(current_user), db: Session = Depends(get_db),
                idempotency_key: Annotated[str | None, Header()] = None) -> dict:
    require_permission(db, user, 'chats.use')
    if body.entity_id:
        check_entity(db, user, body.kind, body.entity_id)

    def operation():
        member_ids = sorted(set(body.member_ids + [user.id]))
        if body.kind == 'direct' and len(member_ids) != 2:
            raise DomainError('DIRECT_MEMBERS_INVALID', 'Личный чат должен содержать двух сотрудников', 422, 'member_ids')
        direct_key = ':'.join(member_ids) if body.kind == 'direct' else None
        if direct_key:
            advisory(db, f'direct-chat:{direct_key}')
            previous = db.scalar(select(Chat).where(Chat.direct_key == direct_key))
            if previous:
                existing, member = check_chat(db, user, previous.id)
                return chat_view(db, existing, member)
        row = Chat(title=body.title, kind=body.kind, owner_id=user.id, direct_key=direct_key,
                   request_id=body.entity_id if body.kind == 'request' else None,
                   wave_id=body.entity_id if body.kind == 'wave' else None)
        for member_id in member_ids:
            validate_member(db, member_id, row)
        db.add(row)
        db.flush()
        for member_id in member_ids:
            db.add(ChatMember(chat_id=row.id, user_id=member_id))
        db.flush()
        audit(db, user, 'chat', row.id, 'created', after={'kind': row.kind, 'member_ids': member_ids})
        return chat_view(db, row, db.scalar(select(ChatMember).where(ChatMember.chat_id == row.id,
                                                                   ChatMember.user_id == user.id)))
    return idem(db, user, idempotency_key, 'chat:create', body.model_dump(), operation)


@router.get('/chats')
def chats(page: int = 1, page_size: int = 25, q: str = '', entity_id: str | None = None,
          user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    from app.commerce.models import Wave
    from app.crm.models import Request as CRMRequest
    require_permission(db, user, 'chats.use')
    statement = select(Chat).where(Chat.id.in_(select(ChatMember.chat_id).where(
        ChatMember.user_id == user.id, ChatMember.active.is_(True))))
    statement = statement.where(or_(Chat.request_id.is_(None), Chat.request_id.in_(
        select(CRMRequest.id).where(request_predicate(db, user)))))
    if scope_for(db, user, 'waves.write') != 'all' and scope_for(db, user, 'requests.read') != 'all':
        statement = statement.where(or_(Chat.wave_id.is_(None), Chat.wave_id.in_(select(Wave.id).where(Wave.owner_id == user.id))))
    if entity_id:
        statement = statement.where(or_(Chat.request_id == entity_id, Chat.wave_id == entity_id))
    if q:
        statement = statement.where(Chat.title.ilike(f'%{q[:200]}%'))
    result = paginate(db, statement.order_by(Chat.created_at.desc(), Chat.id), page, page_size)
    result['items'] = [chat_view(db, *check_chat(db, user, item['id'])) for item in result['items']]
    return result


@router.get('/chats/{entity_id}')
def get_chat(entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return chat_view(db, *check_chat(db, user, entity_id))


@router.patch('/chats/{entity_id}')
def edit_chat(entity_id: str, body: ChatPatch, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    row, member = check_chat(db, user, entity_id, for_update=True)
    if row.owner_id != user.id:
        raise DomainError('CHAT_OWNER_REQUIRED', 'Изменить чат может его создатель', 403)
    check_version(row, body.version)
    old_title = row.title
    row.title = body.title
    row.version += 1
    audit(db, user, 'chat', row.id, 'renamed', before={'title': old_title}, after={'title': row.title})
    return chat_view(db, row, member)


@router.post('/chats/{entity_id}/members')
def add_member(entity_id: str, body: MemberInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    row, member = check_chat(db, user, entity_id, for_update=True)
    if row.owner_id != user.id or row.kind == 'direct':
        raise DomainError('CHAT_MEMBERS_FORBIDDEN', 'Состав группы изменяет её создатель; состав личного чата неизменяем', 403)
    check_version(row, body.version)
    if not body.history_acknowledged:
        raise DomainError('HISTORY_ACK_REQUIRED', 'Новый участник увидит всю историю группы. Подтвердите добавление.', 422, 'history_acknowledged')
    validate_member(db, body.user_id, row)
    target = db.scalar(select(ChatMember).where(ChatMember.chat_id == row.id, ChatMember.user_id == body.user_id))
    if target and target.active:
        raise DomainError('MEMBER_ALREADY_EXISTS', 'Сотрудник уже участвует в группе', 409)
    if target:
        target.active = True
        target.removed_at = None
        target.version += 1
    else:
        db.add(ChatMember(chat_id=row.id, user_id=body.user_id))
    row.version += 1
    db.flush()
    audit(db, user, 'chat', row.id, 'member_added', after={'user_id': body.user_id, 'history_acknowledged': True})
    return chat_view(db, row, member)


@router.delete('/chats/{entity_id}/members/{member_id}')
def remove_member(entity_id: str, member_id: str, version: int = Query(ge=1),
                  user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    row, member = check_chat(db, user, entity_id, for_update=True)
    if row.kind == 'direct' or row.owner_id != user.id:
        raise DomainError('CHAT_MEMBERS_FORBIDDEN', 'Состав группы изменяет её создатель', 403)
    check_version(row, version)
    if member_id == row.owner_id:
        raise DomainError('CHAT_OWNER_REQUIRED', 'Создатель должен оставаться в группе', 422)
    target = db.scalar(select(ChatMember).where(ChatMember.chat_id == row.id, ChatMember.user_id == member_id,
                                               ChatMember.active.is_(True)))
    if target is None:
        raise DomainError('NOT_FOUND', 'Участник не найден', 404)
    target.active = False
    target.removed_at = utcnow()
    target.version += 1
    row.version += 1
    audit(db, user, 'chat', row.id, 'member_removed', before={'user_id': member_id})
    return chat_view(db, row, member)


def message_view(db: Session, user: User, row: Message) -> dict:
    result = serialize(row)
    result['author_name'] = db.get(User, row.author_id).name
    result['files'] = []
    for file_id in db.scalars(select(MessageFile.file_id).where(MessageFile.message_id == row.id)):
        try:
            result['files'].append(file_view(check_file(db, user, file_id)))
        except DomainError as exc:
            if exc.status not in (403, 404):
                raise
    result['document_refs'] = []
    for reference in row.document_refs:
        try:
            check_entity(db, user, reference['kind'], reference['id'])
            result['document_refs'].append(reference)
        except DomainError as exc:
            if exc.status not in (403, 404):
                raise
    return result


@router.post('/chats/{entity_id}/messages', status_code=201)
def send_message(entity_id: str, body: MessageInput, user: User = Depends(current_user), db: Session = Depends(get_db),
                 idempotency_key: Annotated[str | None, Header()] = None) -> dict:
    check_chat(db, user, entity_id)

    def operation():
        chat, member = check_chat(db, user, entity_id, for_update=True)
        members = db.scalars(select(ChatMember).where(ChatMember.chat_id == chat.id, ChatMember.active.is_(True))).all()
        if not set(body.mention_ids).issubset({m.user_id for m in members}):
            raise DomainError('MENTION_INVALID', 'Упоминать можно только действующих участников чата', 422, 'mention_ids')
        for file_id in body.file_ids:
            attachment = check_file(db, user, file_id)
            if attachment.status != 'clean':
                raise DomainError('FILE_NOT_READY', 'Вложение ещё не прошло проверку или заблокировано', 409, 'file_ids')
            # Standalone chat uploads cannot be forwarded across groups and silently broaden access.
            if attachment.chat_id and attachment.chat_id != chat.id:
                raise DomainError('FILE_CHAT_MISMATCH', 'Вложение другого чата нельзя перенести; используйте исходную ссылку', 422, 'file_ids')
        for reference in body.document_refs:
            check_entity(db, user, reference.kind, reference.id)
        chat.last_sequence += 1
        row = Message(chat_id=chat.id, author_id=user.id, sequence=chat.last_sequence,
                      content=body.content, mention_ids=body.mention_ids,
                      document_refs=[r.model_dump() for r in body.document_refs])
        db.add(row)
        db.flush()
        for file_id in body.file_ids:
            db.add(MessageFile(message_id=row.id, file_id=file_id))
        for target in members:
            if target.user_id != user.id:
                db.add(OutboxEvent(event_key=f'message:{row.id}:{target.user_id}', kind='notification',
                                   payload={'user_id': target.user_id,
                                            'title': 'Вас упомянули в обсуждении' if target.user_id in body.mention_ids else 'Новое сообщение в чате',
                                            'entity_type': 'chat', 'entity_id': chat.id}))
        # Sending does not implicitly mark older messages as read.
        db.flush()
        audit(db, user, 'chat', chat.id, 'message_sent', after={'message_id': row.id, 'sequence': row.sequence})
        return message_view(db, user, row)

    saved = idem(db, user, idempotency_key, f'chat:{entity_id}:send', body.model_dump(), operation)
    # Re-evaluate source file/document rights even for an idempotent replay after a role change.
    return message_view(db, user, db.get(Message, saved['id']))


@router.get('/chats/{entity_id}/messages')
def messages(entity_id: str, after: str | None = None, limit: int = Query(default=50, ge=1, le=100),
             user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    chat, member = check_chat(db, user, entity_id)
    cursor = 0
    if after:
        if after.isascii() and after.isdigit():
            cursor = int(after)
        else:
            previous = db.scalar(select(Message).where(Message.id == after, Message.chat_id == chat.id))
            if previous is None:
                raise DomainError('CURSOR_INVALID', 'Курсор не относится к этому чату', 422, 'after')
            cursor = previous.sequence
        if cursor > chat.last_sequence:
            raise DomainError('CURSOR_INVALID', 'Курсор превышает последнее сообщение', 422, 'after')
    rows = db.scalars(select(Message).where(Message.chat_id == chat.id, Message.sequence > cursor)
                      .order_by(Message.sequence).limit(limit + 1)).all()
    selected = rows[:limit]
    return {'items': [message_view(db, user, row) for row in selected], 'has_more': len(rows) > limit,
            'next_cursor': selected[-1].sequence if selected else cursor, 'last_sequence': chat.last_sequence,
            'read_sequence': member.read_sequence, 'unread': chat_view(db, chat, member)['unread']}


@router.post('/chats/{entity_id}/read')
def read_chat(entity_id: str, body: ReadInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    chat, member = check_chat(db, user, entity_id, for_update=True)
    through = chat.last_sequence if body.through is None else body.through
    if through > chat.last_sequence:
        raise DomainError('READ_CURSOR_INVALID', 'Нельзя прочитать ещё не существующие сообщения', 422, 'through')
    member.read_sequence = max(member.read_sequence, through)
    member.version += 1
    return chat_view(db, chat, member)


@router.get('/notifications')
def notifications(read: bool | None = None, page: int = 1, page_size: int = 25,
                  user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    statement = select(Notification).where(Notification.user_id == user.id)
    
    from app.crm.models import Task
    statement = statement.where(
        or_(
            Notification.entity_type != 'task',
            Notification.entity_id.in_(select(Task.id).where(task_predicate(db, user)))
        )
    )
    
    if read is not None:
        statement = statement.where(Notification.read.is_(read))
    result = paginate(db, statement.order_by(Notification.created_at.desc(), Notification.id), page, page_size)
    # Notification titles contain no financial data; access is checked again when following the object link.
    result['unread'] = db.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_id == user.id, Notification.read.is_(False),
        or_(
            Notification.entity_type != 'task',
            Notification.entity_id.in_(select(Task.id).where(task_predicate(db, user)))
        )
    )) or 0
    return result


@router.post('/notifications/{entity_id}/read')
def read_notification(entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    row = db.scalar(select(Notification).where(Notification.id == entity_id, Notification.user_id == user.id).with_for_update())
    if row is None:
        raise DomainError('NOT_FOUND', 'Уведомление не найдено', 404)
    row.read = True
    return serialize(row)


@router.post('/files', status_code=201)
def upload_file(file: Annotated[UploadFile, File()], entity_type: Annotated[str, Form()],
                entity_id: Annotated[str, Form()], classification: Annotated[str, Form()] = 'general',
                user: User = Depends(current_user), db: Session = Depends(get_db),
                idempotency_key: Annotated[str | None, Header()] = None) -> dict:
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
    content = file.file.read(maximum + 1)
    if not content:
        raise DomainError('FILE_EMPTY', 'Файл пуст', 422, 'file')
    if len(content) > maximum:
        raise DomainError('FILE_TOO_LARGE', f'Максимальный размер файла — {maximum // (1024 * 1024)} МБ', 413, 'file')
    filename, media_type = validate_upload(file.filename or '', file.content_type, content)
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


@router.get('/files')
def files(entity_type: str, entity_id: str, page: int = 1, page_size: int = 25,
          user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    source = check_entity(db, user, entity_type, entity_id)
    request_id = entity_id if entity_type == 'request' else getattr(source, 'request_id', None)
    allowed = ['general']
    for category, permission in FILE_PERMISSIONS.items():
        try:
            require_permission(db, user, permission, request_id)
            allowed.append(category)
        except DomainError as exc:
            if exc.status not in (403, 404):
                raise
    result = paginate(db, select(FileRecord).where(getattr(FileRecord, ENTITY_COLUMNS[entity_type]) == entity_id,
                      FileRecord.classification.in_(allowed)).order_by(FileRecord.created_at.desc()), page, page_size)
    result['items'] = [file_view(check_file(db, user, row['id'])) for row in result['items']]
    return result


@router.get('/files/{entity_id}')
def get_file(entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return file_view(check_file(db, user, entity_id))


@router.get('/files/{entity_id}/download')
def download_file(entity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> StreamingResponse:
    row = check_file(db, user, entity_id)
    require_permission(db, user, 'exports.download', row.request_id)
    if row.status != 'clean':
        raise DomainError('FILE_NOT_READY', 'Скачивание доступно только после успешной проверки файла', 409)
    content = verified_content(row.storage_key, row.sha256, row.size)
    audit(db, user, 'file', row.id, 'downloaded', after={'sha256': row.sha256})
    return StreamingResponse(io.BytesIO(content), media_type='application/octet-stream', headers={
        'Content-Disposition': f"attachment; filename*=UTF-8''{quote(row.name, safe='')}",
        'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "sandbox; default-src 'none'",
        'Cache-Control': 'private, no-store', 'Content-Length': str(len(content))})
