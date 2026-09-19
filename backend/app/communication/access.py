from sqlalchemy import select
from sqlalchemy.orm import Session

from app.communication.models import Chat, ChatMember, FileRecord
from app.core.errors import DomainError
from app.core.models import User
from app.core.security import check_client, check_request, require_permission, scope_for

FILE_PERMISSIONS = {'purchase': 'finance.purchase.read', 'calculation': 'finance.calculations.read',
                    'reward': 'finance.reward.read', 'profit': 'finance.profit.read'}
ENTITY_COLUMNS = {'request': 'request_id', 'client': 'client_id', 'chat': 'chat_id', 'wave': 'wave_id',
                  'quote': 'quote_id', 'calculation': 'calculation_id', 'document': 'document_id'}


def check_wave(db: Session, user: User, entity_id: str):
    from app.commerce.models import Wave
    row = db.get(Wave, entity_id)
    if row is None or not (row.owner_id == user.id or scope_for(db, user, 'waves.write') == 'all'
                           or scope_for(db, user, 'requests.read') == 'all'):
        raise DomainError('NOT_FOUND', 'Объект не найден или недоступен', 404)
    return row


def check_chat(db: Session, user: User, entity_id: str, *, for_update: bool = False) -> tuple[Chat, ChatMember]:
    require_permission(db, user, 'chats.use')
    statement = select(Chat).where(Chat.id == entity_id)
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    chat = db.scalar(statement)
    member = db.scalar(select(ChatMember).where(ChatMember.chat_id == entity_id,
                                               ChatMember.user_id == user.id, ChatMember.active.is_(True)))
    if chat is None or member is None:
        raise DomainError('NOT_FOUND', 'Объект не найден или недоступен', 404)
    check_chat_entity(db, user, chat)
    return chat, member


def check_chat_entity(db: Session, user: User, chat: Chat) -> None:
    if chat.request_id:
        check_request(db, user, chat.request_id)
    if chat.wave_id:
        check_wave(db, user, chat.wave_id)


def check_entity(db: Session, user: User, entity_type: str, entity_id: str):
    if entity_type == 'request':
        return check_request(db, user, entity_id)
    if entity_type == 'client':
        return check_client(db, user, entity_id)
    if entity_type == 'chat':
        return check_chat(db, user, entity_id)[0]
    if entity_type == 'wave':
        return check_wave(db, user, entity_id)
    if entity_type in ('quote', 'calculation', 'document'):
        from app.commerce.models import Calculation, CommercialDocument, Quote
        cls = {'quote': Quote, 'calculation': Calculation, 'document': CommercialDocument}[entity_type]
        row = db.get(cls, entity_id)
        if row is None:
            raise DomainError('NOT_FOUND', 'Объект не найден или недоступен', 404)
        check_request(db, user, row.request_id)
        if entity_type != 'document':
            permission = 'finance.purchase.read' if entity_type == 'quote' else 'finance.calculations.read'
            require_permission(db, user, permission, row.request_id)
        return row
    raise DomainError('ENTITY_TYPE_INVALID', 'Неизвестный тип объекта', 422, 'entity_type')


def check_file(db: Session, user: User, file_id: str) -> FileRecord:
    row = db.get(FileRecord, file_id)
    if row is None:
        raise DomainError('NOT_FOUND', 'Файл не найден или недоступен', 404)
    entity_type, entity_id = next((kind, getattr(row, column)) for kind, column in ENTITY_COLUMNS.items()
                                  if getattr(row, column))
    entity = check_entity(db, user, entity_type, entity_id)
    if permission := FILE_PERMISSIONS.get(row.classification):
        # A financial read grant with own scope must still match the originating request.
        request_id = getattr(entity, 'request_id', None)
        if entity_type == 'request':
            request_id = entity_id
        require_permission(db, user, permission, request_id)
    return row


def file_view(row: FileRecord) -> dict:
    from app.core.service import serialize
    data = serialize(row)
    data.pop('storage_key', None)
    data.pop('scan_result', None)
    return data
