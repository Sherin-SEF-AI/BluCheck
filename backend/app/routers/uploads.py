"""Presigned multipart upload lifecycle for capture videos.

upload-url starts (or resumes) a multipart upload and returns presigned part URLs.
complete finalizes the S3 object, records the capture, and when both captures of an
inspection are present flips the inspection to processing and enqueues extraction.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import storage
from ..auth import get_current_user
from ..config import get_settings
from ..db import get_db
from ..models import Capture, Inspection, User
from ..schemas import (
    CaptureOut,
    CompleteUploadRequest,
    CompleteUploadResponse,
    PresignedPart,
    UploadUrlRequest,
    UploadUrlResponse,
)

router = APIRouter(prefix="/inspections", tags=["uploads"])
logger = logging.getLogger("blucheck.uploads")
_settings = get_settings()

KIND_PATH = Path(pattern="^(exterior|interior)$")


def _owned_inspection(inspection_id: uuid.UUID, user: User, db: Session) -> Inspection:
    inspection = db.get(Inspection, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Inspection not found")
    if user.role != "driver" or inspection.driver_id != user.id:
        raise HTTPException(status_code=403, detail="Not your inspection")
    return inspection


@router.post(
    "/{inspection_id}/captures/{kind}/upload-url", response_model=UploadUrlResponse
)
def create_upload_url(
    body: UploadUrlRequest,
    inspection_id: uuid.UUID,
    kind: str = KIND_PATH,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UploadUrlResponse:
    _owned_inspection(inspection_id, user, db)
    key = storage.raw_key(str(inspection_id), kind)

    # Resume path: reuse the caller's upload id, or an existing in-progress upload.
    upload_id = body.upload_id or storage.find_active_upload(key)
    if not upload_id:
        upload_id = storage.create_multipart(key, body.content_type)

    parts = storage.presign_parts(key, upload_id, body.part_count)
    logger.info(
        "upload_url_issued inspection=%s kind=%s parts=%d resumed=%s",
        inspection_id,
        kind,
        body.part_count,
        bool(body.upload_id),
    )
    return UploadUrlResponse(
        key=key,
        upload_id=upload_id,
        part_size=_settings.multipart_part_size,
        parts=[PresignedPart(**p) for p in parts],
    )


@router.post(
    "/{inspection_id}/captures/{kind}/complete", response_model=CompleteUploadResponse
)
def complete_upload(
    body: CompleteUploadRequest,
    inspection_id: uuid.UUID,
    kind: str = KIND_PATH,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompleteUploadResponse:
    inspection = _owned_inspection(inspection_id, user, db)
    key = storage.raw_key(str(inspection_id), kind)

    storage.complete_multipart(
        key,
        body.upload_id,
        [{"part_number": p.part_number, "etag": p.etag} for p in body.parts],
    )
    if not storage.object_exists(key):
        raise HTTPException(status_code=502, detail="Upload did not land in storage")

    # Idempotent per (inspection, kind): update if the capture already exists.
    capture = db.execute(
        select(Capture).where(
            Capture.inspection_id == inspection_id, Capture.kind == kind
        )
    ).scalar_one_or_none()

    if capture is None:
        capture = Capture(inspection_id=inspection_id, kind=kind, video_s3_key=key)
        db.add(capture)

    capture.video_s3_key = key
    capture.duration_s = body.duration_s
    capture.recorded_at_utc = body.recorded_at_utc
    if body.gps:
        capture.gps_lat = body.gps.lat
        capture.gps_lon = body.gps.lon
    capture.resolution = body.resolution
    capture.status = "uploaded"

    db.flush()

    # When all expected captures are uploaded, move to processing and guarantee extraction.
    # A normal inspection expects both exterior + interior; a targeted re-clean declares the
    # flagged subset it re-films in device_meta.reclean_kinds.
    kinds_present = set(
        db.execute(
            select(Capture.kind).where(Capture.inspection_id == inspection_id)
        ).scalars()
    )
    expected = {"exterior", "interior"}
    if inspection.reinspection_of is not None and isinstance(inspection.device_meta, dict):
        rk = inspection.device_meta.get("reclean_kinds")
        if isinstance(rk, list):
            sub = {k for k in rk if k in ("exterior", "interior")}
            if sub:
                expected = sub
    ready = expected.issubset(kinds_present)
    if ready and inspection.status in ("uploading", "failed"):
        inspection.status = "processing"

    db.commit()
    db.refresh(capture)

    if ready:
        # Fallback to the primary S3 event notification.
        storage.enqueue_extraction(str(inspection_id))

    logger.info(
        "capture_completed inspection=%s kind=%s ready=%s status=%s",
        inspection_id,
        kind,
        ready,
        inspection.status,
    )
    return CompleteUploadResponse(
        capture=CaptureOut.model_validate(capture), inspection_status=inspection.status
    )
