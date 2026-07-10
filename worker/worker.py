"""BluCheck frame extraction worker.

Long-polls SQS. Each message (an S3 ObjectCreated event on raw/ or an API fallback
message) identifies an inspection. For each uploaded-but-unextracted capture, the worker
downloads the video, extracts full-resolution frames plus thumbnails, embeds GPS and
timestamp metadata, uploads to S3, and records frame rows. When both captures are
extracted the inspection flips to pending. Extraction is idempotent per capture.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3
import requests
from botocore.config import Config
from sqlalchemy import select

from db import (
    AuditLog,
    Capture,
    Frame,
    Inspection,
    ModelVersion,
    ScoringResult,
    SessionLocal,
    ZoneScore,
)
from extract import ExtractionError, extract_capture
from score import ScoringError, score_frames
from selection import compute_metrics, select as select_frames

FRAME_SELECT_TOP_N = int(os.environ.get("FRAME_SELECT_TOP_N", "8"))
PHASH_THRESHOLD = int(os.environ.get("PHASH_THRESHOLD", "6"))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    stream=sys.stdout,
)
logger = logging.getLogger("blucheck.worker")

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
QUEUE_URL = os.environ["EXTRACTION_QUEUE_URL"]
MEDIA_BUCKET = os.environ["MEDIA_BUCKET"]
KMS_KEY_ID = os.environ.get("KMS_KEY_ID") or None
FRAME_FPS = int(os.environ.get("FRAME_FPS", "2"))
THUMB_WIDTH = int(os.environ.get("THUMB_WIDTH", "480"))
WAIT_SECONDS = int(os.environ.get("SQS_WAIT_SECONDS", "20"))

_s3 = boto3.client(
    "s3",
    region_name=AWS_REGION,
    config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
)
_sqs = boto3.client("sqs", region_name=AWS_REGION, config=Config(retries={"max_attempts": 5}))


def _inspection_ids_from_message(body: dict) -> set[str]:
    """Support both S3 event notifications and the API fallback message shape."""
    ids: set[str] = set()

    # API fallback: {"inspection_id": "..."}
    if "inspection_id" in body:
        ids.add(str(body["inspection_id"]))

    # S3 event: {"Records": [{"s3": {"object": {"key": "raw/<id>/<kind>.mp4"}}}]}
    for record in body.get("Records", []):
        key = record.get("s3", {}).get("object", {}).get("key")
        if not key:
            continue
        key = unquote_plus(key)
        parts = key.split("/")
        if len(parts) >= 2 and parts[0] == "raw":
            ids.add(parts[1])
    return ids


def _put_file(local_path, key: str, content_type: str) -> None:
    extra = {"ContentType": content_type, "ServerSideEncryption": "aws:kms"}
    if KMS_KEY_ID:
        extra["SSEKMSKeyId"] = KMS_KEY_ID
    _s3.upload_file(str(local_path), MEDIA_BUCKET, key, ExtraArgs=extra)


def _send_cap(db) -> int:
    """The VLM per-call image cap from the active model version's scoring_config (default 5).
    Selection uses this so a raised cap actually sends more frames. Never raises."""
    try:
        mv = db.execute(select(ModelVersion).where(ModelVersion.active.is_(True))).scalar_one_or_none()
        if mv is not None and isinstance(mv.scoring_config, dict):
            return int(mv.scoring_config.get("max_images_per_call", 5) or 5)
    except Exception as err:  # noqa: BLE001 - selection must never fail on a config read
        logger.warning("could not read send cap: %s", err)
    return 5


def _expected_kinds(inspection) -> set[str]:
    """Which capture kinds an inspection must have before it is ready to score. Normally both;
    a targeted re-clean declares a subset in device_meta.reclean_kinds."""
    if getattr(inspection, "reinspection_of", None) is not None:
        meta = getattr(inspection, "device_meta", None)
        if isinstance(meta, dict) and isinstance(meta.get("reclean_kinds"), list):
            sub = {k for k in meta["reclean_kinds"] if k in ("exterior", "interior")}
            if sub:
                return sub
    return {"exterior", "interior"}


def _process_capture(db, capture: Capture) -> bool:
    """Extract one capture. Returns True on success. Idempotent (deletes prior frames).

    Claims the capture under a real row lock (SELECT ... FOR UPDATE SKIP LOCKED) and holds it
    for the whole extraction, so a duplicate SQS delivery (S3 event + API fallback) or a second
    worker cannot process the same capture concurrently. If another worker holds the row, we
    skip; if this worker dies mid-extraction the transaction rolls back and the message redrives.
    """
    locked = db.execute(
        select(Capture).where(Capture.id == capture.id).with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if locked is None:
        logger.info("capture %s locked by another worker; skipping", capture.id)
        return False
    capture = locked
    if capture.status == "extracted":
        logger.info("capture %s already extracted; skipping", capture.id)
        return True

    inspection_id = str(capture.inspection_id)
    kind = capture.kind

    # Mark claimed but keep the row lock held (flush, do not commit) until extraction finishes.
    capture.status = "processing"
    db.flush()

    with tempfile.TemporaryDirectory(prefix="blucheck-") as tmp:
        video_path = os.path.join(tmp, f"{kind}.mp4")
        try:
            _s3.download_file(MEDIA_BUCKET, capture.video_s3_key, video_path)
        except Exception as err:
            raise ExtractionError(f"download failed for {capture.video_s3_key}: {err}") from err

        artifacts = extract_capture(
            video_path,
            os.path.join(tmp, "out"),
            recorded_at_utc=capture.recorded_at_utc,
            gps_lat=capture.gps_lat,
            gps_lon=capture.gps_lon,
            fps=FRAME_FPS,
            thumb_width=THUMB_WIDTH,
        )

        # Frame selection: score sharpness/exposure, dedup, mark the best top-N. Select at least
        # as many as the VLM send-cap (scoring_config.max_images_per_call) so raising that cap
        # actually sends more frames end to end, not just the historical 8.
        metrics = [compute_metrics(str(art.full_path), art.seq) for art in artifacts]
        metrics_by_seq = {m.seq: m for m in metrics}
        top_n = max(FRAME_SELECT_TOP_N, _send_cap(db))
        selected_seqs = select_frames(metrics, top_n, PHASH_THRESHOLD)
        logger.info(
            "selected %d of %d frames for capture %s", len(selected_seqs), len(artifacts), capture.id
        )

        # Idempotent replace: clear any prior frames for this capture.
        db.query(Frame).filter(Frame.capture_id == capture.id).delete()

        for art in artifacts:
            full_key = f"frames/{inspection_id}/{kind}/{art.seq}.jpg"
            thumb_key = f"thumbs/{inspection_id}/{kind}/{art.seq}.jpg"
            _put_file(art.full_path, full_key, "image/jpeg")
            _put_file(art.thumb_path, thumb_key, "image/jpeg")

            m = metrics_by_seq.get(art.seq)
            db.add(
                Frame(
                    id=uuid.uuid4(),
                    capture_id=capture.id,
                    seq=art.seq,
                    offset_ms=art.offset_ms,
                    absolute_ts_utc=art.absolute_ts_utc,
                    gps_lat=art.gps_lat,
                    gps_lon=art.gps_lon,
                    s3_key_full=full_key,
                    s3_key_thumb=thumb_key,
                    width=art.width,
                    height=art.height,
                    selected=art.seq in selected_seqs,
                    blur_score=m.blur if m else None,
                    exposure_score=m.exposure if m else None,
                    phash=m.phash if m else None,
                )
            )

        capture.frame_count = len(artifacts)
        capture.resolution = f"{artifacts[0].width}x{artifacts[0].height}"
        capture.status = "extracted"
        db.commit()
        logger.info("capture %s extracted: %d frames", capture.id, len(artifacts))
        return True


API_INTERNAL_URL = os.environ.get("API_INTERNAL_URL", "https://d1sc0mm026oa3r.cloudfront.net")
RUNPOD_SECRET_NAME = os.environ.get("RUNPOD_SECRET_NAME", "blucheck/runpod")
_secrets = boto3.client("secretsmanager", region_name=AWS_REGION)


def _internal_token() -> str | None:
    try:
        cfg = json.loads(_secrets.get_secret_value(SecretId=RUNPOD_SECRET_NAME)["SecretString"])
        return cfg.get("internal_token")
    except Exception as err:  # noqa: BLE001
        logger.warning("could not read internal_token: %s", err)
        return None


def _report_vision_incident(model: str, message: str) -> None:
    """Best-effort: tell the backend a vision-model call failed, so it shows on dashboard health."""
    tok = _internal_token()
    if not tok:
        return
    try:
        requests.post(
            f"{API_INTERNAL_URL}/model/vision-incident",
            headers={"Authorization": f"Bearer {tok}"},
            json={"source": "worker", "model": model, "message": message[:400]},
            timeout=10,
        )
    except requests.RequestException:
        pass


def _delegate_decision(inspection_id: str) -> None:
    """Hand the decision to the backend (the single decision engine, which also sends the
    driver notification). Best-effort: if it fails the inspection stays pending and the
    admin 'run backlog' / run-pending path recovers it, so scoring is never lost."""
    tok = _internal_token()
    if not tok:
        logger.warning("no internal_token; inspection %s left pending for run-pending", inspection_id)
        return
    try:
        r = requests.post(
            f"{API_INTERNAL_URL}/model/agent-decide/{inspection_id}",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=25,
        )
        if r.status_code >= 300:
            logger.warning("agent-decide non-2xx for %s: %s %s", inspection_id, r.status_code, r.text[:200])
        else:
            logger.info("agent-decide %s -> %s", inspection_id, r.text[:200])
    except requests.RequestException as err:
        logger.warning("agent-decide call failed for %s: %s (left pending)", inspection_id, err)


def run_scoring(db, inspection_id) -> None:
    """VLM scoring stage. Scores the selected frames and stores the result, then delegates
    the approve/reject/route decision to the backend so there is exactly one decision engine.
    Idempotent per (inspection, model_version). Fails closed to human review on any error.
    """
    mv = db.execute(select(ModelVersion).where(ModelVersion.active.is_(True))).scalar_one_or_none()
    if mv is None or mv.mode == "disabled":  # kill switch: no scoring at all
        return
    exists = db.execute(
        select(ScoringResult).where(
            ScoringResult.inspection_id == inspection_id,
            ScoringResult.model_version_id == mv.id,
        )
    ).first()
    if exists:
        return

    rows = list(
        db.execute(
            select(Frame, Capture.kind)
            .join(Capture, Capture.id == Frame.capture_id)
            .where(Capture.inspection_id == inspection_id, Frame.selected.is_(True))
            .order_by(Capture.kind, Frame.seq)
        ).all()
    )
    if not rows:
        return

    with tempfile.TemporaryDirectory(prefix="score-") as tmp:
        # Group selected frames by capture kind so exterior and interior are scored in
        # separate calls (vision models cap images per request); this also keeps each
        # zone's frames together.
        frames_by_kind: dict[str, list[str]] = {}
        frame_ids_by_kind: dict[str, list[str]] = {}
        for i, (frame, kind) in enumerate(rows):
            p = os.path.join(tmp, f"f{i}.jpg")
            _s3.download_file(MEDIA_BUCKET, frame.s3_key_full, p)
            frames_by_kind.setdefault(kind, []).append(p)
            # Parallel real frame ids so scoring can tag each issue with the exact frame it is in.
            frame_ids_by_kind.setdefault(kind, []).append(str(frame.id))
        try:
            result, latency, stats = score_frames(
                frames_by_kind, mv.vlm_model or "", mv.scoring_config,
                frame_ids_by_kind=frame_ids_by_kind,
            )
        except ScoringError as e:
            logger.warning("scoring failed inspection=%s: %s (leaving for human)", inspection_id, e)
            # Surface model-access failures (blocked/rate-limited model) to the dashboard health.
            msg = str(e).lower()
            if any(w in msg for w in ("403", "forbidden", "blocked", "429", "rate", "model")):
                _report_vision_incident(mv.vlm_model or "", str(e))
            return

    overall = result.get("overall_score")
    conf = result.get("overall_confidence")
    zones = result.get("zones") or []

    # Store the score. The decision itself is deliberately NOT made here: it is delegated to
    # the backend below so the mode/threshold logic and driver notification live in one place.
    sr = ScoringResult(
        id=uuid.uuid4(),
        inspection_id=inspection_id,
        model_version_id=mv.id,
        overall_score=overall,
        overall_confidence=conf,
        decision="none",  # authoritative decision is set by the backend engine on delegate
        # Store the exact resolved scoring_config and image count so every score is reproducible
        # and cost is auditable (item 1 + cost accounting).
        raw_json={
            "latency_ms": latency,
            "image_count": stats["image_count"],
            "prompt_version": mv.prompt_version,
            "scoring_config": stats["scoring_config"],
            "not_vehicle": stats.get("not_vehicle", False),
            "result": result,
        },
    )
    db.add(sr)
    db.flush()
    for z in zones:
        db.add(
            ZoneScore(
                id=uuid.uuid4(),
                scoring_result_id=sr.id,
                zone_key=z["zone_key"],
                score=z.get("score"),
                confidence=z.get("confidence"),
                issues=z.get("issues"),
            )
        )
    db.commit()
    logger.info(
        "scored inspection=%s overall=%s conf=%s latency=%sms", inspection_id, overall, conf, latency
    )

    # One decision engine: the backend applies the mode/thresholds and notifies the driver.
    _delegate_decision(str(inspection_id))


def handle_message(body: dict) -> None:
    ids = _inspection_ids_from_message(body)
    if not ids:
        logger.warning("message carried no inspection id; ignoring")
        return

    with SessionLocal() as db:
        for inspection_id in ids:
            try:
                uuid.UUID(inspection_id)
            except ValueError:
                logger.warning("bad inspection id %s", inspection_id)
                continue

            captures = list(
                db.execute(
                    select(Capture).where(Capture.inspection_id == uuid.UUID(inspection_id))
                ).scalars()
            )
            if not captures:
                logger.info("no captures for inspection %s yet", inspection_id)
                continue

            for capture in captures:
                if capture.status in ("uploaded", "processing", "failed"):
                    _process_capture(db, capture)

            # Re-read to decide overall status.
            captures = list(
                db.execute(
                    select(Capture).where(Capture.inspection_id == uuid.UUID(inspection_id))
                ).scalars()
            )
            kinds_extracted = {c.kind for c in captures if c.status == "extracted"}
            inspection = db.get(Inspection, uuid.UUID(inspection_id))
            if inspection is None:
                continue
            # A normal inspection scores once BOTH captures are extracted. A targeted re-clean
            # (reinspection_of set) re-films only the flagged group(s), so it declares which kinds
            # to expect in device_meta.reclean_kinds; score once exactly those are extracted and no
            # capture is still mid-flight (prevents scoring a 2-group re-clean before both land).
            all_extracted = bool(captures) and all(c.status == "extracted" for c in captures)
            expected = _expected_kinds(inspection)
            ready = bool(expected) and expected.issubset(kinds_extracted) and all_extracted
            if ready:
                inspection.status = "pending"
                db.commit()
                logger.info("inspection %s -> pending", inspection_id)
                # VLM scoring stage (shadow by default; router may auto-decide in auto mode).
                try:
                    run_scoring(db, uuid.UUID(inspection_id))
                except Exception as err:  # noqa: BLE001 - scoring never blocks the pipeline
                    logger.error("scoring stage error for %s: %s", inspection_id, err)


def _mark_failed(body: dict, err: Exception) -> None:
    ids = _inspection_ids_from_message(body)
    with SessionLocal() as db:
        for inspection_id in ids:
            try:
                insp = db.get(Inspection, uuid.UUID(inspection_id))
            except ValueError:
                continue
            if insp is not None and insp.status in ("processing", "uploading"):
                insp.status = "failed"
            for cap in db.execute(
                select(Capture).where(Capture.inspection_id == uuid.UUID(inspection_id))
            ).scalars():
                if cap.status == "processing":
                    cap.status = "failed"
        db.commit()
    logger.error("extraction failed: %s", err)


def poll_forever() -> None:
    logger.info("worker started; polling %s", QUEUE_URL.rsplit("/", 1)[-1])
    while True:
        resp = _sqs.receive_message(
            QueueUrl=QUEUE_URL,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=WAIT_SECONDS,
            # Above worst-case processing (download + extract both captures + scoring, with the
            # capture row lock held) so a slow message is not made visible and double-processed.
            VisibilityTimeout=int(os.environ.get("SQS_VISIBILITY_TIMEOUT", "900")),
        )
        messages = resp.get("Messages", [])
        if not messages:
            continue

        for msg in messages:
            receipt = msg["ReceiptHandle"]
            try:
                body = json.loads(msg["Body"])
            except json.JSONDecodeError:
                logger.error("undecodable message body; deleting to avoid poison loop")
                _sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt)
                continue

            # S3 test events have an "Event" key and no Records; ignore and delete.
            if body.get("Event") == "s3:TestEvent":
                _sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt)
                continue

            try:
                handle_message(body)
                _sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt)
            except Exception as err:  # noqa: BLE001 - bounded retry via SQS redrive
                _mark_failed(body, err)
                # Do not delete: message becomes visible again and retries until the
                # DLQ maxReceiveCount is reached.


if __name__ == "__main__":
    poll_forever()
