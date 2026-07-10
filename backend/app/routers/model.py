"""Model management and performance: mode (incl. kill switch), thresholds, and the
model-versus-human agreement surface used to decide when to leave shadow mode.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import boto3
import requests
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import agent, audit
from ..auth import require_admin
from ..config import get_settings
from ..db import get_db
from ..modelcfg import ensure_active_model_version
from ..models import (
    AuditLog,
    Inspection,
    ModelVersion,
    Review,
    ReviewZoneLabel,
    ScoringResult,
    User,
    Vehicle,
    ZoneScore,
)
from .. import calibration as calib
from .. import health as vision_health
from .. import scoring_defaults, scoring_math, sop as sop_gen, tuning_ai
from ..schemas import (
    AgentActivityItem,
    AgentActivityResponse,
    AgentSummary,
    CalibrateRequest,
    CalibrationResponse,
    ModelModeRequest,
    ModelPerformance,
    ModelThresholdsRequest,
    ModelVersionOut,
    PolicyListResponse,
    PolicyMutateResponse,
    PolicyOut,
    PolicySaveRequest,
    PolicyUpdateRequest,
    RecommendedSop,
    RecommendThresholdsRequest,
    RecommendThresholdsResponse,
    RunPendingResponse,
    ScoringConfigRequest,
    ScoringConfigResponse,
    SopApplyRequest,
    SopGenerateRequest,
    SopProposal,
    TuningEvidence,
    TuningSuggestion,
    VisionIncidentRequest,
    ValidationReport,
    ZoneIssueLabel,
)

router = APIRouter(prefix="/model", tags=["model"])
logger = logging.getLogger("blucheck.model")

# Shared secret used only for worker->backend internal calls. Stored alongside the Groq
# inference config (in the secret still named "<prefix>/runpod") so both the API and the
# worker can see it without extra IAM. Read lazily; a failed read is never cached.
_INTERNAL: dict = {}


def _internal_token() -> str | None:
    if "tok" not in _INTERNAL:
        try:
            sm = boto3.client("secretsmanager", region_name=get_settings().aws_region)
            cfg = json.loads(sm.get_secret_value(SecretId="blucheck/runpod")["SecretString"])
            _INTERNAL["tok"] = cfg.get("internal_token")
        except Exception:  # noqa: BLE001 - internal auth simply unavailable until configured
            return None
    return _INTERNAL.get("tok")


def require_internal(authorization: str | None = Header(default=None)) -> None:
    tok = _internal_token()
    if not tok or authorization != f"Bearer {tok}":
        raise HTTPException(status_code=401, detail="internal auth required")


# Real model reachability, cached briefly so /activity does not ping Groq every call.
_health: dict = {"at": None, "online": False}


def _model_online() -> bool:
    now = datetime.now(timezone.utc)
    if _health["at"] and (now - _health["at"]).total_seconds() < 120:
        return _health["online"]
    online = False
    try:
        sm = boto3.client("secretsmanager", region_name=get_settings().aws_region)
        cfg = json.loads(sm.get_secret_value(SecretId="blucheck/runpod")["SecretString"])
        if cfg.get("groq_api_key"):
            base = cfg.get("groq_base_url", "https://api.groq.com/openai/v1").rstrip("/")
            r = requests.get(f"{base}/models", headers={"Authorization": f"Bearer {cfg['groq_api_key']}"}, timeout=4)
            online = r.status_code == 200
    except Exception:  # noqa: BLE001 - reachability check must never error the endpoint
        online = False
    _health.update(at=now, online=online)
    return online


@router.post("/agent-decide/{inspection_id}")
def agent_decide(
    inspection_id: uuid.UUID,
    _: None = Depends(require_internal),
    db: Session = Depends(get_db),
):
    """Single decision entry point. The worker calls this after scoring, so the agent's
    decision + driver notification happen in exactly one place (this backend), never in the
    worker. Applies the current mode/thresholds to the inspection's latest scoring result.
    """
    mv = ensure_active_model_version(db)
    insp = db.get(Inspection, inspection_id)
    if insp is None:
        raise HTTPException(status_code=404, detail="Inspection not found")
    sr = db.execute(
        select(ScoringResult)
        .where(ScoringResult.inspection_id == inspection_id)
        .order_by(ScoringResult.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if sr is None:
        return {"decision": "none", "acted": False}
    return agent.apply_decision(db, insp, sr, mv)


@router.get("/health")
def model_health(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Vision-model health: whether scoring is currently working, and the last failure if any."""
    return vision_health.status(db)


@router.post("/vision-incident")
def vision_incident(
    body: VisionIncidentRequest, _: None = Depends(require_internal), db: Session = Depends(get_db)
) -> dict:
    """Internal: the worker reports a vision-model failure here so it surfaces on the dashboard."""
    vision_health.record_incident(db, body.source, body.model, body.message)
    return {"ok": True}


@router.get("/version", response_model=ModelVersionOut)
def current_version(_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return ensure_active_model_version(db)


@router.post("/mode", response_model=ModelVersionOut)
def set_mode(
    body: ModelModeRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    mv = ensure_active_model_version(db)
    prev = mv.mode
    mv.mode = body.mode  # 'disabled' is the kill switch: reverts to human-only instantly
    audit.record(
        db,
        actor_id=admin.id,
        action="model_set_mode",
        entity="model_version",
        entity_id=str(mv.id),
        detail={"from": prev, "to": body.mode},
    )
    db.commit()
    db.refresh(mv)
    logger.info("model_mode_changed from=%s to=%s by=%s", prev, body.mode, admin.id)
    # Turning autonomy on immediately works the existing backlog of scored inspections.
    if body.mode == "auto":
        counts = agent.process_pending(db, mv)
        logger.info("mode->auto processed backlog: %s", counts)
    return mv


@router.post("/run-pending", response_model=RunPendingResponse)
def run_pending(_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Have the agent work the current backlog of already-scored pending inspections."""
    mv = ensure_active_model_version(db)
    counts = agent.process_pending(db, mv)
    return RunPendingResponse(**counts)


def _reasons_for(sr_id, db: Session) -> list[ZoneIssueLabel]:
    return [ZoneIssueLabel(**r) for r in agent.zone_reasons(sr_id, db)]


@router.get("/activity", response_model=AgentActivityResponse)
def activity(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    limit: int = 40,
):
    """Agent control-centre feed: summary tiles + recent decisions with full reasoning.

    All counts are computed with SQL aggregates (not by loading every inspection) so the
    endpoint stays fast at fleet scale.
    """
    mv = ensure_active_model_version(db)

    # ---- Summary tiles via aggregate counts ----
    def _count(*where):
        return db.execute(select(func.count()).select_from(Inspection).where(*where)).scalar_one()

    is_agent = (Inspection.reviewed_by.is_(None)) & (Inspection.reviewed_at.is_not(None))
    auto_approved = _count(Inspection.status == "approved", is_agent)
    auto_rejected = _count(Inspection.status == "rejected", is_agent)
    awaiting_human = _count(Inspection.status == "pending")
    scored_total = db.execute(
        select(func.count(func.distinct(ScoringResult.inspection_id)))
    ).scalar_one()
    # Escalated = pending inspections that already have a scoring result.
    escalated = db.execute(
        select(func.count(func.distinct(ScoringResult.inspection_id)))
        .select_from(ScoringResult)
        .join(Inspection, Inspection.id == ScoringResult.inspection_id)
        .where(Inspection.status == "pending")
    ).scalar_one()

    # Average latency from a recent sample of scoring results (latency lives in raw_json).
    recent_srs = list(
        db.execute(select(ScoringResult).order_by(ScoringResult.created_at.desc()).limit(200)).scalars()
    )
    latencies = [
        r.raw_json["latency_ms"]
        for r in recent_srs
        if isinstance(getattr(r, "raw_json", None), dict) and isinstance(r.raw_json.get("latency_ms"), (int, float))
    ]

    # ---- Feed: most-recent scored inspections only (bounded) ----
    latest = (
        select(ScoringResult.inspection_id.label("iid"), func.max(ScoringResult.created_at).label("mx"))
        .group_by(ScoringResult.inspection_id)
        .subquery()
    )
    feed_rows = list(
        db.execute(
            select(ScoringResult, Inspection, Vehicle.registration_plate, User.name)
            .join(latest, (ScoringResult.inspection_id == latest.c.iid) & (ScoringResult.created_at == latest.c.mx))
            .join(Inspection, Inspection.id == ScoringResult.inspection_id)
            .join(Vehicle, Vehicle.id == Inspection.vehicle_id)
            .join(User, User.id == Inspection.driver_id)
            .order_by(Inspection.created_at.desc())
            .limit(limit)
        ).all()
    )
    items = [
        AgentActivityItem(
            inspection_id=insp.id,
            vehicle_plate=plate,
            driver_name=driver_name,
            status=insp.status,
            decision_source=agent.decision_source(insp),
            overall_score=sr.overall_score,
            overall_confidence=sr.overall_confidence,
            reasons=_reasons_for(sr.id, db),
            created_at=insp.created_at,
            reviewed_at=insp.reviewed_at,
        )
        for sr, insp, plate, driver_name in feed_rows
    ]

    summary = AgentSummary(
        mode=mv.mode,
        model_name=mv.vlm_model,
        online=_model_online(),
        auto_approved=auto_approved,
        auto_rejected=auto_rejected,
        escalated=escalated,
        awaiting_human=awaiting_human,
        scored_total=scored_total,
        avg_latency_ms=round(sum(latencies) / len(latencies), 1) if latencies else None,
    )
    return AgentActivityResponse(summary=summary, items=items)


@router.post("/thresholds", response_model=ModelVersionOut)
def set_thresholds(
    body: ModelThresholdsRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    mv = ensure_active_model_version(db)
    mv.thresholds = body.thresholds
    audit.record(
        db,
        actor_id=admin.id,
        action="model_set_thresholds",
        entity="model_version",
        entity_id=str(mv.id),
        detail={"thresholds": body.thresholds},
    )
    db.commit()
    db.refresh(mv)
    return mv


@router.get("/scoring-config", response_model=ScoringConfigResponse)
def get_scoring_config(_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """The scoring-layer math for the active model version: the effective config (stored
    override merged over defaults), the raw stored override, and the defaults."""
    mv = ensure_active_model_version(db)
    stored = mv.scoring_config
    return ScoringConfigResponse(
        effective=scoring_defaults.resolve(stored),
        stored=stored,
        defaults=scoring_defaults.DEFAULTS,
    )


@router.patch("/scoring-config", response_model=ScoringConfigResponse)
def set_scoring_config(
    body: ScoringConfigRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """Set the scoring-layer math for the active model version (admin-only, audited). Only known
    keys are kept; the worker merges this over its defaults, so a partial config is fine and any
    unknown key is ignored. This is the scoring counterpart to /model/thresholds and stays
    separate from decision-layer bands so the two control surfaces never entangle."""
    mv = ensure_active_model_version(db)
    # Keep only recognized keys so the stored config can never inject unknown behavior; merge
    # over the existing stored config so partial PATCHes accumulate.
    incoming = {k: v for k, v in body.scoring_config.items() if k in scoring_defaults.DEFAULTS}
    merged_store = {**(mv.scoring_config or {}), **incoming}
    prev = mv.scoring_config
    mv.scoring_config = merged_store
    audit.record(
        db,
        actor_id=admin.id,
        action="model_set_scoring_config",
        entity="model_version",
        entity_id=str(mv.id),
        detail={"from": prev, "to": merged_store},
    )
    db.commit()
    db.refresh(mv)
    logger.info("scoring_config updated by=%s keys=%s", admin.id, list(incoming))
    return ScoringConfigResponse(
        effective=scoring_defaults.resolve(mv.scoring_config),
        stored=mv.scoring_config,
        defaults=scoring_defaults.DEFAULTS,
    )


@router.post("/calibrate", response_model=CalibrationResponse)
def calibrate(
    body: CalibrateRequest | None = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Build the confidence->correctness reliability curve for the active model version from its
    scored inspections that have a human review, and persist it to ModelVersion.calibration. This
    is the same gold-signal join as /performance. Report-only: it does not change the mode. Once
    built, the decision layer gates auto actions on calibrated confidence (see agent.py)."""
    mv = ensure_active_model_version(db)
    days = (body.days if body else None)
    q = select(ScoringResult).where(ScoringResult.model_version_id == mv.id)
    if days:
        q = q.where(ScoringResult.created_at >= datetime.now(timezone.utc) - timedelta(days=days))
    results = list(db.execute(q).scalars())
    insp_ids = {r.inspection_id for r in results}

    human_action: dict = {}
    if insp_ids:
        for r in db.execute(
            select(Review).where(Review.inspection_id.in_(insp_ids)).order_by(Review.created_at)
        ).scalars():
            human_action[r.inspection_id] = r.action  # last (asc order) wins

    pairs: list[tuple[float, bool]] = []
    for res in results:
        ha = human_action.get(res.inspection_id)
        if ha is None or res.overall_confidence is None:
            continue
        mverdict = _model_verdict(res.overall_score, mv.thresholds or {})
        if mverdict is None:
            continue
        pairs.append((float(res.overall_confidence), mverdict == ha))

    curve = calib.build_curve(pairs)
    built_at = datetime.now(timezone.utc).isoformat()
    curve["built_at"] = built_at
    curve["model_version_id"] = str(mv.id)
    mv.calibration = curve
    audit.record(
        db,
        actor_id=admin.id,
        action="model_calibrate",
        entity="model_version",
        entity_id=str(mv.id),
        detail={"n_samples": curve["n_samples"], "base_rate": curve["base_rate"], "days": days},
    )
    db.commit()
    logger.info("calibration built mv=%s n=%s base_rate=%s", mv.id, curve["n_samples"], curve["base_rate"])
    return CalibrationResponse(
        n_samples=curve["n_samples"],
        base_rate=curve["base_rate"],
        min_bin_support=curve["min_bin_support"],
        bins=curve["bins"],
        built_at=built_at,
    )


@router.post("/sop/generate", response_model=SopProposal)
def sop_generate(
    body: SopGenerateRequest, _admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """Agentic SOP generator: translate a natural-language cleanliness policy into a proposed
    scoring config + thresholds. PREVIEW ONLY -- the admin reviews and applies separately."""
    mv = ensure_active_model_version(db)
    current = {
        **scoring_defaults.resolve(mv.scoring_config),
        "thresholds": mv.thresholds,
    }
    proposal = sop_gen.generate(body.sop, current)
    if proposal is None:
        raise HTTPException(status_code=502, detail="Could not generate an SOP right now; try again.")
    return SopProposal(**proposal)


@router.post("/sop/apply", response_model=ScoringConfigResponse)
def sop_apply(
    body: SopApplyRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """Apply a reviewed SOP: set the scoring config + thresholds and record the policy text.
    Only recognized scoring keys are kept. Audited."""
    mv = ensure_active_model_version(db)
    incoming = {k: v for k, v in (body.scoring_config or {}).items() if k in scoring_defaults.DEFAULTS}
    mv.scoring_config = {**(mv.scoring_config or {}), **incoming, "_sop": body.sop[:2000]}
    ov = ((body.thresholds or {}).get("overall") or {})
    thresholds = {**(mv.thresholds or {})}
    thresholds["overall"] = {**(thresholds.get("overall") or {}), **{k: ov[k] for k in ("auto_approve", "auto_reject") if k in ov}}
    mv.thresholds = thresholds
    audit.record(
        db, actor_id=admin.id, action="model_apply_sop", entity="model_version",
        entity_id=str(mv.id), detail={"sop": body.sop[:500], "scoring_config": incoming, "thresholds": ov},
    )
    db.commit()
    db.refresh(mv)
    logger.info("SOP applied by=%s", admin.id)
    return ScoringConfigResponse(
        effective=scoring_defaults.resolve(mv.scoring_config),
        stored=mv.scoring_config,
        defaults=scoring_defaults.DEFAULTS,
    )


# ----- Saved policy library (edit/delete/activate + recommended templates) -----
# Policies are stored inside the active model version's scoring_config under the "_policies"
# key, with "_active_policy" holding the active policy id. Both keys are ignored by the worker
# (scoring_defaults.resolve keeps only known scoring knobs), so no schema change is needed.


def _policies_of(mv) -> list[dict]:
    return [dict(p) for p in ((mv.scoring_config or {}).get("_policies") or []) if isinstance(p, dict)]


def _active_policy_id(mv) -> str | None:
    return (mv.scoring_config or {}).get("_active_policy")


def _norm_thresholds(thresholds: dict | None) -> dict:
    ov = ((thresholds or {}).get("overall") or {})
    keep = {}
    for k in ("auto_approve", "auto_reject"):
        if k in ov:
            try:
                keep[k] = int(ov[k])
            except (TypeError, ValueError):
                pass
    return {"overall": keep}


def _apply_config_to_mv(mv, scoring_config: dict, thresholds: dict, sop: str) -> None:
    """Copy a policy's scoring knobs + overall thresholds onto the live model version."""
    incoming = {k: v for k, v in (scoring_config or {}).items() if k in scoring_defaults.DEFAULTS}
    cfg = {**(mv.scoring_config or {}), **incoming, "_sop": (sop or "")[:2000]}
    mv.scoring_config = cfg
    ov = _norm_thresholds(thresholds)["overall"]
    th = {**(mv.thresholds or {})}
    th["overall"] = {**(th.get("overall") or {}), **ov}
    mv.thresholds = th


def _set_library(mv, policies: list[dict], active_id: str | None) -> None:
    cfg = {**(mv.scoring_config or {}), "_policies": policies, "_active_policy": active_id}
    mv.scoring_config = cfg


def _policy_out(p: dict, active_id: str | None) -> PolicyOut:
    return PolicyOut(
        id=p.get("id", ""),
        name=p.get("name", "Untitled"),
        sop=p.get("sop", ""),
        scoring_config=p.get("scoring_config") or {},
        thresholds=p.get("thresholds") or {},
        summary=p.get("summary", "") or "",
        active=(p.get("id") == active_id),
        created_at=p.get("created_at"),
        updated_at=p.get("updated_at"),
    )


def _mutate_response(mv) -> PolicyMutateResponse:
    active_id = _active_policy_id(mv)
    return PolicyMutateResponse(
        policies=[_policy_out(p, active_id) for p in _policies_of(mv)], active_id=active_id
    )


@router.get("/policies", response_model=PolicyListResponse)
def list_policies(_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """The saved policy library, the active policy id, and the recommended starter templates."""
    mv = ensure_active_model_version(db)
    active_id = _active_policy_id(mv)
    return PolicyListResponse(
        policies=[_policy_out(p, active_id) for p in _policies_of(mv)],
        active_id=active_id,
        recommended=[RecommendedSop(**r) for r in sop_gen.RECOMMENDED],
    )


@router.post("/policies", response_model=PolicyMutateResponse)
def save_policy(
    body: PolicySaveRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """Save a reviewed proposal as a named policy. Optionally activate it immediately."""
    mv = ensure_active_model_version(db)
    pols = _policies_of(mv)
    now = datetime.now(timezone.utc).isoformat()
    pol = {
        "id": uuid.uuid4().hex,
        "name": body.name.strip()[:120],
        "sop": body.sop[:2000],
        "scoring_config": {k: v for k, v in (body.scoring_config or {}).items() if k in scoring_defaults.DEFAULTS},
        "thresholds": _norm_thresholds(body.thresholds),
        "summary": (body.summary or "")[:1200],
        "created_at": now,
        "updated_at": now,
    }
    pols.append(pol)
    active_id = _active_policy_id(mv)
    if body.activate:
        _apply_config_to_mv(mv, pol["scoring_config"], pol["thresholds"], pol["sop"])
        active_id = pol["id"]
    _set_library(mv, pols, active_id)
    audit.record(
        db, actor_id=admin.id, action="policy_save", entity="model_version",
        entity_id=str(mv.id), detail={"policy_id": pol["id"], "name": pol["name"], "activated": body.activate},
    )
    db.commit()
    db.refresh(mv)
    return _mutate_response(mv)


@router.put("/policies/{pid}", response_model=PolicyMutateResponse)
def update_policy(
    pid: str, body: PolicyUpdateRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """Edit a saved policy. If it is the active one, its live config is re-applied."""
    mv = ensure_active_model_version(db)
    pols = _policies_of(mv)
    idx = next((i for i, p in enumerate(pols) if p.get("id") == pid), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    p = pols[idx]
    if body.name is not None:
        p["name"] = body.name.strip()[:120] or p.get("name", "Untitled")
    if body.sop is not None:
        p["sop"] = body.sop[:2000]
    if body.scoring_config is not None:
        p["scoring_config"] = {k: v for k, v in body.scoring_config.items() if k in scoring_defaults.DEFAULTS}
    if body.thresholds is not None:
        p["thresholds"] = _norm_thresholds(body.thresholds)
    if body.summary is not None:
        p["summary"] = body.summary[:1200]
    p["updated_at"] = datetime.now(timezone.utc).isoformat()
    pols[idx] = p
    active_id = _active_policy_id(mv)
    if pid == active_id:
        _apply_config_to_mv(mv, p.get("scoring_config") or {}, p.get("thresholds") or {}, p.get("sop") or "")
    _set_library(mv, pols, active_id)
    audit.record(
        db, actor_id=admin.id, action="policy_update", entity="model_version",
        entity_id=str(mv.id), detail={"policy_id": pid, "reapplied": pid == active_id},
    )
    db.commit()
    db.refresh(mv)
    return _mutate_response(mv)


@router.delete("/policies/{pid}", response_model=PolicyMutateResponse)
def delete_policy(
    pid: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """Delete a saved policy. Deleting the active policy leaves the live scoring config in place
    but clears the active pointer (nothing silently changes what the worker uses)."""
    mv = ensure_active_model_version(db)
    pols = _policies_of(mv)
    remaining = [p for p in pols if p.get("id") != pid]
    if len(remaining) == len(pols):
        raise HTTPException(status_code=404, detail="Policy not found")
    active_id = _active_policy_id(mv)
    if active_id == pid:
        active_id = None
    _set_library(mv, remaining, active_id)
    audit.record(
        db, actor_id=admin.id, action="policy_delete", entity="model_version",
        entity_id=str(mv.id), detail={"policy_id": pid},
    )
    db.commit()
    db.refresh(mv)
    return _mutate_response(mv)


@router.post("/policies/{pid}/activate", response_model=PolicyMutateResponse)
def activate_policy(
    pid: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """Make a saved policy the live cleanliness criteria (copies its config onto the worker)."""
    mv = ensure_active_model_version(db)
    pols = _policies_of(mv)
    pol = next((p for p in pols if p.get("id") == pid), None)
    if pol is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    _apply_config_to_mv(mv, pol.get("scoring_config") or {}, pol.get("thresholds") or {}, pol.get("sop") or "")
    _set_library(mv, pols, pid)
    audit.record(
        db, actor_id=admin.id, action="policy_activate", entity="model_version",
        entity_id=str(mv.id), detail={"policy_id": pid, "name": pol.get("name")},
    )
    db.commit()
    db.refresh(mv)
    logger.info("policy activated id=%s by=%s", pid, admin.id)
    return _mutate_response(mv)


@router.get("/tuning-suggestion", response_model=TuningSuggestion)
def tuning_suggestion(
    days: int = 30, _admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """Self-tuning policy agent: analyze where humans overturned the agent and propose a modest
    scoring/threshold adjustment to reduce those disagreements. Preview only -- apply via the
    existing SOP/policy apply path."""
    mv = ensure_active_model_version(db)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    overrides = db.execute(
        select(Review).where(Review.source == "model_overridden", Review.created_at >= cutoff)
    ).scalars().all()
    too_strict = too_lenient = 0
    strict_zones: dict = {}
    lenient_zones: dict = {}
    for r in overrides:
        if not r.scoring_result_id:
            continue
        zrows = db.execute(
            select(ZoneScore).where(ZoneScore.scoring_result_id == r.scoring_result_id)
        ).scalars().all()
        flagged = [z.zone_key for z in zrows if (z.issues or [])]
        if r.action == "approve":  # agent rejected, human approved -> agent too strict
            too_strict += 1
            for zk in flagged:
                strict_zones[zk] = strict_zones.get(zk, 0) + 1
        else:  # agent approved, human rejected -> agent too lenient
            too_lenient += 1
            for zk in flagged:
                lenient_zones[zk] = lenient_zones.get(zk, 0) + 1
    evidence = TuningEvidence(
        days=days, overrides=len(overrides), too_strict=too_strict, too_lenient=too_lenient,
        strict_zones=strict_zones, lenient_zones=lenient_zones,
    )
    if len(overrides) < 4:
        return TuningSuggestion(
            no_change=True, confidence=0.0, evidence=evidence,
            summary=f"Only {len(overrides)} override(s) in the last {days} days -- not enough signal to tune yet. Keep reviewing and check back.",
        )
    current = {
        **scoring_defaults.resolve(mv.scoring_config),
        "thresholds": (mv.thresholds or {}).get("overall", {}),
    }
    out = tuning_ai.suggest(evidence.model_dump(), current)
    if out is None:
        raise HTTPException(status_code=502, detail="Could not generate a tuning suggestion right now; try again.")
    return TuningSuggestion(
        no_change=out["no_change"], confidence=out["confidence"], summary=out["summary"],
        scoring_config=out.get("scoring_config"), thresholds=out.get("thresholds"), evidence=evidence,
    )


def _model_verdict(overall_score: float | None, thresholds: dict) -> str | None:
    """Binary model verdict for agreement scoring: approve if clearly clean, reject if
    clearly dirty. Uses the midpoint of the bands so every scored inspection contributes.
    """
    if overall_score is None:
        return None
    ov = (thresholds or {}).get("overall", {})
    approve = ov.get("auto_approve", 85)
    reject = ov.get("auto_reject", 40)
    mid = (approve + reject) / 2
    return "approve" if overall_score >= mid else "reject"


@router.get("/performance", response_model=ModelPerformance)
def performance(_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    mv = ensure_active_model_version(db)

    # Bound the working set to a recent window so this stays fast at fleet scale.
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)

    results = list(
        db.execute(
            select(ScoringResult)
            .where(ScoringResult.created_at >= cutoff)
            .order_by(ScoringResult.created_at.desc())
            .limit(5000)
        ).scalars()
    )
    total_scored = db.execute(select(func.count()).select_from(ScoringResult)).scalar_one()
    result_ids = {r.id for r in results}
    insp_ids = {r.inspection_id for r in results}

    # Latest human review action per inspection (only for the inspections in scope).
    reviews = list(
        db.execute(
            select(Review).where(Review.inspection_id.in_(insp_ids)).order_by(Review.created_at)
        ).scalars()
    ) if insp_ids else []
    human_action: dict = {}
    for r in reviews:
        human_action[r.inspection_id] = r.action  # last wins (ordered asc)

    # Human zone labels for those reviews.
    review_ids = {r.id for r in reviews}
    labels = list(
        db.execute(select(ReviewZoneLabel).where(ReviewZoneLabel.review_id.in_(review_ids))).scalars()
    ) if review_ids else []
    review_by_id = {r.id: r for r in reviews}
    human_zones: dict = defaultdict(set)
    for l in labels:
        rv = review_by_id.get(l.review_id)
        if rv is not None:
            human_zones[rv.inspection_id].add(l.zone_key)

    # Model zone flags for the scoring results in scope.
    zone_rows = list(
        db.execute(select(ZoneScore).where(ZoneScore.scoring_result_id.in_(result_ids))).scalars()
    ) if result_ids else []
    zones_by_result: dict = defaultdict(list)
    for z in zone_rows:
        zones_by_result[z.scoring_result_id].append(z)

    matches = 0
    considered = 0
    conf_agree: list[float] = []
    conf_disagree: list[float] = []
    by_day: dict = defaultdict(lambda: {"agree": 0, "total": 0})
    per_zone: dict = defaultdict(lambda: {"agree": 0, "total": 0})
    tp = tn = fp = fn = 0
    latencies: list[float] = []

    for res in results:
        if getattr(res, "raw_json", None) and isinstance(res.raw_json, dict):
            lm = res.raw_json.get("latency_ms")
            if isinstance(lm, (int, float)):
                latencies.append(float(lm))
        ha = human_action.get(res.inspection_id)
        if ha is None:
            continue  # no human ground truth yet
        mverdict = _model_verdict(res.overall_score, mv.thresholds or {})
        if mverdict is None:
            continue
        considered += 1
        agree = mverdict == ha
        matches += 1 if agree else 0
        (conf_agree if agree else conf_disagree).append(res.overall_confidence or 0.0)
        day = res.created_at.date().isoformat()
        by_day[day]["total"] += 1
        by_day[day]["agree"] += 1 if agree else 0
        # confusion with "reject" (dirty) as the positive class
        if mverdict == "reject" and ha == "reject":
            tp += 1
        elif mverdict == "approve" and ha == "approve":
            tn += 1
        elif mverdict == "reject" and ha == "approve":
            fp += 1
        else:
            fn += 1

        # Per-zone agreement: model flagged zone vs human labeled zone.
        model_zones = {z.zone_key for z in zones_by_result.get(res.id, []) if (z.issues or [])}
        hz = human_zones.get(res.inspection_id, set())
        for zk in set(model_zones) | set(hz):
            per_zone[zk]["total"] += 1
            per_zone[zk]["agree"] += 1 if (zk in model_zones) == (zk in hz) else 0

    def _avg(xs):
        return round(sum(xs) / len(xs), 3) if xs else None

    # Supervisor overrides in window: how often the LLM overrode a firm band, and whether it was
    # right vs. the eventual human outcome (band_right = would the deterministic band have been).
    override_audits = list(
        db.execute(
            select(AuditLog).where(
                AuditLog.action == "agent_override", AuditLog.created_at >= cutoff
            )
        ).scalars()
    )
    _as_action = {"auto_approve": "approve", "auto_reject": "reject"}
    ov_appr = ov_rej = reviewed = sup_right = band_right = 0
    deltas: list[float] = []
    for a in override_audits:
        det = a.detail or {}
        if det.get("supervisor_decision") == "auto_approve":
            ov_appr += 1
        elif det.get("supervisor_decision") == "auto_reject":
            ov_rej += 1
        if isinstance(det.get("delta"), (int, float)):
            deltas.append(float(det["delta"]))
        try:
            iid = uuid.UUID(a.entity_id)
        except (ValueError, TypeError):
            continue
        ha = human_action.get(iid)
        if ha is None:
            continue
        reviewed += 1
        if _as_action.get(det.get("supervisor_decision")) == ha:
            sup_right += 1
        if _as_action.get(det.get("band_decision")) == ha:
            band_right += 1
    overrides = {
        "count": len(override_audits),
        "approve_overrides": ov_appr,
        "reject_overrides": ov_rej,
        "avg_delta": round(sum(deltas) / len(deltas), 1) if deltas else None,
        "reviewed": reviewed,
        "supervisor_right": sup_right,
        "band_right": band_right,
    }

    return ModelPerformance(
        mode=mv.mode,
        thresholds=mv.thresholds,
        model_name=mv.vlm_model,
        total_scored=total_scored,
        total_with_human=considered,
        agreement_rate=round(matches / considered, 3) if considered else None,
        per_zone_agreement=[
            {"zone_key": k, "agreement": round(v["agree"] / v["total"], 3), "n": v["total"]}
            for k, v in sorted(per_zone.items())
            if v["total"]
        ],
        confusion={"tp": tp, "tn": tn, "fp": fp, "fn": fn},
        avg_confidence_agree=_avg(conf_agree),
        avg_confidence_disagree=_avg(conf_disagree),
        agreement_by_day=[
            {"day": d, "agreement": round(v["agree"] / v["total"], 3), "n": v["total"]}
            for d, v in sorted(by_day.items())
        ],
        avg_latency_ms=_avg(latencies),
        overrides=overrides,
    )


def _labeled_set(db: Session, mv: ModelVersion, days: int | None):
    """Return [(stored_overall, human_action, zones)] for this version's scored inspections that
    have a human review, plus {inspection_id: set(human zone_keys)}. The shared gold-signal set
    behind the validation harness. `zones` is the per-zone [{zone_key, score, issues}] used to
    recompute overall under candidate configs without re-calling the VLM."""
    q = select(ScoringResult).where(ScoringResult.model_version_id == mv.id)
    if days:
        q = q.where(ScoringResult.created_at >= datetime.now(timezone.utc) - timedelta(days=days))
    results = list(db.execute(q).scalars())
    insp_ids = {r.inspection_id for r in results}
    result_ids = {r.id for r in results}
    human_action: dict = {}
    reviews: list[Review] = []
    if insp_ids:
        reviews = list(
            db.execute(select(Review).where(Review.inspection_id.in_(insp_ids)).order_by(Review.created_at)).scalars()
        )
        for r in reviews:
            human_action[r.inspection_id] = r.action
    review_by_id = {r.id: r for r in reviews}
    human_zones: dict = defaultdict(set)
    if reviews:
        for lbl in db.execute(
            select(ReviewZoneLabel).where(ReviewZoneLabel.review_id.in_({r.id for r in reviews}))
        ).scalars():
            rv = review_by_id.get(lbl.review_id)
            if rv is not None:
                human_zones[rv.inspection_id].add(lbl.zone_key)
    zones_by_result: dict = defaultdict(list)
    if result_ids:
        for z in db.execute(select(ZoneScore).where(ZoneScore.scoring_result_id.in_(result_ids))).scalars():
            zones_by_result[z.scoring_result_id].append(
                {"zone_key": z.zone_key, "score": z.score, "issues": z.issues or []}
            )
    rows = []
    for r in results:
        ha = human_action.get(r.inspection_id)
        if ha is None:
            continue
        rows.append((r.inspection_id, r.overall_score, ha, zones_by_result.get(r.id, [])))
    return rows, human_zones


@router.get("/validate", response_model=ValidationReport)
def validate(
    days: int | None = None, _admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """Measure the active model against human reviews over a window: agreement, confusion
    (dirty=positive), per-zone precision/recall, and the two rates that matter for automation --
    false-approve (missed dirty) and false-reject (clean rejected). Report-only."""
    mv = ensure_active_model_version(db)
    rows, human_zones = _labeled_set(db, mv, days)
    th = mv.thresholds or {}
    tp = tn = fp = fn = matches = 0
    per_zone: dict = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for iid, overall, ha, zones in rows:
        mv_verdict = _model_verdict(overall, th)
        if mv_verdict is None:
            continue
        matches += 1 if mv_verdict == ha else 0
        if mv_verdict == "reject" and ha == "reject":
            tp += 1
        elif mv_verdict == "approve" and ha == "approve":
            tn += 1
        elif mv_verdict == "reject" and ha == "approve":
            fp += 1
        else:
            fn += 1
        model_dirty = {z["zone_key"] for z in zones if z.get("issues")}
        hz = human_zones.get(iid, set())
        for zk in model_dirty | hz:
            if zk in model_dirty and zk in hz:
                per_zone[zk]["tp"] += 1
            elif zk in model_dirty:
                per_zone[zk]["fp"] += 1
            else:
                per_zone[zk]["fn"] += 1
    n = len(rows)
    pz = []
    for zk, c in sorted(per_zone.items()):
        p = c["tp"] / (c["tp"] + c["fp"]) if (c["tp"] + c["fp"]) else None
        rc = c["tp"] / (c["tp"] + c["fn"]) if (c["tp"] + c["fn"]) else None
        pz.append({"zone_key": zk, "precision": round(p, 3) if p is not None else None,
                   "recall": round(rc, 3) if rc is not None else None, "n": c["tp"] + c["fp"] + c["fn"]})
    return ValidationReport(
        window_days=days,
        n_reviewed=n,
        agreement_rate=round(matches / n, 3) if n else None,
        confusion={"tp": tp, "tn": tn, "fp": fp, "fn": fn},
        false_approve_rate=round(fn / (fn + tp), 3) if (fn + tp) else None,
        false_reject_rate=round(fp / (fp + tn), 3) if (fp + tn) else None,
        per_zone=pz,
        note=None if n >= 30 else f"Only {n} human-reviewed inspections; results are indicative, not conclusive.",
    )


@router.post("/recommend-thresholds", response_model=RecommendThresholdsResponse)
def recommend_thresholds(
    body: RecommendThresholdsRequest | None = None,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Sweep the decision bands (and optionally the mean/worst blend, recomputing overall from
    stored zone scores) to find the setting that maximizes agreement with humans while keeping
    the false-approve (missed-dirty) rate under a ceiling. Report-only: never applies. This is
    the instrument for deciding when it is safe to advance shadow -> assist -> auto."""
    body = body or RecommendThresholdsRequest()
    mv = ensure_active_model_version(db)
    rows, _ = _labeled_set(db, mv, body.days)
    if not rows:
        return RecommendThresholdsResponse(
            n_reviewed=0, current={"thresholds": mv.thresholds}, recommended=None, evaluated=0,
            note="No human-reviewed inspections in window; cannot recommend.",
        )

    base_scfg = scoring_defaults.resolve(mv.scoring_config)
    blends = [round(b, 2) for b in (0.3, 0.4, 0.5, 0.6, 0.7)] if body.sweep_blend else [base_scfg["blend_mean_weight"]]

    def eval_candidate(approve_t, reject_t, blend):
        agree = fn = tp = 0
        scfg = {**base_scfg, "blend_mean_weight": blend}
        for _iid, stored_overall, ha, zones in rows:
            overall = scoring_math.overall_from_zones(zones, scfg) if body.sweep_blend else stored_overall
            if overall is None:
                continue
            mid = (approve_t + reject_t) / 2
            verdict = "approve" if overall >= mid else "reject"
            agree += 1 if verdict == ha else 0
            if verdict == "reject" and ha == "reject":
                tp += 1
            elif verdict == "approve" and ha == "reject":
                fn += 1
        fa_rate = fn / (fn + tp) if (fn + tp) else 0.0
        return agree / len(rows), fa_rate

    best = None
    evaluated = 0
    for approve_t in range(60, 96, 5):
        for reject_t in range(20, approve_t - 5, 5):
            for blend in blends:
                evaluated += 1
                agreement, fa = eval_candidate(approve_t, reject_t, blend)
                if fa > body.max_false_approve_rate:
                    continue
                cand = {"auto_approve": approve_t, "auto_reject": reject_t, "blend_mean_weight": blend,
                        "agreement": round(agreement, 3), "false_approve_rate": round(fa, 3)}
                if best is None or cand["agreement"] > best["agreement"]:
                    best = cand
    return RecommendThresholdsResponse(
        n_reviewed=len(rows),
        current={"thresholds": mv.thresholds, "blend_mean_weight": base_scfg["blend_mean_weight"]},
        recommended=best,
        evaluated=evaluated,
        note=("No band kept false-approve under the ceiling; raise the ceiling or gather more labels."
              if best is None else "Report only -- not applied. Review before changing thresholds."),
    )


def _window_agreement(db: Session, mv: ModelVersion, since: datetime, until: datetime) -> dict:
    """Agreement + false-approve rate for this model version over [since, until), against the
    latest human review per inspection. The building block for drift detection."""
    results = list(db.execute(
        select(ScoringResult).where(
            ScoringResult.model_version_id == mv.id,
            ScoringResult.created_at >= since, ScoringResult.created_at < until,
        )
    ).scalars())
    insp_ids = {r.inspection_id for r in results}
    ha: dict = {}
    if insp_ids:
        for r in db.execute(select(Review).where(Review.inspection_id.in_(insp_ids)).order_by(Review.created_at)).scalars():
            ha[r.inspection_id] = r.action
    n = agree = tp = fn = 0
    for r in results:
        h = ha.get(r.inspection_id)
        if h is None:
            continue
        v = _model_verdict(r.overall_score, mv.thresholds or {})
        if v is None:
            continue
        n += 1
        agree += 1 if v == h else 0
        if v == "reject" and h == "reject":
            tp += 1
        elif v == "approve" and h == "reject":
            fn += 1
    return {
        "n": n,
        "agreement": round(agree / n, 3) if n else None,
        "false_approve_rate": round(fn / (fn + tp), 3) if (fn + tp) else None,
    }


@router.get("/drift")
def drift(recent_days: int = 14, _admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Agreement drift: compare the recent window to the prior window of equal length. Flags a
    material drop so a degrading model is caught before it erodes trust. Report-only signal that
    the dashboard can alarm on."""
    mv = ensure_active_model_version(db)
    now = datetime.now(timezone.utc)
    recent = _window_agreement(db, mv, now - timedelta(days=recent_days), now)
    prior = _window_agreement(db, mv, now - timedelta(days=2 * recent_days), now - timedelta(days=recent_days))
    drop = (
        recent["agreement"] is not None and prior["agreement"] is not None
        and recent["n"] >= 10 and prior["n"] >= 10
        and (prior["agreement"] - recent["agreement"]) >= 0.1
    )
    fa_spike = recent["false_approve_rate"] is not None and recent["n"] >= 10 and recent["false_approve_rate"] > 0.1
    return {
        "recent": recent, "prior": prior, "window_days": recent_days,
        "agreement_drop": bool(drop), "false_approve_spike": bool(fa_spike),
        "alarm": bool(drop or fa_spike),
        "note": None if (recent["n"] >= 10) else "Insufficient labeled data for a reliable drift signal.",
    }


@router.get("/fairness")
def fairness(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Fairness audit: reject rate per driver and per vehicle over decided inspections, flagging
    outliers materially harsher than the fleet mean. For an automated system that penalizes
    workers, systematic bias against certain drivers/vehicles is a legal and ethical risk, so it
    is surfaced explicitly. n>=5 required before a group can be flagged."""
    def by(group_id, group_label, join_col):
        rows = db.execute(
            select(group_id, group_label,
                   func.count().filter(Inspection.status == "approved"),
                   func.count().filter(Inspection.status == "rejected"))
            .join(Inspection, join_col)
            .where(Inspection.status.in_(("approved", "rejected")))
            .group_by(group_id, group_label)
        ).all()
        groups = []
        for gid, name, appr, rej in rows:
            decided = (appr or 0) + (rej or 0)
            if decided == 0:
                continue
            groups.append({"id": str(gid), "name": name, "decided": decided,
                           "rejected": rej or 0, "reject_rate": round((rej or 0) / decided, 3)})
        # Fleet mean over groups with enough volume; flag those materially above it.
        eligible = [g for g in groups if g["decided"] >= 5]
        mean = round(sum(g["reject_rate"] for g in eligible) / len(eligible), 3) if eligible else None
        for g in groups:
            g["flagged"] = bool(mean is not None and g["decided"] >= 5 and g["reject_rate"] >= mean + 0.2)
        groups.sort(key=lambda g: g["reject_rate"], reverse=True)
        return {"fleet_mean_reject_rate": mean, "groups": groups,
                "flagged": [g for g in groups if g["flagged"]]}

    drivers = by(User.id, User.name, Inspection.driver_id == User.id)
    vehicles = by(Vehicle.id, Vehicle.registration_plate, Inspection.vehicle_id == Vehicle.id)
    return {
        "by_driver": drivers, "by_vehicle": vehicles,
        "alarm": bool(drivers["flagged"] or vehicles["flagged"]),
        "note": "A flagged group is rejected materially more than the fleet average; investigate for bias (lighting, camera, or genuine dirtier vehicles).",
    }
