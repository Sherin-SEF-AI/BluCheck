"""Admin-only user management and audit-log access."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit
from ..auth import hash_password, require_admin
from ..db import get_db
from ..models import AuditLog, User
from ..schemas import AuditListResponse, UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=list[UserOut])
def list_users(
    role: str | None = None,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[User]:
    stmt = select(User).order_by(User.created_at.desc())
    if role:
        stmt = stmt.where(User.role == role)
    return list(db.execute(stmt).scalars())


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> User:
    user = User(
        name=body.name,
        email=body.email.lower(),
        role=body.role,
        password_hash=hash_password(body.password),
        active=True,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A user with that email already exists")
    audit.record(db, actor_id=admin.id, action="create_user", entity="user", entity_id=str(user.id), detail={"email": user.email, "role": user.role})
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if body.name is not None:
        user.name = body.name
    if body.active is not None:
        # Prevent an admin from deactivating themselves and getting locked out.
        if not body.active and user.id == admin.id:
            raise HTTPException(status_code=400, detail="You cannot deactivate your own account")
        user.active = body.active
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    audit.record(db, actor_id=admin.id, action="update_user", entity="user", entity_id=str(user_id), detail={"fields": [k for k, v in body.model_dump().items() if v is not None]})
    db.commit()
    db.refresh(user)
    return user


@router.get("/audit", response_model=AuditListResponse)
def list_audit(
    entity: str | None = None,
    action: str | None = None,
    actor_id: uuid.UUID | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AuditListResponse:
    stmt = select(AuditLog)
    if entity:
        stmt = stmt.where(AuditLog.entity == entity)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == actor_id)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = list(
        db.execute(
            stmt.order_by(AuditLog.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars()
    )
    return AuditListResponse(items=rows, total=total, page=page, page_size=page_size)
