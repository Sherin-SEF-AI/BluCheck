"""Worker database access. Compact ORM models mapping the tables the worker touches.
The authoritative schema and migrations live in backend/; these mirror the columns the
extraction pipeline reads and writes.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        arn = os.environ.get("BLUCHECK_DB_SECRET_ARN")
        region = os.environ.get("AWS_REGION", "ap-south-1")
        if arn:
            import boto3

            url = boto3.client("secretsmanager", region_name=region).get_secret_value(
                SecretId=arn
            )["SecretString"]
    if not url:
        raise RuntimeError("DATABASE_URL not configured for worker")
    return url


class Base(DeclarativeBase):
    pass


class Inspection(Base):
    __tablename__ = "inspections"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    # Read by the ready-to-score gate: a targeted re-clean (reinspection_of set) may carry a
    # subset of captures, declared in device_meta.reclean_kinds.
    reinspection_of: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    device_meta: Mapped[dict | None] = mapped_column(JSONB)


class Capture(Base):
    __tablename__ = "captures"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    inspection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("inspections.id"))
    kind: Mapped[str] = mapped_column(String(16))
    video_s3_key: Mapped[str] = mapped_column(String(512))
    duration_s: Mapped[float | None] = mapped_column(Float)
    recorded_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gps_lat: Mapped[float | None] = mapped_column(Float)
    gps_lon: Mapped[float | None] = mapped_column(Float)
    resolution: Mapped[str | None] = mapped_column(String(32))
    frame_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))


class Frame(Base):
    __tablename__ = "frames"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    capture_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("captures.id"))
    seq: Mapped[int] = mapped_column(Integer)
    offset_ms: Mapped[int] = mapped_column(Integer)
    absolute_ts_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gps_lat: Mapped[float | None] = mapped_column(Float)
    gps_lon: Mapped[float | None] = mapped_column(Float)
    s3_key_full: Mapped[str] = mapped_column(String(512))
    s3_key_thumb: Mapped[str] = mapped_column(String(512))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    blur_score: Mapped[float | None] = mapped_column(Float)
    exposure_score: Mapped[float | None] = mapped_column(Float)
    phash: Mapped[str | None] = mapped_column(String(32))
    zone_key: Mapped[str | None] = mapped_column(String(32))


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    vlm_model: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    thresholds: Mapped[dict | None] = mapped_column(JSONB)
    # Scoring-layer math, tunable per version; null => worker uses its hardcoded defaults.
    scoring_config: Mapped[dict | None] = mapped_column(JSONB)
    mode: Mapped[str] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean)


class ScoringResult(Base):
    __tablename__ = "scoring_results"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    inspection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("inspections.id"))
    model_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("model_versions.id"))
    overall_score: Mapped[float | None] = mapped_column(Float)
    overall_confidence: Mapped[float | None] = mapped_column(Float)
    decision: Mapped[str] = mapped_column(String(16), default="none")
    raw_json: Mapped[dict | None] = mapped_column(JSONB)


class ZoneScore(Base):
    __tablename__ = "zone_scores"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scoring_result_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("scoring_results.id"))
    zone_key: Mapped[str] = mapped_column(String(32))
    score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    issues: Mapped[list | None] = mapped_column(JSONB)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict | None] = mapped_column(JSONB)


engine = create_engine(_database_url(), pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
