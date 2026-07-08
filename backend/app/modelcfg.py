"""Active model-version helper. There is at most one active model_version; it holds the
current mode (shadow/assist/auto/disabled) and confidence thresholds, changeable at
runtime without a redeploy. Defaults to shadow so nothing auto-acts on deploy.
"""

from __future__ import annotations

from sqlalchemy import select
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


def ensure_active_model_version(db: Session) -> ModelVersion:
    mv = db.execute(
        select(ModelVersion).where(ModelVersion.active.is_(True))
    ).scalar_one_or_none()
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
    db.commit()
    db.refresh(mv)
    return mv
