"""Audit log helper. Every state-changing admin action records a row."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from .models import AuditLog


def record(
    db: Session,
    *,
    actor_id: uuid.UUID | None,
    action: str,
    entity: str,
    entity_id: str,
    detail: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity=entity,
            entity_id=str(entity_id),
            detail=detail,
        )
    )
