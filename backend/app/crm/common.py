"""Checks and persistence shared by the CRM endpoints."""

from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.core.models import User
from app.core.service import audit, serialize
from app.crm.models import Contact, Counterparty


def active_user(db: Session, entity_id: str) -> User:
    row = db.get(User, entity_id)
    if not row or not row.active:
        raise DomainError('ASSIGNEE_INVALID', 'Выберите действующего сотрудника', 422, 'assignee_id')
    return row


def contact_matches(db: Session, contact_id: str | None, client_id: str) -> None:
    if contact_id:
        row = db.get(Contact, contact_id)
        if not row or row.client_id != client_id or row.archived:
            raise DomainError('CONTACT_INVALID', 'Контакт не относится к выбранному клиенту', 422, 'contact_id')


def save(db: Session, user: User, row: Any, kind: str) -> dict[str, Any]:
    db.add(row)
    db.flush()
    result = serialize(row)
    audit(db, user, kind, row.id, 'created', after=result)
    return result


def require_supplier(db: Session, entity_id: str) -> Counterparty:
    row = db.get(Counterparty, entity_id)
    if not row or row.archived or row.kind not in ('supplier', 'both'):
        raise DomainError('SUPPLIER_INVALID', 'Выберите действующего поставщика', 422, 'supplier_id')
    return row
