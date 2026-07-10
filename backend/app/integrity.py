"""Fraud / integrity agent.

Computed at decision time for each inspection, from three signals:

  - reused footage: a strong perceptual-hash (phash) match between this inspection's selected
    frames and the frames of a DIFFERENT inspection (someone re-submitting an old clip).
  - GPS anomaly: no GPS, or a location far from the driver's usual inspection spot.
  - rapid resubmission: several inspections from the same driver in a short window.

Returns {risk: low|medium|high, reasons: [...], signals: {...}}. High risk means an inspection is
held for a human even in auto mode. Never raises: on error the caller skips the check.
"""

from __future__ import annotations

import logging
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Capture, Frame, Inspection

logger = logging.getLogger("blucheck.integrity")

PHASH_MATCH_BITS = 6        # <= this Hamming distance = the same frame
GPS_FAR_KM = 25.0           # farther than this from the driver's usual spot is unusual
RAPID_WINDOW_MIN = 10       # window for rapid-resubmission
RAPID_COUNT = 3             # this many others in the window is suspicious
CANDIDATE_LIMIT = 3000      # cap on other-inspection frames scanned

_ORDER = {"low": 0, "medium": 1, "high": 2}


def _raise_risk(cur: str, to: str) -> str:
    return to if _ORDER[to] > _ORDER[cur] else cur


def _to_int(h: str | None) -> int | None:
    try:
        return int(h, 16) if h else None
    except (TypeError, ValueError):
        return None


def _haversine_km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dphi = math.radians(b_lat - a_lat)
    dlmb = math.radians(b_lon - a_lon)
    x = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(x)))


def check(db: Session, inspection: Inspection) -> dict:
    now = datetime.now(timezone.utc)
    risk = "low"
    reasons: list[str] = []
    signals: dict = {}

    # ---- 1) reused footage (phash match to a different inspection) ----
    my_hashes = [
        _to_int(h) for h in db.execute(
            select(Frame.phash).join(Capture, Capture.id == Frame.capture_id)
            .where(Capture.inspection_id == inspection.id, Frame.selected.is_(True), Frame.phash.isnot(None))
        ).scalars().all()
    ]
    my_hashes = [h for h in my_hashes if h is not None]
    if my_hashes:
        candidates = db.execute(
            select(Frame.phash, Capture.inspection_id).join(Capture, Capture.id == Frame.capture_id)
            .where(Capture.inspection_id != inspection.id, Frame.selected.is_(True), Frame.phash.isnot(None))
            .order_by(Frame.id.desc()).limit(CANDIDATE_LIMIT)
        ).all()
        cand = [(_to_int(h), iid) for h, iid in candidates]
        cand = [(h, iid) for h, iid in cand if h is not None]
        matches: dict = defaultdict(int)
        for mh in my_hashes:
            for ch, iid in cand:
                if bin(mh ^ ch).count("1") <= PHASH_MATCH_BITS:
                    matches[iid] += 1
                    break  # this frame is accounted for
        if matches:
            best_iid, best = max(matches.items(), key=lambda kv: kv[1])
            signals["reused_matches"] = best
            if best >= max(3, len(my_hashes) // 3):
                risk = _raise_risk(risk, "high")
                signals["reused_from"] = str(best_iid)
                reasons.append(f"Footage appears reused: {best} frame(s) closely match a different inspection.")

    # ---- 2) GPS anomaly ----
    if inspection.gps_lat is None or inspection.gps_lon is None:
        risk = _raise_risk(risk, "medium")
        signals["gps"] = "missing"
        reasons.append("No GPS location was captured for this inspection.")
    else:
        others = db.execute(
            select(Inspection.gps_lat, Inspection.gps_lon).where(
                Inspection.driver_id == inspection.driver_id, Inspection.id != inspection.id,
                Inspection.gps_lat.isnot(None), Inspection.gps_lon.isnot(None),
            ).limit(50)
        ).all()
        if others:
            med_lat = statistics.median([o[0] for o in others])
            med_lon = statistics.median([o[1] for o in others])
            km = _haversine_km(inspection.gps_lat, inspection.gps_lon, med_lat, med_lon)
            signals["gps_km_from_usual"] = round(km, 1)
            if km > GPS_FAR_KM:
                risk = _raise_risk(risk, "medium")
                reasons.append(f"Location is {round(km)} km from this driver's usual inspection spot.")

    # ---- 3) rapid resubmission ----
    recent = db.execute(
        select(func.count()).select_from(Inspection).where(
            Inspection.driver_id == inspection.driver_id, Inspection.id != inspection.id,
            Inspection.created_at >= now - timedelta(minutes=RAPID_WINDOW_MIN),
        )
    ).scalar_one()
    signals["submissions_last_10min"] = int(recent)
    if recent >= RAPID_COUNT:
        risk = _raise_risk(risk, "medium")
        reasons.append(f"{recent} other inspections from this driver in the last {RAPID_WINDOW_MIN} minutes.")

    return {"risk": risk, "reasons": reasons, "signals": signals, "checked_at": now.isoformat()}
