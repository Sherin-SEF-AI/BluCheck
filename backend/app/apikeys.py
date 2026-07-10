"""API key generation + the X-API-Key auth dependency for the public /v1 endpoints."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import ApiKey

PREFIX = "blu_live_"


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate() -> tuple[str, str, str]:
    """Return (plaintext_key, display_prefix, sha256_hash). Plaintext is shown once."""
    raw = PREFIX + secrets.token_hex(20)
    return raw, raw[: len(PREFIX) + 6], hash_key(raw)


def require_api_key(
    x_api_key: str | None = Header(default=None), db: Session = Depends(get_db)
) -> ApiKey:
    """Authenticate a public API request by its X-API-Key header. Updates last_used_at."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    key = db.execute(
        select(ApiKey).where(ApiKey.key_hash == hash_key(x_api_key), ApiKey.active.is_(True))
    ).scalar_one_or_none()
    if key is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")
    key.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return key
