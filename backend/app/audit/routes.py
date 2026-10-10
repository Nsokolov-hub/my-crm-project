from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.access import finance_rights, redact, request_ids_of
from app.core.db import get_db
from app.core.models import AuditEvent, User
from app.core.security import current_user, require_permission
from app.core.service import page as paginate

router = APIRouter(tags=["Доступ и настройки"])


@router.get("/admin/audit")
def list_audit(
    entity_id: str | None = None,
    page: int = 1,
    page_size: int = 25,
    user: User = Depends(current_user),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    require_permission(db, user, "audit.read")
    stmt = select(AuditEvent)
    if entity_id:
        stmt = stmt.where(AuditEvent.entity_id == entity_id)
    result = paginate(db, stmt.order_by(AuditEvent.created_at.desc()), page, page_size)
    owners = request_ids_of(db, result["items"])
    rights = finance_rights(db, user, owners.values())
    result["items"] = [redact(db, user, row, owners[row["id"]], rights) for row in result["items"]]
    return result
