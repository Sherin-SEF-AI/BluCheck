"""Vehicle listing for drivers, plus admin CRUD."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit
from ..auth import get_current_user, require_admin
from ..db import get_db
from ..models import User, Vehicle
from ..schemas import VehicleCreate, VehicleOut, VehicleUpdate

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


@router.get("", response_model=list[VehicleOut])
def list_vehicles(
    include_inactive: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Vehicle]:
    stmt = select(Vehicle).order_by(Vehicle.registration_plate)
    # A driver sees only their own registered car; admins see all (optionally inactive).
    if user.role == "driver":
        stmt = stmt.where(Vehicle.registration_plate == (user.car_number or "___none___"))
    elif not include_inactive:
        stmt = stmt.where(Vehicle.active.is_(True))
    return list(db.execute(stmt).scalars())


@router.post("", response_model=VehicleOut, status_code=status.HTTP_201_CREATED)
def create_vehicle(
    body: VehicleCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> Vehicle:
    vehicle = Vehicle(
        registration_plate=body.registration_plate.strip().upper(),
        model=body.model,
        active=body.active,
    )
    db.add(vehicle)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A vehicle with that plate already exists")
    audit.record(db, actor_id=admin.id, action="create_vehicle", entity="vehicle", entity_id=str(vehicle.id), detail={"plate": vehicle.registration_plate})
    db.commit()
    db.refresh(vehicle)
    return vehicle


@router.patch("/{vehicle_id}", response_model=VehicleOut)
def update_vehicle(
    vehicle_id: uuid.UUID,
    body: VehicleUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Vehicle:
    vehicle = db.get(Vehicle, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    if body.registration_plate is not None:
        vehicle.registration_plate = body.registration_plate.strip().upper()
    if body.model is not None:
        vehicle.model = body.model
    if body.active is not None:
        vehicle.active = body.active
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A vehicle with that plate already exists")
    audit.record(db, actor_id=admin.id, action="update_vehicle", entity="vehicle", entity_id=str(vehicle_id), detail=body.model_dump(exclude_none=True))
    db.commit()
    db.refresh(vehicle)
    return vehicle
