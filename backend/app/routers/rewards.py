"""Driver rewards.

Points are derived from a driver's own inspection history (no extra tables) so the number is
always consistent with the record and cannot drift:

  - +10 for every approved inspection
  - +5 first-pass bonus when an approval was NOT a re-clean of an earlier reject
  - +2 for each day in the driver's current consecutive-day inspection streak

Tiers are cosmetic bands over the total. Everything is computed on read; a driver sees their
own rewards, an admin can see the leaderboard.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import get_current_user, require_admin
from ..db import get_db
from ..models import Inspection, ScoringResult, User, ZoneScore
from ..schemas import (
    CoachingResponse,
    LeaderboardResponse,
    LeaderboardRow,
    RewardEvent,
    RewardTier,
    RewardsResponse,
)
from .. import coaching_ai

router = APIRouter(prefix="/rewards", tags=["rewards"])

IST = timezone(timedelta(hours=5, minutes=30))
PTS_APPROVED = 10
PTS_FIRST_PASS = 5
PTS_STREAK_DAY = 2

# (min_points, tier name). Ordered ascending.
TIERS = [(0, "Bronze"), (150, "Silver"), (400, "Gold"), (800, "Platinum")]


def _tier(points: int) -> tuple[str, int | None]:
    """Return (current tier name, points at which the next tier unlocks or None if maxed)."""
    name = TIERS[0][1]
    nxt: int | None = None
    for thr, nm in TIERS:
        if points >= thr:
            name = nm
        else:
            nxt = thr
            break
    return name, nxt


def _streak_days(days: set) -> int:
    """Length of the consecutive-day run ending today (or yesterday, if nothing yet today)."""
    if not days:
        return 0
    today = datetime.now(IST).date()
    start = today if today in days else today - timedelta(days=1)
    if start not in days:
        return 0
    n = 0
    d = start
    while d in days:
        n += 1
        d = d - timedelta(days=1)
    return n


def _compute(insps: list[Inspection]) -> dict:
    approved = [i for i in insps if i.status == "approved"]
    first_pass = [i for i in approved if i.reinspection_of is None]
    day_set = {i.created_at.astimezone(IST).date() for i in insps if i.created_at}
    streak = _streak_days(day_set)
    points = len(approved) * PTS_APPROVED + len(first_pass) * PTS_FIRST_PASS + streak * PTS_STREAK_DAY
    return {
        "points": points,
        "approved": len(approved),
        "first_pass": len(first_pass),
        "streak": streak,
        "total": len(insps),
    }


@router.get("/me", response_model=RewardsResponse)
def my_rewards(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    insps = db.execute(
        select(Inspection).where(Inspection.driver_id == user.id).order_by(Inspection.created_at.desc())
    ).scalars().all()
    c = _compute(insps)
    tier, next_at = _tier(c["points"])

    month = datetime.now(IST).strftime("%Y-%m")
    this_month = [
        i for i in insps
        if i.created_at and i.created_at.astimezone(IST).strftime("%Y-%m") == month
    ]
    month_pts = _compute(this_month)["points"]

    # A short, human-readable earnings feed from the most recent inspections.
    events: list[RewardEvent] = []
    for i in insps[:8]:
        date = i.created_at.astimezone(IST).strftime("%d %b") if i.created_at else ""
        if i.status == "approved":
            pts = PTS_APPROVED + (PTS_FIRST_PASS if i.reinspection_of is None else 0)
            label = "Approved (first pass)" if i.reinspection_of is None else "Approved after re-clean"
        elif i.status == "rejected":
            pts, label = 0, "Rejected -- re-clean to earn points"
        else:
            pts, label = 0, "In progress"
        events.append(RewardEvent(date=date, label=label, points=pts))

    return RewardsResponse(
        points=c["points"],
        tier=tier,
        next_tier_at=next_at,
        streak_days=c["streak"],
        approved_count=c["approved"],
        first_pass_count=c["first_pass"],
        total_inspections=c["total"],
        this_month_points=month_pts,
        tiers=[RewardTier(name=nm, min_points=thr) for thr, nm in TIERS],
        recent=events,
        per_approved=PTS_APPROVED,
        per_first_pass=PTS_FIRST_PASS,
        per_streak_day=PTS_STREAK_DAY,
    )


@router.get("/coaching", response_model=CoachingResponse)
def my_coaching(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """An AI coaching tip built from the driver's own recent history: recurring flagged areas,
    pass rate and streak. Falls back to a friendly generic message if the agent is unavailable."""
    insps = db.execute(
        select(Inspection).where(Inspection.driver_id == user.id).order_by(Inspection.created_at.desc()).limit(15)
    ).scalars().all()
    approved = sum(1 for i in insps if i.status == "approved")
    rejected = [i for i in insps if i.status == "rejected"]
    # Which zones keep getting flagged across this driver's recent rejections.
    zone_counts: dict[str, int] = {}
    for i in rejected:
        sr = db.execute(
            select(ScoringResult).where(ScoringResult.inspection_id == i.id).order_by(ScoringResult.created_at.desc()).limit(1)
        ).scalar_one_or_none()
        if sr is None:
            continue
        for z in db.execute(select(ZoneScore).where(ZoneScore.scoring_result_id == sr.id)).scalars():
            if z.issues:
                zone_counts[z.zone_key] = zone_counts.get(z.zone_key, 0) + 1
    summary = {
        "recent_inspections": len(insps),
        "approved": approved,
        "rejected": len(rejected),
        "recurring_flagged_zones": dict(sorted(zone_counts.items(), key=lambda kv: -kv[1])),
    }
    out = coaching_ai.coach(summary)
    if out is None:
        if not insps:
            out = {"headline": "Welcome aboard!", "tip": "Complete your first inspection to start getting personalized tips.", "focus_zone": ""}
        elif not rejected:
            out = {"headline": "Great work — keep it up!", "tip": "You're passing consistently. A quick daily wipe-down keeps it that way.", "focus_zone": ""}
        else:
            top = next(iter(summary["recurring_flagged_zones"]), "")
            out = {"headline": "One area to watch", "tip": f"Give a little extra attention to your {top.replace('_', ' ')} before your next inspection." if top else "Re-clean the flagged areas and you'll pass.", "focus_zone": top}
    return CoachingResponse(**out)


@router.get("/leaderboard", response_model=LeaderboardResponse)
def leaderboard(_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    drivers = db.execute(select(User).where(User.role == "driver")).scalars().all()
    rows: list[LeaderboardRow] = []
    for d in drivers:
        insps = db.execute(
            select(Inspection).where(Inspection.driver_id == d.id)
        ).scalars().all()
        c = _compute(insps)
        tier, _ = _tier(c["points"])
        rows.append(
            LeaderboardRow(
                driver_id=str(d.id),
                name=d.name,
                car_number=d.car_number,
                points=c["points"],
                tier=tier,
                approved_count=c["approved"],
                streak_days=c["streak"],
            )
        )
    rows.sort(key=lambda r: r.points, reverse=True)
    return LeaderboardResponse(rows=rows)
