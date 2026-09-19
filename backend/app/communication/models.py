from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Entity


class Chat(Entity):
    __tablename__ = 'chats'
    __table_args__ = (
        CheckConstraint("kind IN ('direct','group','request','wave')"),
        CheckConstraint("(kind = 'request' AND request_id IS NOT NULL AND wave_id IS NULL) OR "
                        "(kind = 'wave' AND wave_id IS NOT NULL AND request_id IS NULL) OR "
                        "(kind IN ('direct','group') AND request_id IS NULL AND wave_id IS NULL)"),
    )
    title: Mapped[str] = mapped_column(String(250))
    kind: Mapped[str] = mapped_column(String(20))
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    request_id: Mapped[str | None] = mapped_column(ForeignKey('requests.id'), index=True)
    wave_id: Mapped[str | None] = mapped_column(ForeignKey('waves.id'), index=True)
    direct_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    last_sequence: Mapped[int] = mapped_column(Integer, default=0)


class ChatMember(Entity):
    __tablename__ = 'chat_members'
    __table_args__ = (UniqueConstraint('chat_id', 'user_id'), CheckConstraint('read_sequence >= 0'))
    chat_id: Mapped[str] = mapped_column(ForeignKey('chats.id'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    read_sequence: Mapped[int] = mapped_column(Integer, default=0)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Entity):
    __tablename__ = 'messages'
    __table_args__ = (UniqueConstraint('chat_id', 'sequence'), CheckConstraint('sequence > 0'))
    chat_id: Mapped[str] = mapped_column(ForeignKey('chats.id'), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    mention_ids: Mapped[list] = mapped_column(JSON, default=list)
    document_refs: Mapped[list] = mapped_column(JSON, default=list)


class FileRecord(Entity):
    __tablename__ = 'files'
    __table_args__ = (
        CheckConstraint('size > 0'),
        CheckConstraint("classification IN ('general','purchase','calculation','reward','profit')"),
        CheckConstraint("status IN ('quarantined','clean','infected','scan_failed')"),
        CheckConstraint('(CASE WHEN request_id IS NULL THEN 0 ELSE 1 END + '
                        'CASE WHEN client_id IS NULL THEN 0 ELSE 1 END + '
                        'CASE WHEN chat_id IS NULL THEN 0 ELSE 1 END + '
                        'CASE WHEN wave_id IS NULL THEN 0 ELSE 1 END + '
                        'CASE WHEN quote_id IS NULL THEN 0 ELSE 1 END + '
                        'CASE WHEN calculation_id IS NULL THEN 0 ELSE 1 END + '
                        'CASE WHEN document_id IS NULL THEN 0 ELSE 1 END) = 1'),
    )
    name: Mapped[str] = mapped_column(String(250))
    media_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(200), unique=True)
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    classification: Mapped[str] = mapped_column(String(30), default='general')
    status: Mapped[str] = mapped_column(String(30), default='quarantined', index=True)
    scan_result: Mapped[str | None] = mapped_column(String(100))
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_id: Mapped[str | None] = mapped_column(ForeignKey('requests.id'), index=True)
    client_id: Mapped[str | None] = mapped_column(ForeignKey('counterparties.id'), index=True)
    chat_id: Mapped[str | None] = mapped_column(ForeignKey('chats.id'), index=True)
    wave_id: Mapped[str | None] = mapped_column(ForeignKey('waves.id'), index=True)
    quote_id: Mapped[str | None] = mapped_column(ForeignKey('quotes.id'), index=True)
    calculation_id: Mapped[str | None] = mapped_column(ForeignKey('calculations.id'), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey('commercial_documents.id'), index=True)


class MessageFile(Entity):
    __tablename__ = 'message_files'
    __table_args__ = (UniqueConstraint('message_id', 'file_id'),)
    message_id: Mapped[str] = mapped_column(ForeignKey('messages.id'), index=True)
    file_id: Mapped[str] = mapped_column(ForeignKey('files.id'), index=True)
