"""Admin-managed API keys for third-party integrations (JWT-protected management endpoints)."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import apikeys, audit
from ..auth import require_admin
from ..db import get_db
from ..models import ApiKey, User
from ..schemas import ApiKeyCreated, ApiKeyCreateRequest, ApiKeyList, ApiKeyOut

router = APIRouter(prefix="/apikeys", tags=["apikeys"])
logger = logging.getLogger("blucheck.apikeys")


@router.post("", response_model=ApiKeyCreated, status_code=201)
def create_key(
    body: ApiKeyCreateRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> ApiKeyCreated:
    """Mint a new API key. The full key is returned ONCE and never stored in plaintext."""
    raw, prefix, key_hash = apikeys.generate()
    key = ApiKey(name=body.name.strip()[:120], key_prefix=prefix, key_hash=key_hash, created_by=admin.id)
    db.add(key)
    db.commit()
    db.refresh(key)
    audit.record(
        db, actor_id=admin.id, action="apikey_create", entity="api_key",
        entity_id=str(key.id), detail={"name": key.name, "prefix": prefix},
    )
    db.commit()
    logger.info("api key created id=%s by=%s", key.id, admin.id)
    return ApiKeyCreated(id=str(key.id), name=key.name, key=raw, key_prefix=prefix, created_at=key.created_at)


@router.get("", response_model=ApiKeyList)
def list_keys(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> ApiKeyList:
    rows = db.execute(select(ApiKey).order_by(ApiKey.created_at.desc())).scalars().all()
    return ApiKeyList(keys=[
        ApiKeyOut(id=str(k.id), name=k.name, key_prefix=k.key_prefix, active=k.active,
                  created_at=k.created_at, last_used_at=k.last_used_at)
        for k in rows
    ])


@router.delete("/{key_id}")
def revoke_key(key_id: uuid.UUID, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    key = db.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found")
    key.active = False
    db.commit()
    audit.record(
        db, actor_id=admin.id, action="apikey_revoke", entity="api_key",
        entity_id=str(key_id), detail={"name": key.name},
    )
    db.commit()
    return {"ok": True, "revoked": str(key_id)}
