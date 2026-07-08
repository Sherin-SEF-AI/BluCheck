"""S3 and SQS helpers: presigned multipart uploads, signed reads, cascade delete,
and fallback extraction enqueue. Every AWS call is wrapped so failures surface clearly.
Presigned URLs are never logged.
"""

from __future__ import annotations

import json
import logging
import os

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import HTTPException, status

from .config import get_settings

logger = logging.getLogger("blucheck.storage")
_settings = get_settings()

# Scale-to-zero worker: nudge it awake the instant work is enqueued so an inspection is
# processed in seconds, not after the ~5 min SQS-metric alarm delay. Configured via env.
_ECS_CLUSTER = os.environ.get("ECS_CLUSTER")
_WORKER_SERVICE = os.environ.get("WORKER_SERVICE")

# SigV4 is required for KMS-encrypted objects served via presigned URLs.
# Pin the regional endpoint and virtual addressing so presigned URLs target
# s3.<region>.amazonaws.com directly. Without this, boto3 presigns against the global
# s3.amazonaws.com host, which returns a 307 redirect that mobile uploaders do not
# follow on a PUT, silently failing every part upload.
_s3 = boto3.client(
    "s3",
    region_name=_settings.aws_region,
    endpoint_url=f"https://s3.{_settings.aws_region}.amazonaws.com",
    config=Config(
        signature_version="s3v4",
        s3={"addressing_style": "virtual"},
        retries={"max_attempts": 5, "mode": "standard"},
    ),
)
_sqs = boto3.client("sqs", region_name=_settings.aws_region)
_ecs = boto3.client("ecs", region_name=_settings.aws_region)

RAW_KEY = "raw/{inspection_id}/{kind}.mp4"


def wake_worker() -> None:
    """Immediately bump the scale-to-zero worker to one task if it is idle, so extraction
    starts within seconds instead of waiting for the queue-depth alarm (SQS metrics lag ~5
    min). Best-effort: the alarm remains the backstop, and scale-in returns it to zero. No-op
    if not configured or the worker is already running."""
    if not _ECS_CLUSTER or not _WORKER_SERVICE:
        return
    try:
        svc = _ecs.describe_services(cluster=_ECS_CLUSTER, services=[_WORKER_SERVICE]).get("services", [])
        if svc and svc[0].get("desiredCount", 0) == 0:
            _ecs.update_service(cluster=_ECS_CLUSTER, service=_WORKER_SERVICE, desiredCount=1)
            logger.info("woke worker service (desired 0 -> 1)")
    except ClientError as err:
        logger.warning("wake_worker failed: %s", err)


def raw_key(inspection_id: str, kind: str) -> str:
    return RAW_KEY.format(inspection_id=inspection_id, kind=kind)


def _fail(msg: str, err: Exception) -> HTTPException:
    logger.error("storage_error: %s: %s", msg, err)
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=msg)


def create_multipart(key: str, content_type: str) -> str:
    try:
        resp = _s3.create_multipart_upload(
            Bucket=_settings.media_bucket,
            Key=key,
            ContentType=content_type,
            ServerSideEncryption="aws:kms",
        )
        return resp["UploadId"]
    except ClientError as err:
        raise _fail("Could not start upload", err)


def find_active_upload(key: str) -> str | None:
    """Return the newest in-progress multipart upload id for a key, if any."""
    try:
        resp = _s3.list_multipart_uploads(Bucket=_settings.media_bucket, Prefix=key)
    except ClientError as err:
        raise _fail("Could not list uploads", err)
    uploads = [u for u in resp.get("Uploads", []) if u["Key"] == key]
    if not uploads:
        return None
    uploads.sort(key=lambda u: u["Initiated"], reverse=True)
    return uploads[0]["UploadId"]


def presign_parts(key: str, upload_id: str, part_count: int) -> list[dict]:
    parts = []
    for part_number in range(1, part_count + 1):
        try:
            url = _s3.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": _settings.media_bucket,
                    "Key": key,
                    "UploadId": upload_id,
                    "PartNumber": part_number,
                },
                ExpiresIn=_settings.upload_url_ttl,
            )
        except ClientError as err:
            raise _fail("Could not presign upload part", err)
        parts.append({"part_number": part_number, "url": url})
    return parts


def complete_multipart(key: str, upload_id: str, parts: list[dict]) -> None:
    # Prefer the parts S3 actually holds (with the ETags S3 assigned) rather than the
    # client-provided ETags. Mobile uploaders can report an ETag that does not match the
    # stored part (e.g. progress-based PUT), which makes CompleteMultipartUpload fail with
    # InvalidPart even though every part uploaded fine. Listing the parts is authoritative.
    try:
        listed: list[dict] = []
        paginator = _s3.get_paginator("list_parts")
        for page in paginator.paginate(
            Bucket=_settings.media_bucket, Key=key, UploadId=upload_id
        ):
            for p in page.get("Parts", []):
                listed.append({"ETag": p["ETag"], "PartNumber": p["PartNumber"]})
    except ClientError as err:
        # NoSuchUpload means this multipart session was already completed or aborted. If the
        # target object already exists, an earlier or parallel session (or a client retry)
        # finished the upload, so this is an idempotent no-op success, not a failure. The mobile
        # uploader can legitimately start two sessions or retry a completed one; surfacing a 502
        # here shows the driver a false "upload error" even though the video landed fine.
        if err.response.get("Error", {}).get("Code") == "NoSuchUpload" and object_exists(key):
            logger.info("multipart %s already completed; treating complete as idempotent", key)
            return
        raise _fail("Could not read uploaded parts", err)

    if not listed:
        # Fall back to client-provided ETags if S3 reports nothing (should not happen).
        listed = [
            {"ETag": p["etag"], "PartNumber": p["part_number"]}
            for p in sorted(parts, key=lambda p: p["part_number"])
        ]

    expected = len(parts)
    if expected and len(listed) < expected:
        logger.error(
            "incomplete multipart for %s: S3 has %d of %d parts", key, len(listed), expected
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Upload incomplete: some parts are missing, please retry",
        )

    listed.sort(key=lambda x: x["PartNumber"])
    try:
        _s3.complete_multipart_upload(
            Bucket=_settings.media_bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": listed},
        )
    except ClientError as err:
        raise _fail("Could not finalize upload", err)


def object_exists(key: str) -> bool:
    try:
        _s3.head_object(Bucket=_settings.media_bucket, Key=key)
        return True
    except ClientError as err:
        if err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise _fail("Could not verify uploaded object", err)


def presign_get(key: str, ttl: int | None = None) -> str:
    try:
        return _s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": _settings.media_bucket, "Key": key},
            ExpiresIn=ttl or _settings.frame_url_ttl,
        )
    except ClientError as err:
        raise _fail("Could not generate media URL", err)


def delete_prefix(prefix: str) -> int:
    """Delete every object under a prefix. Used by the admin cascade-delete path."""
    deleted = 0
    paginator = _s3.get_paginator("list_objects_v2")
    try:
        for page in paginator.paginate(Bucket=_settings.media_bucket, Prefix=prefix):
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if objects:
                _s3.delete_objects(
                    Bucket=_settings.media_bucket, Delete={"Objects": objects}
                )
                deleted += len(objects)
    except ClientError as err:
        raise _fail("Could not delete media objects", err)
    return deleted


def enqueue_extraction(inspection_id: str) -> None:
    """Explicit fallback enqueue so extraction is guaranteed even if the S3 event is lost."""
    if not _settings.extraction_queue_url:
        logger.warning("extraction_queue_url not configured; skipping fallback enqueue")
        return
    body = {
        "source": "api-fallback",
        "inspection_id": inspection_id,
        "bucket": _settings.media_bucket,
    }
    try:
        _sqs.send_message(
            QueueUrl=_settings.extraction_queue_url, MessageBody=json.dumps(body)
        )
    except ClientError as err:
        # Do not fail the request; the S3 event is the primary trigger.
        logger.error("fallback enqueue failed for %s: %s", inspection_id, err)
    # Wake the scale-to-zero worker now so processing starts in seconds, not minutes.
    wake_worker()
