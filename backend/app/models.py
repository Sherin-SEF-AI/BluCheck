"""SQLAlchemy models for BluCheck.

An inspection has exactly two captures (exterior and interior); this is enforced in the
API layer and by a unique (inspection_id, kind) constraint here. Frames carry a unique
(capture_id, seq) so re-processing a video cannot duplicate rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# ----- Enumerated value sets (rendered as VARCHAR + CHECK, portable across DBs) -----
ROLES = ("driver", "admin")
INSPECTION_STATUSES = (
    "uploading",
    "processing",
    "pending",
    "approved",
    "rejected",
    "failed",
)
CAPTURE_KINDS = ("exterior", "interior")
CAPTURE_STATUSES = ("uploading", "uploaded", "processing", "extracted", "failed")
REVIEW_ACTIONS = ("approve", "reject")


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    # Drivers log in with their car number; admins with email.
    car_number: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    push_token: Mapped[str | None] = mapped_column(String(512))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (CheckConstraint("role IN ('driver','admin')", name="ck_users_role"),)


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    registration_plate: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, index=True
    )
    model: Mapped[str | None] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Inspection(Base):
    __tablename__ = "inspections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicles.id"), nullable=False, index=True
    )
    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="uploading", index=True
    )

    gps_lat: Mapped[float | None] = mapped_column(Float)
    gps_lon: Mapped[float | None] = mapped_column(Float)
    gps_accuracy_m: Mapped[float | None] = mapped_column(Float)
    captured_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    captured_at_local: Mapped[str | None] = mapped_column(String(64))
    device_meta: Mapped[dict | None] = mapped_column(JSONB)

    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(Text)
    # If this inspection is a re-clean of a previously rejected one, its id.
    reinspection_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inspections.id", ondelete="SET NULL"), index=True
    )
    # Plate OCR integrity check performed before recording (soft-flagged).
    ocr_plate: Mapped[str | None] = mapped_column(String(32))
    ocr_matched: Mapped[bool | None] = mapped_column(Boolean)
    # Fraud/integrity result computed at decision time: {risk, reasons, signals}.
    integrity: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    captures: Mapped[list["Capture"]] = relationship(
        back_populates="inspection", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('uploading','processing','pending','approved','rejected','failed')",
            name="ck_inspections_status",
        ),
    )


class Capture(Base):
    __tablename__ = "captures"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    inspection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inspections.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    video_s3_key: Mapped[str] = mapped_column(String(512), nullable=False)
    duration_s: Mapped[float | None] = mapped_column(Float)
    recorded_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gps_lat: Mapped[float | None] = mapped_column(Float)
    gps_lon: Mapped[float | None] = mapped_column(Float)
    resolution: Mapped[str | None] = mapped_column(String(32))
    frame_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="uploading")

    inspection: Mapped["Inspection"] = relationship(back_populates="captures")
    frames: Mapped[list["Frame"]] = relationship(
        back_populates="capture", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("inspection_id", "kind", name="uq_capture_inspection_kind"),
        CheckConstraint("kind IN ('exterior','interior')", name="ck_captures_kind"),
    )


class Frame(Base):
    __tablename__ = "frames"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    capture_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("captures.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    offset_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    absolute_ts_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gps_lat: Mapped[float | None] = mapped_column(Float)
    gps_lon: Mapped[float | None] = mapped_column(Float)
    s3_key_full: Mapped[str] = mapped_column(String(512), nullable=False)
    s3_key_thumb: Mapped[str] = mapped_column(String(512), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)

    # Frame-selection outputs (Phase 2) and optional zone classification.
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    blur_score: Mapped[float | None] = mapped_column(Float)
    exposure_score: Mapped[float | None] = mapped_column(Float)
    phash: Mapped[str | None] = mapped_column(String(32))
    zone_key: Mapped[str | None] = mapped_column(String(32))

    capture: Mapped["Capture"] = relationship(back_populates="frames")

    __table_args__ = (UniqueConstraint("capture_id", "seq", name="uq_frame_capture_seq"),)


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    inspection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inspections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    admin_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    # source: human review, or human agreeing with / overriding a model verdict.
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="human")
    scoring_result_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scoring_results.id")
    )
    # Frame ids the reviewer actually opened before deciding (ground-truth signal).
    viewed_frame_ids: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    zone_labels: Mapped[list["ReviewZoneLabel"]] = relationship(
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("action IN ('approve','reject')", name="ck_reviews_action"),
        CheckConstraint(
            "source IN ('human','model_confirmed','model_overridden')", name="ck_reviews_source"
        ),
    )


ZONE_KEYS = (
    "exterior_body",
    "windows_glass",
    "seats",
    "floor_mats",
    "dashboard_console",
    "boot",
)
ISSUE_KEYS = ("trash", "stain", "dust", "smudge", "spill", "mud")
MODEL_MODES = ("shadow", "assist", "auto", "disabled")
SCORING_DECISIONS = ("none", "auto_approve", "auto_reject", "route_human")


class TaxonomyZone(Base):
    __tablename__ = "taxonomy_zones"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TaxonomyIssue(Base):
    __tablename__ = "taxonomy_issues"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    vlm_model: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    # Decision-layer config (overall bands, per-zone) read by the backend agent.
    thresholds: Mapped[dict | None] = mapped_column(JSONB)
    # Scoring-layer math (blend weight, zone weights, severity caps, borderline/zoom, image
    # cap) read by the worker. Null => worker falls back to its hardcoded defaults.
    scoring_config: Mapped[dict | None] = mapped_column(JSONB)
    # Confidence->empirical-correctness reliability curve, built offline from human reviews.
    calibration: Mapped[dict | None] = mapped_column(JSONB)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="shadow")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    __table_args__ = (
        CheckConstraint(
            "mode IN ('shadow','assist','auto','disabled')", name="ck_model_versions_mode"
        ),
    )


class ScoringResult(Base):
    __tablename__ = "scoring_results"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    inspection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inspections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_versions.id"), nullable=False
    )
    overall_score: Mapped[float | None] = mapped_column(Float)
    overall_confidence: Mapped[float | None] = mapped_column(Float)
    decision: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    raw_json: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    zone_scores: Mapped[list["ZoneScore"]] = relationship(cascade="all, delete-orphan")
    __table_args__ = (
        UniqueConstraint("inspection_id", "model_version_id", name="uq_scoring_inspection_model"),
        CheckConstraint(
            "decision IN ('none','auto_approve','auto_reject','route_human')",
            name="ck_scoring_decision",
        ),
    )


class ZoneScore(Base):
    __tablename__ = "zone_scores"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    scoring_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scoring_results.id", ondelete="CASCADE"), nullable=False, index=True
    )
    zone_key: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    issues: Mapped[list | None] = mapped_column(JSONB)


class ReviewZoneLabel(Base):
    __tablename__ = "review_zone_labels"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False, index=True
    )
    zone_key: Mapped[str] = mapped_column(String(32), nullable=False)
    issue_key: Mapped[str] = mapped_column(String(32), nullable=False)


class ApiKey(Base):
    """A third-party integration key. Only the hash is stored; the plaintext is shown once."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
