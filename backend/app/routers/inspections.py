"""Inspection creation (driver), listing and detail (admin), frame URLs, and delete."""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, joinedload

from .. import agent, audit, storage
from ..auth import get_current_user, require_admin
from ..db import get_db
from ..models import (
    Capture,
    Frame,
    Inspection,
    ModelVersion,
    Review,
    ReviewZoneLabel,
    ScoringResult,
    User,
    Vehicle,
    ZoneScore,
)
from ..schemas import (
    CaptureDetail,
    FrameOut,
    FrameUrlResponse,
    InspectionCreate,
    InspectionCreated,
    InspectionDetail,
    InspectionListItem,
    InspectionListResponse,
    PlateVerifyRequest,
    ZoneIssueLabel,
)

router = APIRouter(prefix="/inspections", tags=["inspections"])
logger = logging.getLogger("blucheck.inspections")


@router.post("/verify-plate")
def verify_plate(
    body: PlateVerifyRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """OCR the plate photo and correlate to the driver's registered car (soft integrity)."""
    import base64

    from .. import plateocr
    from ..schemas import PlateVerifyResponse

    if user.role != "driver" or not user.car_number:
        raise HTTPException(status_code=400, detail="Only registered drivers can verify plates")
    try:
        img = base64.b64decode(body.image_b64)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid image data")
    res = plateocr.read_plate(img, user.car_number)
    return PlateVerifyResponse(
        read_plate=res["read_plate"],
        matched=res["matched"],
        expected=user.car_number,
        candidates=res["candidates"],
    )


@router.post("/precheck")
def precheck(
    body: PlateVerifyRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """One-shot capture pre-check run automatically by the app before recording: (1) is this a
    vehicle at all (so non-car footage is rejected on the phone), and (2) read the plate and
    correlate to the driver's registered car -- no separate 'scan plate' tap needed."""
    import base64 as _b64

    from .. import plateocr

    if user.role != "driver" or not user.car_number:
        raise HTTPException(status_code=400, detail="Only registered drivers can pre-check")
    try:
        img = _b64.b64decode(body.image_b64)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid image data")
    veh = plateocr.detect_vehicle(img)
    plate = plateocr.read_plate(img, user.car_number)
    return {
        "is_vehicle": veh["is_vehicle"],
        "vehicle_confidence": veh["confidence"],
        "labels": veh["labels"],
        "read_plate": plate["read_plate"],
        "matched": plate["matched"],
        "expected": user.car_number,
    }


@router.post("", response_model=InspectionCreated, status_code=status.HTTP_201_CREATED)
def create_inspection(
    body: InspectionCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InspectionCreated:
    if user.role != "driver":
        raise HTTPException(status_code=403, detail="Only drivers create inspections")

    vehicle = db.get(Vehicle, body.vehicle_id)
    if vehicle is None or not vehicle.active:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    inspection = Inspection(
        vehicle_id=body.vehicle_id,
        driver_id=user.id,
        status="uploading",
        gps_lat=body.gps.lat,
        gps_lon=body.gps.lon,
        gps_accuracy_m=body.gps.accuracy_m,
        captured_at_utc=body.captured_at_utc,
        captured_at_local=body.captured_at_local,
        device_meta=body.device_meta,
        ocr_plate=body.ocr_plate,
        ocr_matched=body.ocr_matched,
        reinspection_of=body.reinspection_of,
    )
    db.add(inspection)
    db.commit()
    db.refresh(inspection)
    logger.info(
        "inspection_created id=%s driver=%s vehicle=%s reinspection_of=%s",
        inspection.id, user.id, vehicle.id, body.reinspection_of,
    )
    return InspectionCreated(inspection_id=inspection.id, status=inspection.status)


def _authorize_view(inspection: Inspection, user: User) -> None:
    if user.role == "admin":
        return
    if user.role == "driver" and inspection.driver_id == user.id:
        return
    raise HTTPException(status_code=403, detail="Not permitted")


@router.get("", response_model=InspectionListResponse)
def list_inspections(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    status_filter: str | None = Query(default=None, alias="status"),
    vehicle_id: uuid.UUID | None = None,
    driver_id: uuid.UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    order: str | None = None,  # "uncertainty" orders by model uncertainty (active learning)
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
) -> InspectionListResponse:
    stmt = (
        select(Inspection, Vehicle.registration_plate, User.name)
        .join(Vehicle, Vehicle.id == Inspection.vehicle_id)
        .join(User, User.id == Inspection.driver_id)
    )

    # Drivers only see their own history; admins see everything.
    if user.role == "driver":
        stmt = stmt.where(Inspection.driver_id == user.id)
    elif driver_id is not None:
        stmt = stmt.where(Inspection.driver_id == driver_id)

    if status_filter:
        stmt = stmt.where(Inspection.status == status_filter)
    if vehicle_id is not None:
        stmt = stmt.where(Inspection.vehicle_id == vehicle_id)
    if date_from is not None:
        stmt = stmt.where(Inspection.created_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(Inspection.created_at <= date_to)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.execute(count_stmt).scalar_one()

    # Latest overall score per inspection, for the "decided by / score" columns.
    score_sq = (
        select(
            ScoringResult.inspection_id.label("iid"),
            func.max(ScoringResult.created_at).label("mx"),
        )
        .group_by(ScoringResult.inspection_id)
        .subquery()
    )
    latest_score = (
        select(ScoringResult.inspection_id, ScoringResult.overall_score)
        .join(score_sq, (ScoringResult.inspection_id == score_sq.c.iid) & (ScoringResult.created_at == score_sq.c.mx))
        .subquery()
    )
    stmt = stmt.add_columns(latest_score.c.overall_score).outerjoin(
        latest_score, latest_score.c.inspection_id == Inspection.id
    )

    if order == "uncertainty":
        # Active learning: least-confident model verdicts first (nulls last), then newest.
        sq = (
            select(
                ScoringResult.inspection_id,
                func.max(ScoringResult.overall_confidence).label("conf"),
            )
            .group_by(ScoringResult.inspection_id)
            .subquery()
        )
        stmt = stmt.outerjoin(sq, sq.c.inspection_id == Inspection.id).order_by(
            sq.c.conf.asc().nulls_last(), Inspection.created_at.desc()
        )
    else:
        stmt = stmt.order_by(Inspection.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = db.execute(stmt).all()

    items = [
        InspectionListItem(
            id=insp.id,
            status=insp.status,
            vehicle_plate=plate,
            driver_name=driver_name,
            gps_lat=insp.gps_lat,
            gps_lon=insp.gps_lon,
            captured_at_utc=insp.captured_at_utc,
            created_at=insp.created_at,
            decision_source=agent.decision_source(insp),
            overall_score=overall_score,
        )
        for insp, plate, driver_name, overall_score in rows
    ]
    return InspectionListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{inspection_id}", response_model=InspectionDetail)
def get_inspection(
    inspection_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InspectionDetail:
    inspection = db.execute(
        select(Inspection)
        .where(Inspection.id == inspection_id)
        .options(joinedload(Inspection.captures).joinedload(Capture.frames))
    ).unique().scalar_one_or_none()
    if inspection is None:
        raise HTTPException(status_code=404, detail="Inspection not found")
    _authorize_view(inspection, user)

    vehicle = db.get(Vehicle, inspection.vehicle_id)
    driver = db.get(User, inspection.driver_id)

    captures: list[CaptureDetail] = []
    for cap in sorted(inspection.captures, key=lambda c: c.kind):
        frames = [
            FrameOut(
                id=f.id,
                seq=f.seq,
                offset_ms=f.offset_ms,
                absolute_ts_utc=f.absolute_ts_utc,
                gps_lat=f.gps_lat,
                gps_lon=f.gps_lon,
                thumb_url=storage.presign_get(f.s3_key_thumb),
                full_url_endpoint=f"/inspections/{inspection.id}/frames/{f.id}/url",
                width=f.width,
                height=f.height,
                selected=f.selected,
                blur_score=f.blur_score,
            )
            # Selected frames first, then by sequence.
            for f in sorted(cap.frames, key=lambda x: (not x.selected, x.seq))
        ]
        captures.append(
            CaptureDetail(
                id=cap.id,
                kind=cap.kind,
                status=cap.status,
                duration_s=cap.duration_s,
                recorded_at_utc=cap.recorded_at_utc,
                gps_lat=cap.gps_lat,
                gps_lon=cap.gps_lon,
                resolution=cap.resolution,
                frame_count=cap.frame_count,
                frames=frames,
            )
        )

    # Structured reject labels from the most recent reject review (drives driver reasons).
    reject_labels: list[ZoneIssueLabel] = []
    last_reject = db.execute(
        select(Review)
        .where(Review.inspection_id == inspection_id, Review.action == "reject")
        .order_by(Review.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if last_reject is not None:
        labels = db.execute(
            select(ReviewZoneLabel).where(ReviewZoneLabel.review_id == last_reject.id)
        ).scalars()
        reject_labels = [
            ZoneIssueLabel(zone_key=l.zone_key, issue_key=l.issue_key) for l in labels
        ]

    # Latest model scoring result (if any) with its zone breakdown.
    scoring = None
    sr = db.execute(
        select(ScoringResult)
        .where(ScoringResult.inspection_id == inspection_id)
        .order_by(ScoringResult.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if sr is not None:
        zrows = db.execute(
            select(ZoneScore).where(ZoneScore.scoring_result_id == sr.id)
        ).scalars()
        mv = db.get(ModelVersion, sr.model_version_id)
        raw = sr.raw_json if isinstance(sr.raw_json, dict) else {}
        reasoning = (raw.get("result") or {}).get("reasoning") if isinstance(raw.get("result"), dict) else None
        scoring = {
            "id": str(sr.id),
            "model_version_id": str(sr.model_version_id),
            "model_name": mv.vlm_model if mv else None,
            "overall_score": sr.overall_score,
            "overall_confidence": sr.overall_confidence,
            "decision": sr.decision,
            "reasoning": reasoning,
            "created_at": sr.created_at.isoformat(),
            "zones": [
                {
                    "zone_key": z.zone_key,
                    "score": z.score,
                    "confidence": z.confidence,
                    "issues": z.issues or [],
                }
                for z in zrows
            ],
        }

    # If auto-rejected by the model and no human labels exist, surface the model's issues
    # as the driver-facing reasons so the driver still knows what to re-clean.
    if not reject_labels and scoring and scoring.get("decision") == "auto_reject":
        derived: list[ZoneIssueLabel] = []
        for z in scoring.get("zones", []):
            for iss in z.get("issues") or []:
                derived.append(ZoneIssueLabel(zone_key=z["zone_key"], issue_key=iss.get("issue_key", "")))
        reject_labels = [d for d in derived if d.issue_key]

    return InspectionDetail(
        id=inspection.id,
        status=inspection.status,
        vehicle_id=inspection.vehicle_id,
        vehicle_plate=vehicle.registration_plate if vehicle else "",
        driver_id=inspection.driver_id,
        driver_name=driver.name if driver else "",
        gps_lat=inspection.gps_lat,
        gps_lon=inspection.gps_lon,
        gps_accuracy_m=inspection.gps_accuracy_m,
        captured_at_utc=inspection.captured_at_utc,
        captured_at_local=inspection.captured_at_local,
        device_meta=inspection.device_meta,
        reviewed_by=inspection.reviewed_by,
        reviewed_at=inspection.reviewed_at,
        reject_reason=inspection.reject_reason,
        reject_labels=reject_labels,
        scoring=scoring,
        decision_source=agent.decision_source(inspection),
        ocr_plate=inspection.ocr_plate,
        ocr_matched=inspection.ocr_matched,
        reinspection_of=inspection.reinspection_of,
        reinspection_of_reason=(
            db.get(Inspection, inspection.reinspection_of).reject_reason
            if inspection.reinspection_of
            else None
        ),
        created_at=inspection.created_at,
        captures=captures,
    )


@router.post("/{inspection_id}/reprocess")
def reprocess_inspection(
    inspection_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Admin recovery for failed or stuck inspections: reset failed captures and re-enqueue
    the inspection for extraction (and, once frames are ready, scoring)."""
    inspection = db.get(Inspection, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Inspection not found")

    captures = db.execute(
        select(Capture).where(Capture.inspection_id == inspection_id)
    ).scalars()
    reset = 0
    for cap in captures:
        if cap.status == "failed":
            cap.status = "uploaded"  # let the worker re-extract it
            reset += 1
    if inspection.status in ("failed", "processing", "pending"):
        inspection.status = "processing"
    db.commit()

    storage.enqueue_extraction(str(inspection_id))
    audit.record(
        db,
        actor_id=admin.id,
        action="inspection_reprocess",
        entity="inspection",
        entity_id=str(inspection_id),
        detail={"captures_reset": reset},
    )
    db.commit()
    logger.info("inspection_reprocess id=%s captures_reset=%s by=%s", inspection_id, reset, admin.id)
    return {"ok": True, "status": inspection.status, "captures_reset": reset}


@router.post("/{inspection_id}/rerun-analysis")
def rerun_analysis(
    inspection_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Re-run the whole intelligence pipeline on an already-analysed inspection: clear its
    stored score, reset it to pending, and re-enqueue so the worker re-scores it (Groq Scout)
    and the supervisor agent decides again. Frames are kept (no re-extraction)."""
    inspection = db.get(Inspection, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Inspection not found")
    # Guard: only re-run once frames exist. Re-running mid-upload/extract would race the worker.
    if inspection.status in ("uploading", "processing"):
        raise HTTPException(status_code=409, detail="Inspection is still processing; cannot re-run yet")

    sr_ids = list(
        db.execute(select(ScoringResult.id).where(ScoringResult.inspection_id == inspection_id)).scalars()
    )
    if sr_ids:
        # Detach any human reviews from these results, then delete (ZoneScores cascade).
        db.execute(update(Review).where(Review.scoring_result_id.in_(sr_ids)).values(scoring_result_id=None))
        db.execute(delete(ScoringResult).where(ScoringResult.id.in_(sr_ids)))

    inspection.status = "pending"
    inspection.reviewed_at = None
    inspection.reviewed_by = None
    inspection.reject_reason = None
    db.commit()

    storage.enqueue_extraction(str(inspection_id))
    audit.record(
        db, actor_id=admin.id, action="inspection_rerun_analysis", entity="inspection",
        entity_id=str(inspection_id), detail={"scoring_cleared": len(sr_ids)},
    )
    db.commit()
    logger.info("inspection_rerun_analysis id=%s cleared=%s by=%s", inspection_id, len(sr_ids), admin.id)
    return {"ok": True, "status": "pending", "scoring_cleared": len(sr_ids)}


@router.post("/{inspection_id}/appeal")
def appeal(
    inspection_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Driver disputes an automated rejection. Re-opens the inspection for a human reviewer
    (status -> pending, agent decision cleared) and records the appeal. The dispute outcome the
    human reaches becomes a gold training label. Only the owning driver may appeal, and only a
    rejected inspection. Because the auto gate fails safe to human without a calibration curve,
    a re-opened inspection is not silently re-rejected by the agent."""
    inspection = db.get(Inspection, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Inspection not found")
    if inspection.driver_id != user.id:
        raise HTTPException(status_code=403, detail="Not your inspection")
    if inspection.status != "rejected":
        raise HTTPException(status_code=409, detail="Only a rejected inspection can be appealed")

    prior_reason = inspection.reject_reason
    prior_source = agent.decision_source(inspection)
    inspection.status = "pending"
    inspection.reviewed_at = None
    inspection.reviewed_by = None
    db.commit()
    audit.record(
        db, actor_id=user.id, action="driver_appeal", entity="inspection",
        entity_id=str(inspection_id),
        detail={"prior_reject_reason": prior_reason, "prior_decided_by": prior_source, "by": "driver"},
    )
    db.commit()
    logger.info("driver_appeal id=%s driver=%s prior_source=%s", inspection_id, user.id, prior_source)
    return {"ok": True, "status": "pending"}


@router.get("/{inspection_id}/frames/{frame_id}/url", response_model=FrameUrlResponse)
def get_frame_url(
    inspection_id: uuid.UUID,
    frame_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FrameUrlResponse:
    inspection = db.get(Inspection, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Inspection not found")
    _authorize_view(inspection, user)

    frame = db.execute(
        select(Frame)
        .join(Capture, Capture.id == Frame.capture_id)
        .where(Frame.id == frame_id, Capture.inspection_id == inspection_id)
    ).scalar_one_or_none()
    if frame is None:
        raise HTTPException(status_code=404, detail="Frame not found")

    from ..config import get_settings

    ttl = get_settings().frame_url_ttl
    return FrameUrlResponse(url=storage.presign_get(frame.s3_key_full, ttl), expires_in=ttl)


@router.get("/{inspection_id}/frames/{frame_id}/annotated")
def annotated_frame(
    inspection_id: uuid.UUID,
    frame_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Full-res frame with detected cleanliness issues boxed and labelled (on-demand VLM)."""
    from fastapi import Response

    from .. import grounding

    inspection = db.get(Inspection, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Inspection not found")
    _authorize_view(inspection, user)

    frame = db.execute(
        select(Frame)
        .join(Capture, Capture.id == Frame.capture_id)
        .where(Frame.id == frame_id, Capture.inspection_id == inspection_id)
    ).scalar_one_or_none()
    if frame is None:
        raise HTTPException(status_code=404, detail="Frame not found")

    try:
        jpeg = grounding.annotate_frame(frame.s3_key_full)
    except grounding.GroundingError as err:
        raise HTTPException(status_code=503, detail=f"Issue detection unavailable: {err}")
    return Response(content=jpeg, media_type="image/jpeg")


@router.delete("/{inspection_id}")
def delete_inspection(
    inspection_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Admin-only DPDP delete: removes DB rows and all S3 media for the inspection."""
    inspection = db.get(Inspection, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Inspection not found")

    for prefix in (
        f"raw/{inspection_id}/",
        f"frames/{inspection_id}/",
        f"thumbs/{inspection_id}/",
    ):
        storage.delete_prefix(prefix)

    audit.record(
        db,
        actor_id=admin.id,
        action="delete_inspection",
        entity="inspection",
        entity_id=str(inspection_id),
        detail={"vehicle_id": str(inspection.vehicle_id)},
    )
    db.delete(inspection)
    db.commit()
    logger.info("inspection_deleted id=%s by admin=%s", inspection_id, admin.id)
    return {"deleted": True, "id": str(inspection_id)}
