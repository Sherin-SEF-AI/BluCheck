"""Self-healing pipeline agent.

Periodically scans for inspections that got stuck or never finished, and quietly recovers them so
no human has to notice:

  - failed                         -> reset failed captures and re-enqueue extraction
  - stuck in uploading/processing  -> re-enqueue (a lost SQS message / crashed worker mid-run)
  - pending but never scored       -> re-enqueue so the worker runs the scoring stage

Every heal is bounded per inspection (a small attempt budget with a cooldown, tracked in
device_meta._heal) so a genuinely broken inspection can never cause an infinite reprocess loop.
Runs in a background loop inside the API and can also be triggered manually.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit, storage
from .models import Capture, Inspection, ScoringResult

logger = logging.getLogger("blucheck.self_heal")

STUCK_MINUTES = 15      # uploading/processing older than this is considered stuck
UNSCORED_MINUTES = 10   # pending with no score older than this needs a scoring nudge
MAX_ATTEMPTS = 3        # per-inspection heal budget
COOLDOWN_MINUTES = 30   # min gap between heals of the same inspection
BATCH = 100             # cap work per sweep


def _heal_state(inspection: Inspection) -> dict:
    dm = inspection.device_meta if isinstance(inspection.device_meta, dict) else {}
    h = dm.get("_heal")
    return h if isinstance(h, dict) else {}


def _can_heal(inspection: Inspection, now: datetime) -> bool:
    h = _heal_state(inspection)
    if int(h.get("count", 0)) >= MAX_ATTEMPTS:
        return False
    last = h.get("last")
    if last:
        try:
            if now - datetime.fromisoformat(last) < timedelta(minutes=COOLDOWN_MINUTES):
                return False
        except (TypeError, ValueError):
            pass
    return True


def _mark_healed(inspection: Inspection, now: datetime, kind: str) -> None:
    dm = dict(inspection.device_meta) if isinstance(inspection.device_meta, dict) else {}
    h = dm.get("_heal") if isinstance(dm.get("_heal"), dict) else {}
    dm["_heal"] = {"count": int(h.get("count", 0)) + 1, "last": now.isoformat(), "kind": kind}
    inspection.device_meta = dm  # reassign so SQLAlchemy tracks the JSONB change


def run(db: Session) -> dict:
    """One self-healing sweep. Returns counts of what it recovered and what it skipped (budget)."""
    now = datetime.now(timezone.utc)
    counts = {"failed": 0, "stuck": 0, "unscored": 0, "skipped": 0}

    def _reprocess(inspection: Inspection, kind: str, reset_captures: bool) -> None:
        if reset_captures:
            caps = db.execute(
                select(Capture).where(Capture.inspection_id == inspection.id)
            ).scalars()
            for cap in caps:
                if cap.status == "failed":
                    cap.status = "uploaded"
            if inspection.status in ("failed", "uploading"):
                inspection.status = "processing"
        _mark_healed(inspection, now, kind)
        db.commit()
        storage.enqueue_extraction(str(inspection.id))
        audit.record(
            db, actor_id=None, action="self_heal", entity="inspection",
            entity_id=str(inspection.id), detail={"kind": kind},
        )
        db.commit()
        counts[kind] += 1
        logger.info("self_heal id=%s kind=%s", inspection.id, kind)

    # 1) Failed inspections.
    failed = db.execute(
        select(Inspection).where(Inspection.status == "failed").limit(BATCH)
    ).scalars().all()
    for insp in failed:
        if _can_heal(insp, now):
            _reprocess(insp, "failed", reset_captures=True)
        else:
            counts["skipped"] += 1

    # 2) Stuck in uploading/processing past the threshold.
    stuck_before = now - timedelta(minutes=STUCK_MINUTES)
    stuck = db.execute(
        select(Inspection)
        .where(Inspection.status.in_(("uploading", "processing")), Inspection.created_at < stuck_before)
        .limit(BATCH)
    ).scalars().all()
    for insp in stuck:
        if _can_heal(insp, now):
            _reprocess(insp, "stuck", reset_captures=True)
        else:
            counts["skipped"] += 1

    # 3) Pending but never scored (extraction finished, scoring stage never ran).
    unscored_before = now - timedelta(minutes=UNSCORED_MINUTES)
    pending = db.execute(
        select(Inspection)
        .where(Inspection.status == "pending", Inspection.created_at < unscored_before)
        .limit(BATCH)
    ).scalars().all()
    for insp in pending:
        has_score = db.execute(
            select(ScoringResult.id).where(ScoringResult.inspection_id == insp.id).limit(1)
        ).first()
        if has_score:
            continue
        if _can_heal(insp, now):
            _reprocess(insp, "unscored", reset_captures=False)
        else:
            counts["skipped"] += 1

    if any(counts[k] for k in ("failed", "stuck", "unscored")):
        logger.info("self_heal sweep: %s", counts)
    return counts
