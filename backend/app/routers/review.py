"""Admin review: approve or reject an inspection. Rejections carry structured
zone/issue labels (the ground-truth dataset for the intelligence layer). Every action
is audited, and the review records whether it confirmed or overrode a model verdict.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import audit, review_ai
from ..auth import require_admin
from ..db import get_db
from ..models import (
    ISSUE_KEYS,
    ZONE_KEYS,
    Inspection,
    Review,
    ReviewZoneLabel,
    ScoringResult,
    User,
)
from ..schemas import ReviewRephraseRequest, ReviewRephraseResponse, ReviewRequest, ZoneIssueLabel

router = APIRouter(prefix="/inspections", tags=["review"])
logger = logging.getLogger("blucheck.review")

REVIEWABLE = {"pending", "approved", "rejected"}


@router.post("/review-rephrase", response_model=ReviewRephraseResponse)
def review_rephrase(
    body: ReviewRephraseRequest, _admin: User = Depends(require_admin)
) -> ReviewRephraseResponse:
    """Agentic review helper: turn a reviewer's rough free-text note into a clear, driver-facing
    rejection reason plus structured (zone, issue) labels. Preview only -- the reviewer confirms
    with Approve/Reject. Does not touch the inspection."""
    out = review_ai.rephrase(body.text, body.context)
    if out is None:
        raise HTTPException(status_code=502, detail="Could not rephrase right now; try again.")
    return ReviewRephraseResponse(
        reason=out["reason"],
        labels=[ZoneIssueLabel(zone_key=l["zone_key"], issue_key=l["issue_key"]) for l in out["labels"]],
    )


def _summarize(labels) -> str:
    # Human-readable summary of structured labels, e.g. "seats: stain; floor_mats: trash".
    return "; ".join(f"{l.zone_key}: {l.issue_key}" for l in labels)


@router.post("/{inspection_id}/review", status_code=status.HTTP_200_OK)
def review_inspection(
    inspection_id: uuid.UUID,
    body: ReviewRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    inspection = db.get(Inspection, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Inspection not found")
    if inspection.status not in REVIEWABLE:
        raise HTTPException(
            status_code=409, detail=f"Inspection is {inspection.status}; not ready for review"
        )

    if body.action == "reject":
        # Prefer structured labels (the gold dataset), but allow a reason-only reject so an agent
        # recommendation with no specific zone (content-gate / low-overall) can still be confirmed.
        if not body.labels and not (body.reason and body.reason.strip()):
            raise HTTPException(
                status_code=422,
                detail="Rejection requires at least one zone/issue label or a reason",
            )
        for lbl in body.labels:
            if lbl.zone_key not in ZONE_KEYS:
                raise HTTPException(status_code=422, detail=f"Unknown zone_key: {lbl.zone_key}")
            if lbl.issue_key not in ISSUE_KEYS:
                raise HTTPException(status_code=422, detail=f"Unknown issue_key: {lbl.issue_key}")

    # Determine review source: did the admin confirm or override a model verdict?
    source = "human"
    if body.scoring_result_id is not None:
        scoring = db.get(ScoringResult, body.scoring_result_id)
        if scoring is not None and scoring.inspection_id == inspection_id:
            model_action = (
                "approve" if scoring.decision == "auto_approve"
                else "reject" if scoring.decision == "auto_reject"
                else None
            )
            if model_action is not None:
                source = "model_confirmed" if model_action == body.action else "model_overridden"

    new_status = "approved" if body.action == "approve" else "rejected"
    now = datetime.now(timezone.utc)

    inspection.status = new_status
    inspection.reviewed_by = admin.id
    inspection.reviewed_at = now
    if body.action == "reject":
        summary = _summarize(body.labels)
        inspection.reject_reason = f"{summary}{f' - {body.reason}' if body.reason else ''}"
    else:
        inspection.reject_reason = None

    review = Review(
        inspection_id=inspection_id,
        admin_id=admin.id,
        action=body.action,
        reason=body.reason,
        source=source,
        scoring_result_id=body.scoring_result_id,
        viewed_frame_ids=[str(f) for f in body.viewed_frame_ids] or None,
    )
    db.add(review)
    db.flush()  # get review.id for the labels

    for lbl in body.labels:
        db.add(ReviewZoneLabel(review_id=review.id, zone_key=lbl.zone_key, issue_key=lbl.issue_key))

    audit.record(
        db,
        actor_id=admin.id,
        action=f"review_{body.action}",
        entity="inspection",
        entity_id=str(inspection_id),
        detail={
            "new_status": new_status,
            "source": source,
            "labels": [{"zone_key": l.zone_key, "issue_key": l.issue_key} for l in body.labels],
            "note": body.reason,
        },
    )
    db.commit()
    logger.info(
        "inspection_reviewed id=%s action=%s source=%s admin=%s",
        inspection_id,
        body.action,
        source,
        admin.id,
    )

    # Notify the driver (push) that their inspection was approved/rejected.
    from .. import push
    from ..models import User as UserModel, Vehicle

    driver = db.get(UserModel, inspection.driver_id)
    vehicle = db.get(Vehicle, inspection.vehicle_id)
    plate = vehicle.registration_plate if vehicle else "your vehicle"
    if new_status == "approved":
        title, msg = "Inspection approved", f"{plate} passed the cleanliness check."
    else:
        reasons = _summarize(body.labels) or "see the app for details"
        title, msg = "Inspection rejected", f"{plate}: re-clean {reasons}."
    push.send_to_driver(db, driver, title, msg, {"inspection_id": str(inspection_id), "status": new_status})

    return {"id": str(inspection_id), "status": new_status, "source": source, "reviewed_at": now.isoformat()}
