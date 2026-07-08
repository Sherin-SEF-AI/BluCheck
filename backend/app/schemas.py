"""Pydantic v2 request and response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ----- Auth -----
class LoginRequest(BaseModel):
    password: str
    # Drivers log in with car_number, admins with email. `username` accepts either.
    email: str | None = None
    car_number: str | None = None
    username: str | None = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    name: str
    car_number: str | None = None


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    car_number: str = Field(min_length=2, max_length=32)
    # Drivers authenticate with a 4-digit PIN (stored hashed) instead of a full password.
    pin: str = Field(pattern=r"^\d{4}$")


class PlateResolveRequest(BaseModel):
    image_b64: str


class PlateResolveResponse(BaseModel):
    car_number: str
    name: str


class PinLoginRequest(BaseModel):
    car_number: str = Field(min_length=2, max_length=32)
    pin: str = Field(pattern=r"^\d{4}$")


class PushTokenRequest(BaseModel):
    push_token: str


class TestPushRequest(BaseModel):
    car_number: str


class TestPushResponse(BaseModel):
    car_number: str
    driver: str | None
    had_token: bool
    status: str  # ok | unregistered | error | skipped (skipped => no token registered)
    detail: str


# ----- Vehicles -----
class VehicleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    registration_plate: str
    model: str | None = None
    active: bool


# ----- GPS / inspection creation -----
class GpsIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)


class InspectionCreate(BaseModel):
    vehicle_id: uuid.UUID
    gps: GpsIn
    captured_at_utc: datetime
    captured_at_local: str | None = None
    device_meta: dict[str, Any] | None = None
    ocr_plate: str | None = None
    ocr_matched: bool | None = None
    reinspection_of: uuid.UUID | None = None


class PlateVerifyRequest(BaseModel):
    image_b64: str


class PlateVerifyResponse(BaseModel):
    read_plate: str | None
    matched: bool
    expected: str | None
    candidates: list[str]


class InspectionCreated(BaseModel):
    inspection_id: uuid.UUID
    status: str


# ----- Upload -----
class UploadUrlRequest(BaseModel):
    content_type: str = "video/mp4"
    part_count: int = Field(ge=1, le=10000)
    # Provide to resume an existing multipart upload instead of starting a new one.
    upload_id: str | None = None


class PresignedPart(BaseModel):
    part_number: int
    url: str


class UploadUrlResponse(BaseModel):
    key: str
    upload_id: str
    part_size: int
    parts: list[PresignedPart]


class CompletedPart(BaseModel):
    part_number: int = Field(ge=1)
    etag: str


class CompleteUploadRequest(BaseModel):
    upload_id: str
    parts: list[CompletedPart]
    duration_s: float | None = None
    recorded_at_utc: datetime | None = None
    gps: GpsIn | None = None
    resolution: str | None = None


class CaptureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    status: str
    duration_s: float | None = None
    recorded_at_utc: datetime | None = None
    resolution: str | None = None
    frame_count: int


class CompleteUploadResponse(BaseModel):
    capture: CaptureOut
    inspection_status: str


# ----- Taxonomy -----
class TaxonomyItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    key: str
    label: str


class ZoneIssueLabel(BaseModel):
    zone_key: str
    issue_key: str


# ----- Review -----
class ReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    reason: str | None = None
    # Required on reject: one or more structured zone/issue labels (ground truth).
    labels: list[ZoneIssueLabel] = []
    # Frame ids the reviewer opened before deciding.
    viewed_frame_ids: list[uuid.UUID] = []
    # When confirming/overriding a model verdict, the scoring result it relates to.
    scoring_result_id: uuid.UUID | None = None


# ----- Detail / listing -----
class FrameOut(BaseModel):
    id: uuid.UUID
    seq: int
    offset_ms: int
    absolute_ts_utc: datetime | None
    gps_lat: float | None
    gps_lon: float | None
    thumb_url: str
    full_url_endpoint: str
    width: int | None
    height: int | None
    selected: bool = False
    blur_score: float | None = None


class CaptureDetail(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    duration_s: float | None
    recorded_at_utc: datetime | None
    gps_lat: float | None
    gps_lon: float | None
    resolution: str | None
    frame_count: int
    frames: list[FrameOut]


class InspectionListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: str
    vehicle_plate: str
    driver_name: str
    gps_lat: float | None
    gps_lon: float | None
    captured_at_utc: datetime | None
    created_at: datetime
    # Who reached the current status: "agent", "human", or None (not yet decided).
    decision_source: str | None = None
    overall_score: float | None = None


class InspectionListResponse(BaseModel):
    items: list[InspectionListItem]
    total: int
    page: int
    page_size: int


class InspectionDetail(BaseModel):
    id: uuid.UUID
    status: str
    vehicle_id: uuid.UUID
    vehicle_plate: str
    driver_id: uuid.UUID
    driver_name: str
    gps_lat: float | None
    gps_lon: float | None
    gps_accuracy_m: float | None
    captured_at_utc: datetime | None
    captured_at_local: str | None
    device_meta: dict[str, Any] | None
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    reject_reason: str | None
    reject_labels: list[ZoneIssueLabel] = []
    scoring: dict[str, Any] | None = None  # populated once the VLM scoring stage is live
    decision_source: str | None = None  # "agent" | "human" | None
    ocr_plate: str | None = None
    ocr_matched: bool | None = None
    reinspection_of: uuid.UUID | None = None
    reinspection_of_reason: str | None = None  # the prior rejection's reasons, for context
    created_at: datetime
    captures: list[CaptureDetail]


class FrameUrlResponse(BaseModel):
    url: str
    expires_in: int


# ----- Metrics -----
class MetricsSummary(BaseModel):
    counts_by_status: dict[str, int]
    average_review_seconds: float | None
    rejects_by_vehicle: list[dict[str, Any]]


class MetricsTrends(BaseModel):
    reviews_by_day: list[dict[str, Any]]  # {day, approved, rejected}
    per_driver: list[dict[str, Any]]  # {driver, total, approved, rejected, approval_rate}
    average_review_seconds: float | None


# ----- Daily inspection compliance -----
class ComplianceDriver(BaseModel):
    driver_id: uuid.UUID
    name: str
    car_number: str | None
    inspected: bool
    last_inspection_at: datetime | None
    last_status: str | None


class ComplianceResponse(BaseModel):
    date: str  # IST date being reported
    total_drivers: int
    inspected_count: int
    missing_count: int
    rate: float | None  # inspected / total
    drivers: list[ComplianceDriver]


# ----- Per-vehicle cleanliness trends -----
class VehicleTrend(BaseModel):
    vehicle_id: uuid.UUID
    plate: str
    model: str | None
    active: bool
    total: int
    approved: int
    rejected: int
    pending: int
    avg_score: float | None
    last_score: float | None
    last_status: str | None
    last_decided_by: str | None
    last_inspected_at: datetime | None


class VehicleTrendsResponse(BaseModel):
    vehicles: list[VehicleTrend]


# ----- Vehicle admin CRUD -----
class VehicleCreate(BaseModel):
    registration_plate: str = Field(min_length=1, max_length=32)
    model: str | None = None
    active: bool = True


class VehicleUpdate(BaseModel):
    registration_plate: str | None = Field(default=None, max_length=32)
    model: str | None = None
    active: bool | None = None


# ----- User admin CRUD -----
class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: Literal["driver", "admin"]


class UserUpdate(BaseModel):
    name: str | None = None
    active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    email: str | None = None
    car_number: str | None = None
    role: str
    active: bool
    created_at: datetime


# ----- Audit -----
class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    actor_id: uuid.UUID | None
    action: str
    entity: str
    entity_id: str
    detail: dict[str, Any] | None
    created_at: datetime


class AuditListResponse(BaseModel):
    items: list[AuditOut]
    total: int
    page: int
    page_size: int


# ----- Model management + scoring -----
class ModelVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    vlm_model: str | None
    prompt_version: str | None
    thresholds: dict[str, Any] | None
    mode: str
    active: bool
    created_at: datetime


class ModelModeRequest(BaseModel):
    mode: Literal["shadow", "assist", "auto", "disabled"]


class ModelThresholdsRequest(BaseModel):
    # e.g. {"overall": {"auto_approve": 85, "auto_reject": 40}, "per_zone": {"seats": {...}}}
    thresholds: dict[str, Any]


class ScoringConfigRequest(BaseModel):
    # Partial override of the scoring math; keys absent fall back to worker defaults.
    # e.g. {"blend_mean_weight": 0.6, "zone_weight": {"seats": 1.6}, "max_images_per_call": 6}
    scoring_config: dict[str, Any]


class ScoringConfigResponse(BaseModel):
    # The effective config (stored override merged over defaults) plus the raw stored override
    # and the defaults, so the admin UI can show what is set vs. inherited.
    effective: dict[str, Any]
    stored: dict[str, Any] | None
    defaults: dict[str, Any]


class CalibrateRequest(BaseModel):
    days: int | None = None  # window of human-reviewed inspections to fit on (default all)


class CalibrationResponse(BaseModel):
    n_samples: int
    base_rate: float | None
    min_bin_support: int
    bins: list[dict[str, Any]]
    built_at: str


class ValidationReport(BaseModel):
    window_days: int | None
    n_reviewed: int
    agreement_rate: float | None
    confusion: dict[str, int]           # dirty(reject)=positive: tp/tn/fp/fn
    false_approve_rate: float | None    # missed-dirty rate = fn/(fn+tp) -- the dangerous one
    false_reject_rate: float | None     # falsely-rejected-clean rate = fp/(fp+tn)
    per_zone: list[dict[str, Any]]      # zone_key, precision, recall, n
    note: str | None = None


class RecommendThresholdsRequest(BaseModel):
    days: int | None = None
    max_false_approve_rate: float = 0.05  # ceiling on missed-dirty rate
    sweep_blend: bool = False             # also sweep the mean/worst blend (recomputes from zones)


class RecommendThresholdsResponse(BaseModel):
    n_reviewed: int
    current: dict[str, Any]
    recommended: dict[str, Any] | None
    evaluated: int
    note: str | None = None


class ZoneScoreOut(BaseModel):
    zone_key: str
    score: float | None
    confidence: float | None
    issues: list[dict[str, Any]] | None


class ScoringOut(BaseModel):
    id: uuid.UUID
    model_version_id: uuid.UUID
    model_name: str | None = None
    overall_score: float | None
    overall_confidence: float | None
    decision: str
    created_at: datetime
    zones: list[ZoneScoreOut]


class AgentActivityItem(BaseModel):
    inspection_id: uuid.UUID
    vehicle_plate: str
    driver_name: str
    status: str
    decision_source: str | None  # agent | human | None
    overall_score: float | None
    overall_confidence: float | None
    reasons: list[ZoneIssueLabel]
    created_at: datetime
    reviewed_at: datetime | None


class AgentSummary(BaseModel):
    mode: str
    model_name: str | None
    online: bool  # recent successful scoring seen
    auto_approved: int
    auto_rejected: int
    escalated: int  # scored but routed to a human (uncertain band)
    awaiting_human: int  # pending inspections a human still needs to look at
    scored_total: int
    avg_latency_ms: float | None


class AgentActivityResponse(BaseModel):
    summary: AgentSummary
    items: list[AgentActivityItem]


class RunPendingResponse(BaseModel):
    approved: int
    rejected: int
    escalated: int
    scored_missing: int


class ModelPerformance(BaseModel):
    mode: str
    thresholds: dict[str, Any] | None
    model_name: str | None
    total_scored: int
    total_with_human: int
    agreement_rate: float | None
    per_zone_agreement: list[dict[str, Any]]
    confusion: dict[str, int]  # tp/tn/fp/fn on the approve/reject axis
    avg_confidence_agree: float | None
    avg_confidence_disagree: float | None
    agreement_by_day: list[dict[str, Any]]
    avg_latency_ms: float | None
    # Two-layer auditability: how often the LLM supervisor overrode the deterministic band, and
    # whether the override was right vs. the eventual human outcome.
    overrides: dict[str, Any] | None = None
