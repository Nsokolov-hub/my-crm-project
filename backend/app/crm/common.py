"""Checks and persistence shared by the CRM endpoints."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.core.models import User
from app.core.service import audit, serialize
from app.crm.models import Contact, Counterparty


def party_root(db: Session, counterparty_id: str) -> str:
    """The card that stands for a counterparty now: a merged duplicate stands for its main card."""
    seen = {counterparty_id}
    while target := db.scalar(select(Counterparty.merged_into_id).where(Counterparty.id == counterparty_id)):
        if target in seen:
            break
        seen.add(target)
        counterparty_id = target
    return counterparty_id


def party_ids(db: Session, counterparty_id: str) -> set[str]:
    """A counterparty and every duplicate merged into it."""
    ids, frontier = {counterparty_id}, {counterparty_id}
    while frontier:
        frontier = set(db.scalars(select(Counterparty.id).where(Counterparty.merged_into_id.in_(frontier)))) - ids
        ids |= frontier
    return ids


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
