"""Cleanliness taxonomy: zones and issues used for structured review labels."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import TaxonomyIssue, TaxonomyZone, User
from ..schemas import TaxonomyItem

router = APIRouter(prefix="/taxonomy", tags=["taxonomy"])


@router.get("/zones", response_model=list[TaxonomyItem])
def zones(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return list(
        db.execute(select(TaxonomyZone).where(TaxonomyZone.active.is_(True)).order_by(TaxonomyZone.label)).scalars()
    )


@router.get("/issues", response_model=list[TaxonomyItem])
def issues(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return list(
        db.execute(select(TaxonomyIssue).where(TaxonomyIssue.active.is_(True)).order_by(TaxonomyIssue.label)).scalars()
    )
