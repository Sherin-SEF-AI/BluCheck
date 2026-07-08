"""Admin metrics: status counts, average review time, repeat-offender vehicles."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import agent, audit, push
from ..auth import require_admin
from ..db import get_db
from ..models import Inspection, Review, ScoringResult, User, Vehicle
from ..modelcfg import ensure_active_model_version
from ..schemas import (
    CadenceRequest,
    CadenceResponse,
    ComplianceDriver,
    ComplianceResponse,
    MetricsSummary,
    MetricsTrends,
    OverdueResponse,
    OverdueVehicle,
    VehicleTrendsResponse,
    VehicleTrend,
)

router = APIRouter(prefix="/metrics", tags=["metrics"])

IST = timezone(timedelta(hours=5, minutes=30))  # fleet operates in India
DEFAULT_CADENCE_HOURS = 24  # inspect at least once a day by default


def _cadence_hours(db: Session) -> int:
    """The required inspection cadence in hours, stored on the active model version's config."""
    mv = ensure_active_model_version(db)
    try:
        return max(1, int((mv.thresholds or {}).get("cadence_hours", DEFAULT_CADENCE_HOURS)))
    except (TypeError, ValueError):
        return DEFAULT_CADENCE_HOURS


def _overdue_rows(db: Session, now: datetime | None = None) -> tuple[int, list[dict]]:
    """Active drivers whose most recent APPROVED inspection is older than the cadence (or who have
    never passed one). Returns (cadence_hours, rows sorted most-overdue-first)."""
    now = now or datetime.now(timezone.utc)
    cadence = _cadence_hours(db)
    drivers = list(
        db.execute(select(User).where(User.role == "driver", User.active.is_(True))).scalars()
    )
    # Latest APPROVED inspection per driver in one query.
    last_appr = {
        did: ts
        for did, ts in db.execute(
            select(Inspection.driver_id, Inspection.created_at)
            .where(Inspection.status == "approved")
            .distinct(Inspection.driver_id)
            .order_by(Inspection.driver_id, Inspection.created_at.desc())
        ).all()
    }
    rows: list[dict] = []
    for d in drivers:
        la = last_appr.get(d.id)
        if la is not None and la.tzinfo is None:
            la = la.replace(tzinfo=timezone.utc)
        hours_since = (now - la).total_seconds() / 3600 if la else None
        if la is None or hours_since > cadence:
            over = (hours_since - cadence) if hours_since is not None else None
            critical = la is None or (hours_since is not None and hours_since > 2 * cadence)
            rows.append(
                {
                    "driver_id": str(d.id),
                    "plate": d.car_number or "unknown",
                    "name": d.name,
                    "last_approved_at": la,
                    "hours_overdue": round(over, 1) if over is not None else None,
                    "never": la is None,
                    "severity": "critical" if critical else "due",
                }
            )
    rows.sort(key=lambda r: (0 if r["never"] else 1, -(r["hours_overdue"] or 0)))
    return cadence, rows


def overdue_sweep(db: Session) -> dict:
    """Push a reminder to every overdue driver; a stronger message for critically overdue ones.
    Called on a daily schedule by the API background loop. Idempotent to call repeatedly."""
    cadence, rows = _overdue_rows(db)
    reminded = escalated = 0
    for r in rows:
        driver = db.get(User, uuid.UUID(r["driver_id"]))
        if driver is None:
            continue
        plate = r["plate"]
        if r["severity"] == "critical":
            title = "Inspection overdue"
            msg = f"{plate} is overdue for its cleanliness inspection. Please complete it now."
            escalated += 1
        else:
            title = "Inspection due"
            msg = f"Please complete today's cleanliness inspection for {plate}."
        if push.send_to_driver(db, driver, title, msg, {"type": "overdue_reminder"}) == push.OK:
            reminded += 1
    audit.record(
        db, actor_id=None, action="overdue_sweep", entity="fleet", entity_id="all",
        detail={"overdue": len(rows), "reminded": reminded, "escalated": escalated, "cadence_hours": cadence},
    )
    db.commit()
    return {"overdue": len(rows), "reminded": reminded, "escalated": escalated, "cadence_hours": cadence}


@router.get("/cadence", response_model=CadenceResponse)
def get_cadence(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> CadenceResponse:
    return CadenceResponse(cadence_hours=_cadence_hours(db))


@router.post("/cadence", response_model=CadenceResponse)
def set_cadence(
    body: CadenceRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> CadenceResponse:
    mv = ensure_active_model_version(db)
    mv.thresholds = {**(mv.thresholds or {}), "cadence_hours": body.cadence_hours}
    audit.record(
        db, actor_id=admin.id, action="set_cadence", entity="model_version",
        entity_id=str(mv.id), detail={"cadence_hours": body.cadence_hours},
    )
    db.commit()
    return CadenceResponse(cadence_hours=body.cadence_hours)


@router.get("/overdue", response_model=OverdueResponse)
def overdue(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> OverdueResponse:
    """Vehicles whose driver has not passed an inspection within the cadence window."""
    cadence, rows = _overdue_rows(db)
    return OverdueResponse(
        cadence_hours=cadence,
        count=len(rows),
        items=[OverdueVehicle(**r) for r in rows],
    )


@router.post("/run-overdue")
def run_overdue(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Manually trigger the overdue reminder sweep (also runs daily in the background)."""
    return overdue_sweep(db)


@router.get("/summary", response_model=MetricsSummary)
def summary(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> MetricsSummary:
    # Counts by status.
    status_rows = db.execute(
        select(Inspection.status, func.count()).group_by(Inspection.status)
    ).all()
    counts = {status: count for status, count in status_rows}

    # Average seconds between capture and review, over reviewed inspections.
    avg_seconds = db.execute(
        select(
            func.avg(
                func.extract("epoch", Inspection.reviewed_at)
                - func.extract("epoch", Inspection.created_at)
            )
        ).where(Inspection.reviewed_at.is_not(None))
    ).scalar_one()

    # Rejects grouped by vehicle (repeat offenders first).
    reject_rows = db.execute(
        select(Vehicle.registration_plate, func.count())
        .join(Inspection, Inspection.vehicle_id == Vehicle.id)
        .where(Inspection.status == "rejected")
        .group_by(Vehicle.registration_plate)
        .order_by(func.count().desc())
    ).all()
    rejects = [{"vehicle_plate": plate, "rejects": count} for plate, count in reject_rows]

    return MetricsSummary(
        counts_by_status=counts,
        average_review_seconds=float(avg_seconds) if avg_seconds is not None else None,
        rejects_by_vehicle=rejects,
    )


@router.get("/trends", response_model=MetricsTrends)
def trends(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> MetricsTrends:
    # Reviews per day, split by action (last 30 days).
    day = func.date_trunc("day", Review.created_at)
    day_rows = db.execute(
        select(day.label("day"), Review.action, func.count())
        .group_by("day", Review.action)
        .order_by("day")
    ).all()
    by_day: dict[str, dict[str, int]] = {}
    for d, action, count in day_rows:
        key = d.date().isoformat()
        entry = by_day.setdefault(key, {"day": key, "approved": 0, "rejected": 0})
        if action == "approve":
            entry["approved"] += count
        else:
            entry["rejected"] += count
    reviews_by_day = list(by_day.values())

    # Per-driver totals and approval rate.
    driver_rows = db.execute(
        select(
            User.name,
            func.count(Inspection.id),
            func.count().filter(Inspection.status == "approved"),
            func.count().filter(Inspection.status == "rejected"),
        )
        .join(Inspection, Inspection.driver_id == User.id)
        .group_by(User.id, User.name)
        .order_by(func.count(Inspection.id).desc())
    ).all()
    per_driver = []
    for name, total, approved, rejected in driver_rows:
        decided = (approved or 0) + (rejected or 0)
        per_driver.append(
            {
                "driver": name,
                "total": total,
                "approved": approved or 0,
                "rejected": rejected or 0,
                "approval_rate": round((approved or 0) / decided, 2) if decided else None,
            }
        )

    avg_seconds = db.execute(
        select(
            func.avg(
                func.extract("epoch", Inspection.reviewed_at)
                - func.extract("epoch", Inspection.created_at)
            )
        ).where(Inspection.reviewed_at.is_not(None))
    ).scalar_one()

    return MetricsTrends(
        reviews_by_day=reviews_by_day,
        per_driver=per_driver,
        average_review_seconds=float(avg_seconds) if avg_seconds is not None else None,
    )


@router.get("/compliance", response_model=ComplianceResponse)
def compliance(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    date: str | None = Query(default=None, description="IST date YYYY-MM-DD; defaults to today"),
) -> ComplianceResponse:
    """Which active drivers have submitted an inspection today (IST). The core daily
    operational view for a 100+ vehicle fleet."""
    if date:
        target = datetime.strptime(date, "%Y-%m-%d").date()
    else:
        target = datetime.now(IST).date()
    start_ist = datetime(target.year, target.month, target.day, tzinfo=IST)
    start_utc = start_ist.astimezone(timezone.utc)
    end_utc = start_utc + timedelta(days=1)

    drivers = list(
        db.execute(
            select(User).where(User.role == "driver", User.active.is_(True)).order_by(User.name)
        ).scalars()
    )

    # Latest inspection today per driver (if any).
    rows = list(
        db.execute(
            select(Inspection)
            .where(Inspection.created_at >= start_utc, Inspection.created_at < end_utc)
            .order_by(Inspection.created_at.desc())
        ).scalars()
    )
    latest_by_driver: dict = {}
    for insp in rows:
        latest_by_driver.setdefault(insp.driver_id, insp)

    out: list[ComplianceDriver] = []
    inspected = 0
    for d in drivers:
        insp = latest_by_driver.get(d.id)
        if insp is not None:
            inspected += 1
        out.append(
            ComplianceDriver(
                driver_id=d.id,
                name=d.name,
                car_number=d.car_number,
                inspected=insp is not None,
                last_inspection_at=insp.created_at if insp else None,
                last_status=insp.status if insp else None,
            )
        )
    total = len(drivers)
    return ComplianceResponse(
        date=target.isoformat(),
        total_drivers=total,
        inspected_count=inspected,
        missing_count=total - inspected,
        rate=round(inspected / total, 3) if total else None,
        drivers=out,
    )


def _ist_day_bounds(target=None):
    target = target or datetime.now(IST).date()
    start_utc = datetime(target.year, target.month, target.day, tzinfo=IST).astimezone(timezone.utc)
    return target, start_utc, start_utc + timedelta(days=1)


@router.get("/dispatch-check/{plate}")
def dispatch_check(plate: str, _admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Dispatch-block hook: a vehicle is cleared to go on trip only once it has an APPROVED
    cleanliness inspection today (IST). A dispatch/trip system calls this to gate assignments,
    turning BluCheck from a report into an operational control. Returns cleared + reason."""
    p = plate.strip().upper().replace(" ", "")
    v = db.execute(select(Vehicle).where(Vehicle.registration_plate == p)).scalar_one_or_none()
    if v is None:
        raise HTTPException(status_code=404, detail="No such vehicle")
    _, start_utc, end_utc = _ist_day_bounds()
    latest = db.execute(
        select(Inspection).where(
            Inspection.vehicle_id == v.id, Inspection.created_at >= start_utc, Inspection.created_at < end_utc
        ).order_by(Inspection.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    cleared = latest is not None and latest.status == "approved"
    if cleared:
        reason = "Approved cleanliness inspection today."
    elif latest is None:
        reason = "No cleanliness inspection submitted today."
    elif latest.status == "rejected":
        reason = "Today's inspection was rejected; re-clean and re-inspect."
    else:
        reason = f"Today's inspection is {latest.status}; awaiting a pass."
    return {
        "plate": p, "cleared": cleared, "reason": reason,
        "last_status": latest.status if latest else None,
        "last_inspected_at": latest.created_at.isoformat() if latest else None,
    }


@router.post("/run-sla")
def run_sla(
    _admin: User = Depends(require_admin), db: Session = Depends(get_db),
) -> dict:
    """SLA enforcement: push a reminder to every active driver who has NOT passed a cleanliness
    inspection today. Intended to be called on a schedule (e.g. an EventBridge rule hitting this
    endpoint after the shift-start deadline). Idempotent to call repeatedly."""
    _, start_utc, end_utc = _ist_day_bounds()
    drivers = list(db.execute(
        select(User).where(User.role == "driver", User.active.is_(True))
    ).scalars())
    approved_driver_ids = set(db.execute(
        select(Inspection.driver_id).where(
            Inspection.status == "approved", Inspection.created_at >= start_utc, Inspection.created_at < end_utc
        )
    ).scalars())
    missing = [d for d in drivers if d.id not in approved_driver_ids]
    reminded = 0
    for d in missing:
        plate = d.car_number or "your vehicle"
        if push.send_to_driver(db, d, "Daily inspection due",
                               f"Please complete today's cleanliness inspection for {plate}.",
                               {"type": "sla_reminder"}) == push.OK:
            reminded += 1
    audit.record(db, actor_id=None, action="sla_reminders_sent", entity="fleet", entity_id="all",
                 detail={"missing": len(missing), "reminded": reminded})
    db.commit()
    return {"missing": len(missing), "reminded": reminded, "total_drivers": len(drivers)}


@router.get("/vehicles", response_model=VehicleTrendsResponse)
def vehicle_trends(
    _admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> VehicleTrendsResponse:
    """Per-vehicle cleanliness history: volumes, approval mix, average and latest score."""
    vehicles = list(db.execute(select(Vehicle).order_by(Vehicle.registration_plate)).scalars())

    # Aggregate inspection counts per vehicle.
    agg = {
        vid: {"total": t, "approved": a, "rejected": r, "pending": p}
        for vid, t, a, r, p in db.execute(
            select(
                Inspection.vehicle_id,
                func.count(),
                func.count().filter(Inspection.status == "approved"),
                func.count().filter(Inspection.status == "rejected"),
                func.count().filter(Inspection.status == "pending"),
            ).group_by(Inspection.vehicle_id)
        ).all()
    }

    # Average score per vehicle (over scored inspections).
    avg_score = {
        vid: float(s) if s is not None else None
        for vid, s in db.execute(
            select(Inspection.vehicle_id, func.avg(ScoringResult.overall_score))
            .join(ScoringResult, ScoringResult.inspection_id == Inspection.id)
            .group_by(Inspection.vehicle_id)
        ).all()
    }

    # Latest inspection per vehicle in ONE query (Postgres DISTINCT ON), no per-vehicle loop.
    last_by_vehicle = {
        insp.vehicle_id: insp
        for insp in db.execute(
            select(Inspection)
            .distinct(Inspection.vehicle_id)
            .order_by(Inspection.vehicle_id, Inspection.created_at.desc())
        ).scalars()
    }
    # Latest overall score per inspection in ONE query.
    score_sq = (
        select(ScoringResult.inspection_id.label("iid"), func.max(ScoringResult.created_at).label("mx"))
        .group_by(ScoringResult.inspection_id)
        .subquery()
    )
    last_score_by_insp = {
        iid: sc
        for iid, sc in db.execute(
            select(ScoringResult.inspection_id, ScoringResult.overall_score).join(
                score_sq,
                (ScoringResult.inspection_id == score_sq.c.iid)
                & (ScoringResult.created_at == score_sq.c.mx),
            )
        ).all()
    }

    out: list[VehicleTrend] = []
    for v in vehicles:
        a = agg.get(v.id, {"total": 0, "approved": 0, "rejected": 0, "pending": 0})
        last = last_by_vehicle.get(v.id)
        out.append(
            VehicleTrend(
                vehicle_id=v.id,
                plate=v.registration_plate,
                model=v.model,
                active=v.active,
                total=a["total"],
                approved=a["approved"],
                rejected=a["rejected"],
                pending=a["pending"],
                avg_score=round(avg_score[v.id], 1) if avg_score.get(v.id) is not None else None,
                last_score=last_score_by_insp.get(last.id) if last else None,
                last_status=last.status if last else None,
                last_decided_by=agent.decision_source(last) if last else None,
                last_inspected_at=last.created_at if last else None,
            )
        )
    return VehicleTrendsResponse(vehicles=out)
