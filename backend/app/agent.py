"""Autonomous cleanliness agent: turns a stored VLM scoring result into a decision and
carries it out end to end (status change, driver-facing reasons, notification, audit).

Design principles:
- Humans stay in control via the mode switch. In 'auto' the agent acts; in 'assist' it
  only recommends; in 'shadow'/'disabled' it does nothing here.
- The agent never invents ground truth: it decides only from a validated ScoringResult.
- Uncertain cases (score between the bands) are escalated to a human, never guessed.
- The agent leaves reviewed_by NULL. That, with a terminal status, is how the rest of the
  system recognises an agent decision versus a human one (no schema change needed).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import agent_brain, audit, calibration, integrity, push
from .models import ISSUE_KEYS, Inspection, ModelVersion, ScoringResult, User, Vehicle, ZoneScore

logger = logging.getLogger("blucheck.agent")

# Decisions the agent can reach from a score.
AUTO_APPROVE = "auto_approve"
AUTO_REJECT = "auto_reject"
ROUTE_HUMAN = "route_human"
NONE = "none"

# Minimum *calibrated* confidence required to auto-act; overridable per version via
# thresholds["min_calibrated_confidence"]. Uncalibrated or below-floor -> route to a human.
DEFAULT_MIN_CAL_CONF = 0.85


def decision_source(inspection: Inspection) -> str | None:
    """Who reached the current status. Agent decisions leave reviewed_by NULL with a
    terminal status; a human review sets reviewed_by. Neither => not yet decided.
    """
    if inspection.reviewed_by is not None:
        return "human"
    if inspection.status in ("approved", "rejected") and inspection.reviewed_at is not None:
        return "agent"
    return None


def decide(overall: float | None, thresholds: dict, mode: str) -> str:
    """Map an overall cleanliness score to a decision under the current mode."""
    if mode == "auto":
        if overall is None:
            return ROUTE_HUMAN
        ov = (thresholds or {}).get("overall", {})
        if overall >= ov.get("auto_approve", 85):
            return AUTO_APPROVE
        if overall <= ov.get("auto_reject", 40):
            return AUTO_REJECT
        return ROUTE_HUMAN
    if mode == "assist":
        # Recommend only; a human confirms. The recommendation itself mirrors the bands.
        return ROUTE_HUMAN
    return NONE


def _override_record(overall: float | None, supervisor_decision: str, thresholds: dict) -> dict:
    """Compare the LLM supervisor's decision to the deterministic band decision. An override is
    approving what the bands would reject, or rejecting what the bands would approve. Returns a
    structured, queryable record (for the audit log and /performance)."""
    ov = (thresholds or {}).get("overall", {})
    approve_t = ov.get("auto_approve", 85)
    reject_t = ov.get("auto_reject", 40)
    band = decide(overall, thresholds, "auto")
    sup = {"approve": AUTO_APPROVE, "reject": AUTO_REJECT, "escalate": ROUTE_HUMAN}[supervisor_decision]
    overrode, delta = False, 0
    if overall is not None:
        if supervisor_decision == "approve" and overall <= reject_t:
            overrode, delta = True, round(reject_t - overall)  # approved this deep into reject band
        elif supervisor_decision == "reject" and overall >= approve_t:
            overrode, delta = True, round(overall - approve_t)  # rejected this deep into approve band
    return {"band_decision": band, "supervisor_decision": sup, "overrode": overrode,
            "delta": delta, "auto_approve": approve_t, "auto_reject": reject_t}


def zone_reasons(sr_id, db: Session) -> list[dict]:
    """Structured {zone_key, issue_key} reasons from a scoring result's flagged zones."""
    reasons: list[dict] = []
    for z in db.execute(select(ZoneScore).where(ZoneScore.scoring_result_id == sr_id)).scalars():
        for iss in z.issues or []:
            ik = iss.get("issue_key")
            if ik in ISSUE_KEYS:
                reasons.append({"zone_key": z.zone_key, "issue_key": ik})
    return reasons


def summarize(reasons: list[dict]) -> str:
    return "; ".join(f"{r['zone_key']}: {r['issue_key']}" for r in reasons[:6]) or "low cleanliness score"


def apply_decision(db: Session, inspection: Inspection, sr: ScoringResult, mv: ModelVersion) -> dict:
    """Carry out the agent's decision for one scored, pending inspection.

    Returns {decision, reasons, notified}. Commits its own changes. Safe to call on any
    inspection: only 'pending' inspections in 'auto' mode are acted upon; everything else
    is a no-op that still reports what the agent *would* recommend.
    """
    reasons = zone_reasons(sr.id, db)

    # Content gate: the scorer determined the frames are not a vehicle (a room, a person, random
    # footage). This is invalid input, not a cleanliness judgment, so it is rejected in EVERY mode
    # (even shadow) -- cleanliness is only ever analysed on an actual vehicle.
    if (sr.raw_json or {}).get("not_vehicle") and inspection.status == "pending":
        now = datetime.now(timezone.utc)
        sr.decision = AUTO_REJECT
        inspection.status, inspection.reviewed_at, inspection.reviewed_by = "rejected", now, None
        inspection.reject_reason = (
            "This does not appear to be a vehicle. Only vehicle cleanliness can be analysed -- "
            "please record the exterior and interior of the car."
        )
        db.add(sr)
        audit.record(db, actor_id=None, action="agent_reject_not_vehicle", entity="inspection",
                     entity_id=str(inspection.id), detail={"reason": "not_vehicle", "by": "content_gate"})
        db.commit()
        notified = _notify(db, inspection, "Inspection rejected", inspection.reject_reason)
        logger.info("content gate rejected non-vehicle inspection=%s", inspection.id)
        return {"decision": AUTO_REJECT, "reasons": [], "acted": True, "notified": notified, "gate": "not_vehicle"}

    # Fraud / integrity signals (best-effort; never blocks the pipeline). Stored on the inspection
    # and, when high-risk, used below to hold it for a human instead of auto-approving.
    integ = None
    if inspection.status == "pending":
        try:
            integ = integrity.check(db, inspection)
            inspection.integrity = integ
            db.commit()
            if integ["risk"] != "low":
                logger.info("integrity risk=%s inspection=%s reasons=%s", integ["risk"], inspection.id, integ["reasons"])
        except Exception as err:  # noqa: BLE001 - integrity must never break scoring
            logger.warning("integrity check failed for %s: %s", inspection.id, err)

    # Non-auto modes only observe/recommend (deterministic; no side effects).
    if mv.mode != "auto":
        decision = decide(sr.overall_score, mv.thresholds or {}, mv.mode)
        sr.decision = decision
        db.add(sr)
        audit.record(db, actor_id=None, action=f"agent_{decision}", entity="inspection",
                     entity_id=str(inspection.id),
                     detail={"mode": mv.mode, "overall_score": sr.overall_score, "acted": False})
        db.commit()
        return {"decision": decision, "reasons": reasons, "acted": False, "notified": False}

    # Full Autonomy: no human ever. Every inspection reaches a terminal approve/reject; the
    # calibration gate and any "escalate" are collapsed to a deterministic decision. Opt-in via
    # thresholds.full_autonomy (default off -> the safe, calibration-gated behavior below).
    full_auto = bool((mv.thresholds or {}).get("full_autonomy"))

    if not full_auto:
        # Calibration gate (auto only): never auto-act on uncalibrated or low-calibrated confidence.
        gated = _calibration_gate(db, inspection, sr, mv, reasons)
        if gated is not None:
            return gated

    # AUTO mode: the supervisor agent reasons over the full context and chooses the actions.
    brain = agent_brain.decide(_context(db, inspection, sr))
    if brain is None:  # brain unavailable -> deterministic threshold fallback (never stall)
        return _apply_threshold(db, inspection, sr, mv, reasons, force_terminal=full_auto)

    now = datetime.now(timezone.utc)
    d = brain["decision"]
    # Full autonomy: an "escalate" must become terminal. Split on the single cutoff (midpoint of
    # the bands, or thresholds.overall.full_auto_cutoff), defaulting an ambiguous case to reject
    # so a possibly-dirty vehicle is re-cleaned rather than passed.
    if full_auto and d == "escalate":
        d = "approve" if (sr.overall_score or 0) >= _full_auto_cutoff(mv.thresholds or {}) else "reject"
    # High fraud risk overrides an approval: never auto-pass footage that looks reused/staged.
    if integ and integ.get("risk") == "high" and d == "approve":
        d = "escalate"
        reasons = reasons + [{"zone_key": "integrity", "issue_key": "flagged"}]
        logger.info("integrity HOLD inspection=%s reasons=%s", inspection.id, integ["reasons"])
    acted = False
    if inspection.status == "pending":
        if d == "approve":
            sr.decision, inspection.status = AUTO_APPROVE, "approved"
            inspection.reviewed_at, inspection.reviewed_by, inspection.reject_reason = now, None, None
            acted = True
        elif d == "reject":
            sr.decision, inspection.status = AUTO_REJECT, "rejected"
            inspection.reviewed_at, inspection.reviewed_by = now, None
            inspection.reject_reason = (brain["reasoning"] or summarize(reasons))[:500]
            acted = True
        else:  # escalate -> leave pending for a human
            sr.decision = ROUTE_HUMAN
    db.add(sr)

    # Two-layer auditability: did the supervisor override the deterministic band? An override is
    # approving something the bands would reject, or rejecting something the bands would approve.
    override = _override_record(sr.overall_score, d, mv.thresholds or {})

    audit.record(db, actor_id=None, action=f"agent_{d}", entity="inspection",
                 entity_id=str(inspection.id),
                 detail={"mode": "auto", "overall_score": sr.overall_score,
                         "reasoning": brain["reasoning"],
                         "notify": {"title": brain["notify_title"], "body": brain["notify_body"]},
                         "reclean_zones": brain["reclean_zones"],
                         "escalate_reason": brain["escalate_reason"], "priority": brain["priority"],
                         "override": override, "by": "supervisor_agent"})
    # When it truly overrides a firm band, emit a distinct, queryable record too.
    if override["overrode"]:
        audit.record(db, actor_id=None, action="agent_override", entity="inspection",
                     entity_id=str(inspection.id),
                     detail={**override, "overall_score": sr.overall_score,
                             "reasoning": brain["reasoning"], "by": "supervisor_agent"})
        logger.info("supervisor OVERRIDE inspection=%s band=%s supervisor=%s delta=%s",
                    inspection.id, override["band_decision"], override["supervisor_decision"],
                    override["delta"])
    db.commit()

    # The agent takes its own action: notify the driver with its tailored message.
    notified = False
    if d in ("approve", "reject"):
        notified = _notify(db, inspection, brain["notify_title"], brain["notify_body"])
    elif d == "escalate":
        notified = _notify(db, inspection, "Inspection under review",
                           brain["notify_body"] or "Your inspection needs a closer look; we'll update you shortly.")
    logger.info("supervisor agent inspection=%s decision=%s priority=%s notified=%s",
                inspection.id, d, brain["priority"], notified)
    mapped = {"approve": AUTO_APPROVE, "reject": AUTO_REJECT, "escalate": ROUTE_HUMAN}[d]
    return {"decision": mapped, "reasons": reasons, "acted": acted, "notified": notified, "agent": brain}


def _calibration_gate(db: Session, inspection: Inspection, sr: ScoringResult, mv: ModelVersion,
                      reasons: list[dict]) -> dict | None:
    """Fail-safe auto gate. Returns None to proceed with the auto decision only when the model's
    *calibrated* confidence at this raw confidence meets the floor. Otherwise (no calibration for
    this version, insufficient evidence at this confidence, or below floor) it routes the
    inspection to a human and returns the route_human result. We never auto-act on uncalibrated
    confidence. Only runs in auto mode (callers gate on that); shadow/assist never reach here.
    """
    floor = (mv.thresholds or {}).get("min_calibrated_confidence", DEFAULT_MIN_CAL_CONF)
    calibrated = calibration.lookup(mv.calibration, sr.overall_confidence)
    if calibrated is not None and calibrated >= floor:
        return None  # calibrated and confident enough -> let the supervisor/threshold decide

    reason = "no_calibration" if calibrated is None else "below_floor"
    sr.decision = ROUTE_HUMAN
    db.add(sr)
    audit.record(
        db, actor_id=None, action="agent_route_human_uncalibrated", entity="inspection",
        entity_id=str(inspection.id),
        detail={"reason": reason, "calibrated_confidence": calibrated, "floor": floor,
                "overall_confidence": sr.overall_confidence, "by": "calibration_gate"},
    )
    db.commit()
    notified = _notify(db, inspection, "Inspection under review",
                       "Your inspection needs a quick human check; we'll update you shortly.")
    logger.info("calibration gate route_human inspection=%s reason=%s calibrated=%s floor=%s",
                inspection.id, reason, calibrated, floor)
    return {"decision": ROUTE_HUMAN, "reasons": reasons, "acted": False, "notified": notified,
            "gate": reason}


def _full_auto_cutoff(thresholds: dict) -> float:
    """Single approve/reject cutoff for Full Autonomy: an explicit override, else the midpoint of
    the auto-approve / auto-reject bands."""
    ov = (thresholds or {}).get("overall", {})
    if isinstance(ov.get("full_auto_cutoff"), (int, float)):
        return float(ov["full_auto_cutoff"])
    return (ov.get("auto_approve", 85) + ov.get("auto_reject", 40)) / 2


def _apply_threshold(db: Session, inspection: Inspection, sr: ScoringResult, mv: ModelVersion,
                     reasons: list[dict], force_terminal: bool = False) -> dict:
    """Deterministic fallback used when the supervisor agent is unavailable. When force_terminal
    (Full Autonomy), an in-between score can't route to a human: it is split on the single cutoff
    into approve/reject."""
    decision = decide(sr.overall_score, mv.thresholds or {}, "auto")
    if force_terminal and decision == ROUTE_HUMAN:
        decision = AUTO_APPROVE if (sr.overall_score or 0) >= _full_auto_cutoff(mv.thresholds or {}) else AUTO_REJECT
    sr.decision = decision
    db.add(sr)
    acted = False
    if inspection.status == "pending":
        now = datetime.now(timezone.utc)
        if decision == AUTO_APPROVE:
            inspection.status, inspection.reviewed_at, inspection.reviewed_by, inspection.reject_reason = "approved", now, None, None
            acted = True
        elif decision == AUTO_REJECT:
            inspection.status, inspection.reviewed_at, inspection.reviewed_by = "rejected", now, None
            inspection.reject_reason = summarize(reasons)
            acted = True
    audit.record(db, actor_id=None, action=f"agent_{decision}", entity="inspection",
                 entity_id=str(inspection.id),
                 detail={"mode": "auto", "overall_score": sr.overall_score, "acted": acted,
                         "reasons": reasons, "by": "threshold_fallback"})
    db.commit()
    notified = _notify_driver(db, inspection, reasons) if acted else False
    return {"decision": decision, "reasons": reasons, "acted": acted, "notified": notified}


def _context(db: Session, inspection: Inspection, sr: ScoringResult) -> dict:
    """Assemble everything the supervisor agent reasons over."""
    vehicle = db.get(Vehicle, inspection.vehicle_id)
    driver = db.get(User, inspection.driver_id)
    zones = [
        {"zone_key": z.zone_key, "score": z.score, "issues": z.issues or []}
        for z in db.execute(select(ZoneScore).where(ZoneScore.scoring_result_id == sr.id)).scalars()
    ]
    is_re = bool(inspection.reinspection_of)
    prior = None
    if is_re:
        parent = db.get(Inspection, inspection.reinspection_of)
        prior = parent.reject_reason if parent else None
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    recent_rejects = db.execute(
        select(func.count()).select_from(Inspection).where(
            Inspection.driver_id == inspection.driver_id,
            Inspection.status == "rejected",
            Inspection.created_at >= cutoff,
        )
    ).scalar_one()
    return {
        "vehicle": vehicle.registration_plate if vehicle else "",
        "driver": driver.name if driver else "",
        "overall_score": sr.overall_score,
        "overall_confidence": sr.overall_confidence,
        "zones": zones,
        "is_reinspection": is_re,
        "prior_issues": prior,
        "recent_rejects": recent_rejects,
    }


def _notify(db: Session, inspection: Inspection, title: str, body: str) -> bool:
    driver = db.get(User, inspection.driver_id)
    status = push.send_to_driver(
        db, driver, title, body,
        {"inspection_id": str(inspection.id), "status": inspection.status, "by": "agent"})
    return status == push.OK


def _notify_driver(db: Session, inspection: Inspection, reasons: list[dict]) -> bool:
    """Best-effort push to the driver. The mobile app also detects the status change by
    polling, so this never blocks and its failure does not matter for delivery.
    """
    driver = db.get(User, inspection.driver_id)
    vehicle = db.get(Vehicle, inspection.vehicle_id)
    plate = vehicle.registration_plate if vehicle else "your vehicle"
    if inspection.status == "approved":
        title, msg = "Inspection approved", f"{plate} passed the automated cleanliness check."
    else:
        title, msg = "Inspection rejected", f"{plate}: re-clean {summarize(reasons)}."
    status = push.send_to_driver(
        db, driver, title, msg,
        {"inspection_id": str(inspection.id), "status": inspection.status, "by": "agent"})
    return status == push.OK


def process_pending(db: Session, mv: ModelVersion, limit: int = 500) -> dict:
    """Apply the agent to the backlog of already-scored pending inspections. Used when the
    admin turns autonomy on, or when thresholds change. Returns a counts summary.
    """
    counts = {"approved": 0, "rejected": 0, "escalated": 0, "scored_missing": 0}
    pend = list(
        db.execute(
            select(Inspection).where(Inspection.status == "pending").limit(limit)
        ).scalars()
    )
    for insp in pend:
        sr = db.execute(
            select(ScoringResult)
            .where(ScoringResult.inspection_id == insp.id)
            .order_by(ScoringResult.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if sr is None:
            counts["scored_missing"] += 1
            continue
        out = apply_decision(db, insp, sr, mv)
        if out["decision"] == AUTO_APPROVE and out["acted"]:
            counts["approved"] += 1
        elif out["decision"] == AUTO_REJECT and out["acted"]:
            counts["rejected"] += 1
        else:
            counts["escalated"] += 1
    return counts
