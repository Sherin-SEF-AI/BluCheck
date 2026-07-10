"""Public, API-key-authenticated integration API (/v1).

External apps authenticate with the X-API-Key header (keys minted in the dashboard) and can:
  - POST /v1/score        -> score vehicle images for cleanliness (synchronous)
  - GET  /v1/inspections  -> list inspections with results
  - GET  /v1/inspections/{id} -> one inspection's result + per-zone breakdown
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import agent, health, scoring_api
from ..apikeys import require_api_key
from ..db import get_db
from ..modelcfg import ensure_active_model_version
from ..models import ApiKey, Inspection, ScoringResult, Vehicle, ZoneScore
from ..schemas import (
    PublicInspectionDetail,
    PublicInspectionItem,
    PublicInspectionList,
    ScoreRequest,
    ScoreResponse,
    ScoreZone,
)

router = APIRouter(prefix="/v1", tags=["public"])
logger = logging.getLogger("blucheck.public")


@router.post("/score", response_model=ScoreResponse)
def score(
    body: ScoreRequest, _key: ApiKey = Depends(require_api_key), db: Session = Depends(get_db)
) -> ScoreResponse:
    """Score 1-5 vehicle images for cleanliness against the fleet's active policy."""
    mv = ensure_active_model_version(db)
    try:
        out = scoring_api.score(body.images, mv.scoring_config, mv.thresholds, mv.vlm_model)
    except scoring_api.ScoreError as err:
        if "model call" in str(err):  # a model-access failure, not a bad image
            health.record_incident(db, "public_score", mv.vlm_model, str(err))
        raise HTTPException(status_code=502, detail=str(err))
    return ScoreResponse(
        is_vehicle=out["is_vehicle"], overall_score=out["overall_score"], decision=out["decision"],
        zones=[ScoreZone(**z) for z in out["zones"]],
    )


@router.get("/inspections", response_model=PublicInspectionList)
def list_inspections(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _key: ApiKey = Depends(require_api_key), db: Session = Depends(get_db),
) -> PublicInspectionList:
    where = []
    if status:
        where.append(Inspection.status == status)
    total = db.execute(select(func.count()).select_from(Inspection).where(*where)).scalar_one()
    rows = db.execute(
        select(Inspection).where(*where).order_by(Inspection.created_at.desc()).limit(limit).offset(offset)
    ).scalars().all()
    # Latest overall score per listed inspection.
    ids = [i.id for i in rows]
    scores = {}
    if ids:
        for iid, sc in db.execute(
            select(ScoringResult.inspection_id, ScoringResult.overall_score)
            .where(ScoringResult.inspection_id.in_(ids)).order_by(ScoringResult.created_at.desc())
        ).all():
            scores.setdefault(iid, sc)
    items = []
    for i in rows:
        v = db.get(Vehicle, i.vehicle_id)
        items.append(PublicInspectionItem(
            id=str(i.id), plate=v.registration_plate if v else "?", status=i.status,
            overall_score=scores.get(i.id), decision_source=agent.decision_source(i), created_at=i.created_at,
        ))
    return PublicInspectionList(items=items, total=total)


@router.get("/inspections/{inspection_id}", response_model=PublicInspectionDetail)
def get_inspection(
    inspection_id: uuid.UUID, _key: ApiKey = Depends(require_api_key), db: Session = Depends(get_db)
) -> PublicInspectionDetail:
    insp = db.get(Inspection, inspection_id)
    if insp is None:
        raise HTTPException(status_code=404, detail="Inspection not found")
    v = db.get(Vehicle, insp.vehicle_id)
    sr = db.execute(
        select(ScoringResult).where(ScoringResult.inspection_id == insp.id)
        .order_by(ScoringResult.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    zones = []
    if sr is not None:
        for z in db.execute(select(ZoneScore).where(ZoneScore.scoring_result_id == sr.id)).scalars():
            zones.append(ScoreZone(zone_key=z.zone_key, score=z.score, issues=z.issues or []))
    return PublicInspectionDetail(
        id=str(insp.id), plate=v.registration_plate if v else "?", status=insp.status,
        overall_score=sr.overall_score if sr else None, reject_reason=insp.reject_reason,
        zones=zones, created_at=insp.created_at,
    )
