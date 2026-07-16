"""Active model-version helper. There is at most one active model_version; it holds the
current mode (shadow/assist/auto/disabled) and confidence thresholds, changeable at
runtime without a redeploy. Defaults to shadow so nothing auto-acts on deploy.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import ModelVersion

DEFAULT_THRESHOLDS = {
    # In auto mode: overall_score >= auto_approve approves; <= auto_reject rejects;
    # anything in between routes to a human.
    "overall": {"auto_approve": 85, "auto_reject": 40},
    "per_zone": {},
}
DEFAULT_VLM = "Qwen/Qwen3-VL-30B-A3B-Instruct-FP8"
DEFAULT_PROMPT_VERSION = "v1"


def _active(db: Session) -> ModelVersion | None:
    # first() (not scalar_one_or_none) so a legacy pre-index duplicate can't 500 the whole
    # platform with MultipleResultsFound; the partial-unique index prevents new duplicates.
    return db.execute(
        select(ModelVersion)
        .where(ModelVersion.active.is_(True))
        .order_by(ModelVersion.created_at.asc())
    ).scalars().first()


def ensure_active_model_version(db: Session) -> ModelVersion:
    mv = _active(db)
    if mv is not None:
        return mv
    mv = ModelVersion(
        name="qwen3vl-shadow",
        vlm_model=DEFAULT_VLM,
        prompt_version=DEFAULT_PROMPT_VERSION,
        thresholds=DEFAULT_THRESHOLDS,
        mode="shadow",  # safe default: observe only
        active=True,
    )
    db.add(mv)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent first-request created the active row first; the partial-unique index
        # rejected ours. Roll back and return the winner instead of crashing.
        db.rollback()
        existing = _active(db)
        if existing is None:  # pragma: no cover - lost the row between rollback and re-read
            raise
        return existing
    db.refresh(mv)
    return mv
