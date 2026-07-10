"""Vision-model health + alerting.

The scoring pipeline depends on a Groq vision model. When that model is blocked at the org level,
rate-limited, or erroring (exactly the scout-403 case), scoring silently fails and inspections
pile up unscored. This module records those incidents (as audit rows, no new table) and derives a
health status so the dashboard, the assistant, and self-healing can react instead of failing
quietly.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from . import audit
from .models import AuditLog, ScoringResult

logger = logging.getLogger("blucheck.health")
INCIDENT_ACTION = "vision_incident"
DOWN_WINDOW_MIN = 20  # an incident newer than the last success within this window => vision down


def record_incident(db: Session, source: str, model: str | None, message: str) -> None:
    """Record a vision-model failure. Best-effort; never raises."""
    try:
        audit.record(
            db, actor_id=None, action=INCIDENT_ACTION, entity="vision_model",
            entity_id=(model or "unknown")[:64],
            detail={"source": source, "model": model, "message": (message or "")[:400]},
        )
        db.commit()
        logger.warning("vision incident source=%s model=%s: %s", source, model, (message or "")[:200])
    except Exception:  # noqa: BLE001
        logger.exception("failed to record vision incident")


def status(db: Session) -> dict:
    """Derive current vision health from recent incidents vs recent successful scores."""
    now = datetime.now(timezone.utc)
    last_incident = db.execute(
        select(AuditLog).where(AuditLog.action == INCIDENT_ACTION).order_by(desc(AuditLog.created_at)).limit(1)
    ).scalar_one_or_none()
    last_success = db.execute(
        select(ScoringResult.created_at).where(ScoringResult.overall_score.isnot(None))
        .order_by(desc(ScoringResult.created_at)).limit(1)
    ).scalar_one_or_none()

    inc_at = last_incident.created_at if last_incident else None
    if inc_at is not None and inc_at.tzinfo is None:
        inc_at = inc_at.replace(tzinfo=timezone.utc)
    succ_at = last_success
    if succ_at is not None and succ_at.tzinfo is None:
        succ_at = succ_at.replace(tzinfo=timezone.utc)

    # Vision is considered DOWN if the most recent incident is recent AND no successful score has
    # happened since it (a success after the incident means it recovered).
    vision_ok = True
    if inc_at is not None and (now - inc_at) < timedelta(hours=6):
        recovered = succ_at is not None and succ_at > inc_at
        if not recovered and (now - inc_at) < timedelta(minutes=DOWN_WINDOW_MIN * 100):
            vision_ok = recovered

    detail = (last_incident.detail or {}) if last_incident else {}
    return {
        "vision_ok": vision_ok,
        "last_incident_at": inc_at.isoformat() if inc_at else None,
        "last_incident_model": detail.get("model"),
        "last_incident_message": detail.get("message"),
        "last_incident_source": detail.get("source"),
        "last_success_at": succ_at.isoformat() if succ_at else None,
    }
