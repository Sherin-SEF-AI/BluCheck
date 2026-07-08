"""Inspection creation (driver), listing and detail (admin), frame URLs, and delete."""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, joinedload

from .. import agent, appeal_ai, audit, scoring_defaults, self_heal, storage
from ..auth import get_current_user, require_admin
from ..db import get_db
from ..modelcfg import ensure_active_model_version
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
    FlaggedFrame,
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

# Which capture a zone is seen in, so a flagged zone's photo comes from the right clip. Windows
# and glass are captured in the exterior walk-around.
ZONE_TO_KIND = {
    "seats": "interior",
    "floor_mats": "interior",
    "dashboard_console": "interior",
    "exterior_body": "exterior",
    "windows_glass": "exterior",
    "boot": "exterior",
}
ZONE_LABELS = {
    "seats": "Seats",
    "floor_mats": "Floor mats",
    "dashboard_console": "Dashboard",
    "exterior_body": "Exterior body",
    "windows_glass": "Windows / glass",
    "boot": "Boot",
}

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
    """One-shot capture pre-check run automatically by the app before recording: is this a
    vehicle at all, so non-car footage is rejected on the phone before any upload.

    Plate OCR is disabled for now (read_plate always null); the response keeps the plate fields
    so the client contract is unchanged and OCR can be re-enabled later without a client update."""
    import base64 as _b64

    from .. import plateocr

    if user.role != "driver":
        raise HTTPException(status_code=400, detail="Only drivers can pre-check")
    try:
        img = _b64.b64decode(body.image_b64)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid image data")
    veh = plateocr.detect_vehicle(img)
    return {
        "is_vehicle": veh["is_vehicle"],
        "vehicle_confidence": veh["confidence"],
        "labels": veh["labels"],
        "read_plate": None,
        "matched": False,
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


def _build_flagged_frames(inspection, scoring, reject_labels, max_images):
    """Map each detected issue to the exact analyzed frame that shows it.

    The worker sends selected frames to the VLM as one continuous list, exterior clip first then
    interior, each capped at max_images, ordered by frame sequence. An issue's 1-based frame_index
    points into that list. We rebuild the same list to resolve it. Because the VLM's frame_index
    can be unreliable, we VALIDATE the resolved frame belongs to the zone's own clip and otherwise
    fall back to a representative frame of that clip (exact=False), so the driver never sees an
    exterior photo for an interior problem."""
    # Rebuild the flat list the worker scored: [exterior selected..][interior selected..].
    by_kind: dict[str, list] = {}
    frames_by_id: dict = {}
    for cap in inspection.captures:
        sel = sorted([f for f in cap.frames if f.selected], key=lambda x: x.seq)[:max_images]
        by_kind[cap.kind] = sel
        for f in cap.frames:
            frames_by_id[str(f.id)] = f
    flat = list(by_kind.get("exterior", [])) + list(by_kind.get("interior", []))

    def frame_payload(frame, zone_key, issue_key, severity, description, bbox, exact):
        return FlaggedFrame(
            zone_key=zone_key,
            zone_label=ZONE_LABELS.get(zone_key, zone_key.replace("_", " ").title()),
            issue_key=issue_key,
            severity=severity,
            description=description,
            frame_id=frame.id,
            kind=ZONE_TO_KIND.get(zone_key, "interior"),
            thumb_url=storage.presign_get(frame.s3_key_thumb),
            annotated_endpoint=f"/inspections/{inspection.id}/frames/{frame.id}/annotated",
            bbox=[float(x) for x in bbox] if isinstance(bbox, (list, tuple)) and len(bbox) == 4 else None,
            exact=exact,
        )

    flagged_zone_keys = {l.zone_key for l in reject_labels}
    zones_by_key = {z["zone_key"]: z for z in (scoring or {}).get("zones", [])} if scoring else {}
    out: list[FlaggedFrame] = []
    seen: set = set()

    def fallback_frame(kind):
        return (by_kind.get(kind) or [None])[0]

    for zone_key in flagged_zone_keys:
        expected_kind = ZONE_TO_KIND.get(zone_key, "interior")
        issues = (zones_by_key.get(zone_key) or {}).get("issues") or []
        if not issues:
            fr = fallback_frame(expected_kind)
            if fr and (fr.id, zone_key, "") not in seen:
                seen.add((fr.id, zone_key, ""))
                out.append(frame_payload(fr, zone_key, "", None, None, None, exact=False))
            continue
        for iss in issues:
            frame = None
            exact = True
            # Preferred: the scorer tagged this issue with the exact frame id it detected the
            # issue in (frame-accurate, resolved per-call so it is always the right clip).
            fid = iss.get("frame_id")
            if isinstance(fid, str) and fid in frames_by_id:
                frame = frames_by_id[fid]
                exact = bool(iss.get("frame_exact", True))
            else:
                # Legacy scores (no frame_id): resolve the model's frame_index and validate kind.
                fi = iss.get("frame_index")
                if isinstance(fi, int) and 1 <= fi <= len(flat):
                    cand = flat[fi - 1]
                    cand_kind = next((k for k, fs in by_kind.items() if cand in fs), None)
                    if cand_kind == expected_kind:
                        frame = cand
                if frame is None:
                    frame = fallback_frame(expected_kind)
                    exact = False
            if frame is None:
                continue
            key = (frame.id, zone_key, iss.get("issue_key", ""))
            if key in seen:
                continue
            seen.add(key)
            out.append(
                frame_payload(
                    frame, zone_key, iss.get("issue_key", ""), iss.get("severity"),
                    iss.get("description"), iss.get("bbox"), exact,
                )
            )
    # Most severe first so the driver sees the worst offenders at the top.
    order = {"severe": 0, "moderate": 1, "minor": 2, None: 3}
    out.sort(key=lambda f: order.get(f.severity, 3))
    return out[:8]


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

    # Resolve the exact frames behind the rejection (only meaningful when there are flagged zones).
    flagged_frames: list[FlaggedFrame] = []
    if reject_labels:
        raw_sc = (sr.raw_json or {}).get("scoring_config") if sr is not None and isinstance(sr.raw_json, dict) else None
        max_images = 5
        if isinstance(raw_sc, dict) and isinstance(raw_sc.get("max_images_per_call"), int):
            max_images = raw_sc["max_images_per_call"]
        flagged_frames = _build_flagged_frames(inspection, scoring, reject_labels, max_images)

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
        flagged_frames=flagged_frames,
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


def _resolve_appeal(db: Session, inspection: Inspection, prior_reason: str | None) -> dict | None:
    """Run the independent appeal agent over the latest scoring evidence + active policy. Returns
    the ruling dict, or None to force a human (kill switch, no evidence, or agent unavailable)."""
    mv = ensure_active_model_version(db)
    if mv.mode == "disabled":  # kill switch: never auto-resolve
        return None
    sr = db.execute(
        select(ScoringResult)
        .where(ScoringResult.inspection_id == inspection.id)
        .order_by(ScoringResult.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if sr is None:
        return None
    zrows = db.execute(select(ZoneScore).where(ZoneScore.scoring_result_id == sr.id)).scalars()
    scoring = {
        "overall": sr.overall_score,
        "zones": [
            {
                "zone_key": z.zone_key,
                "score": z.score,
                "issues": [
                    {"issue_key": i.get("issue_key"), "severity": i.get("severity")}
                    for i in (z.issues or [])
                ],
            }
            for z in zrows
        ],
    }
    eff = scoring_defaults.resolve(mv.scoring_config)
    policy = {
        "zone_weight": eff.get("zone_weight"),
        "severity_cap": eff.get("severity_cap"),
        "thresholds": (mv.thresholds or {}).get("overall", {}),
    }
    return appeal_ai.resolve(scoring, policy, prior_reason)


@router.post("/self-heal")
def self_heal_now(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Manually trigger the self-healing sweep (also runs automatically in the background):
    auto-reprocess failed/stuck inspections and re-score any that finished extraction but were
    never scored. Bounded per inspection so it can never loop."""
    result = self_heal.run(db)
    audit.record(
        db, actor_id=admin.id, action="self_heal_manual", entity="system",
        entity_id="self_heal", detail=result,
    )
    db.commit()
    return {"ok": True, **result}


@router.post("/{inspection_id}/appeal")
def appeal(
    inspection_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Driver disputes an automated rejection. An independent agent re-examines the evidence and
    self-resolves the appeal: REVERSE (approve), UPHOLD (keep rejected), or ESCALATE to a human
    (status -> pending) only for genuinely borderline cases. The kill switch (disabled mode) or a
    missing agent forces the safe path: escalate to a human. Only the owning driver may appeal,
    and only a rejected inspection."""
    inspection = db.get(Inspection, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Inspection not found")
    if inspection.driver_id != user.id:
        raise HTTPException(status_code=403, detail="Not your inspection")
    if inspection.status != "rejected":
        raise HTTPException(status_code=409, detail="Only a rejected inspection can be appealed")

    prior_reason = inspection.reject_reason
    prior_source = agent.decision_source(inspection)
    audit.record(
        db, actor_id=user.id, action="driver_appeal", entity="inspection",
        entity_id=str(inspection_id),
        detail={"prior_reject_reason": prior_reason, "prior_decided_by": prior_source, "by": "driver"},
    )
    db.commit()

    ruling = _resolve_appeal(db, inspection, prior_reason)
    if ruling is None or ruling["ruling"] == "escalate":
        # Fall back to a human reviewer.
        inspection.status = "pending"
        inspection.reviewed_at = None
        inspection.reviewed_by = None
        db.commit()
        audit.record(
            db, actor_id=None, action="appeal_escalated", entity="inspection",
            entity_id=str(inspection_id), detail={"ruling": (ruling or {}).get("ruling", "unavailable")},
        )
        db.commit()
        logger.info("appeal_escalated id=%s driver=%s", inspection_id, user.id)
        return {"ok": True, "status": "pending", "ruling": "escalate"}

    if ruling["ruling"] == "reverse":
        inspection.status = "approved"
        inspection.reject_reason = None
        title, msg = "Appeal accepted", "Your appeal was reviewed and accepted. The inspection now passes."
    else:  # uphold
        inspection.status = "rejected"
        inspection.reject_reason = ruling["reason"] or prior_reason
        title, msg = "Appeal reviewed", f"Your appeal was reviewed but the rejection stands: {ruling['reason']}"
    inspection.reviewed_at = datetime.now(timezone.utc)
    inspection.reviewed_by = None  # decided by the agent, not a human
    db.commit()
    audit.record(
        db, actor_id=None, action="appeal_auto_resolved", entity="inspection",
        entity_id=str(inspection_id),
        detail={"ruling": ruling["ruling"], "confidence": ruling["confidence"], "reason": ruling["reason"][:300]},
    )
    db.commit()
    agent._notify(db, inspection, title, msg)
    logger.info("appeal_auto_resolved id=%s ruling=%s conf=%.2f", inspection_id, ruling["ruling"], ruling["confidence"])
    return {"ok": True, "status": inspection.status, "ruling": ruling["ruling"], "reason": ruling["reason"]}


@router.post("/{inspection_id}/submit")
def submit_reclean(
    inspection_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Finalize a targeted re-clean. A re-clean re-films only the flagged zone group(s), so it
    may carry a single capture (e.g. interior only). The normal upload-complete path only
    enqueues extraction once BOTH captures are present, which never happens for a one-group
    re-clean; the driver app calls this after it has uploaded every re-filmed capture. The worker
    scores whatever captures a re-clean has (see its relaxed gate). Only for re-inspections."""
    inspection = db.get(Inspection, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Inspection not found")
    if inspection.driver_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Not your inspection")
    if inspection.reinspection_of is None:
        raise HTTPException(status_code=400, detail="submit is only for re-clean inspections")

    captures = list(
        db.execute(select(Capture).where(Capture.inspection_id == inspection_id)).scalars()
    )
    if not captures:
        raise HTTPException(status_code=409, detail="Upload at least one re-clean clip first")
    if inspection.status in ("uploading", "failed"):
        inspection.status = "processing"
        db.commit()
    storage.enqueue_extraction(str(inspection_id))
    logger.info(
        "reclean_submitted id=%s driver=%s captures=%s",
        inspection_id, user.id, [c.kind for c in captures],
    )
    return {"ok": True, "status": inspection.status, "captures": [c.kind for c in captures]}


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
