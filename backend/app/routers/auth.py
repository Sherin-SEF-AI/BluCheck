"""Authentication: driver onboarding (car number + 4-digit PIN), driver login (scan the
number plate to identify the car, then enter the PIN), admin login (email + password), and
push-token registration.
"""

from __future__ import annotations

import base64
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import plateocr, push
from ..auth import create_access_token, get_current_user, hash_password, require_admin, verify_password
from ..db import get_db
from ..models import User, Vehicle
from ..schemas import (
    LoginRequest,
    LoginResponse,
    PinLoginRequest,
    PlateResolveRequest,
    PlateResolveResponse,
    PushTokenRequest,
    RegisterRequest,
    TestPushRequest,
    TestPushResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger("blucheck.auth")


def _norm_plate(s: str) -> str:
    return s.strip().upper().replace(" ", "")


# Lightweight in-memory brute-force guard for the 4-digit PIN (10k combinations). Keyed by
# car number: max 5 failures per 5 minutes, then a short lockout. Per-instance state; the API
# runs a single task so this suffices without a schema change. Move to the DB/cache for scale.
_PIN_FAILS: dict[str, list[float]] = {}
_PIN_MAX_FAILS = 5
_PIN_WINDOW_S = 300


def _pin_locked(car: str) -> bool:
    now = time.time()
    fails = [t for t in _PIN_FAILS.get(car, []) if now - t < _PIN_WINDOW_S]
    _PIN_FAILS[car] = fails
    return len(fails) >= _PIN_MAX_FAILS


def _pin_fail(car: str) -> None:
    _PIN_FAILS.setdefault(car, []).append(time.time())


@router.post("/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: Session = Depends(get_db)) -> LoginResponse:
    """Driver self-onboarding: name + car number + 4-digit PIN. The driver claims a vehicle the
    operator has already provisioned (admin POST /vehicles); the car number is the identity
    (scanned at login) and the PIN is the secret. Trust-on-first-registration: a car number can be
    claimed once and cannot be re-registered, so an attacker cannot reset an existing driver's PIN.
    Registration is restricted to known fleet plates so an attacker cannot squat an arbitrary or
    not-yet-onboarded plate by self-creating its vehicle record.
    """
    car = _norm_plate(body.car_number)
    if db.execute(select(User).where(User.car_number == car)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="A driver with that car number already exists")

    vehicle = db.execute(
        select(Vehicle).where(Vehicle.registration_plate == car)
    ).scalar_one_or_none()
    if vehicle is None or not vehicle.active:
        # Do NOT auto-create the vehicle: only operator-provisioned fleet vehicles can be claimed.
        raise HTTPException(
            status_code=404,
            detail="This car is not registered to the fleet. Contact your operator.",
        )

    user = User(
        role="driver",
        name=body.name.strip(),
        car_number=car,
        password_hash=hash_password(body.pin),
        active=True,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Car number already registered")
    db.refresh(user)
    logger.info("driver_registered car=%s", car)
    token = create_access_token(user)
    return LoginResponse(access_token=token, role=user.role, name=user.name, car_number=user.car_number)


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    identifier = (body.username or body.email or body.car_number or "").strip()
    if not identifier:
        raise HTTPException(status_code=422, detail="Provide car number or email")
    car = _norm_plate(identifier)
    # Same brute-force lockout as /pin-login, keyed by identifier. Without this, a driver's
    # 4-digit PIN (their password_hash) could be enumerated here even though /pin-login is locked.
    lock_key = identifier.lower()
    if _pin_locked(lock_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please wait a few minutes and try again.",
        )
    user = db.execute(
        select(User).where(
            or_(User.email == identifier.lower(), User.car_number == car)
        )
    ).scalar_one_or_none()
    if user is None or not user.active or not verify_password(body.password, user.password_hash):
        _pin_fail(lock_key)
        logger.info("login_failed id=%s", identifier)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    _PIN_FAILS.pop(lock_key, None)
    token = create_access_token(user)
    logger.info("login_ok user_id=%s role=%s", user.id, user.role)
    return LoginResponse(access_token=token, role=user.role, name=user.name, car_number=user.car_number)


@router.post("/plate-resolve", response_model=PlateResolveResponse)
def plate_resolve(body: PlateResolveRequest, db: Session = Depends(get_db)) -> PlateResolveResponse:
    """Step 1 of driver login: read the number plate from a photo and find the matching
    registered driver. Returns the car number and driver name so the app can confirm who is
    signing in; the PIN is still required (pin-login) to actually authenticate.
    """
    try:
        raw = base64.b64decode(body.image_b64.split(",")[-1])
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=422, detail="Invalid image")

    candidates = plateocr.read_candidates(raw)
    if not candidates:
        raise HTTPException(
            status_code=422,
            detail="Could not read the number plate. Hold steady, fill the frame, and try again.",
        )

    drivers = db.execute(
        select(User).where(
            User.role == "driver", User.active.is_(True), User.car_number.is_not(None)
        )
    ).scalars().all()
    for cand in candidates:
        for u in drivers:
            if plateocr.similar(cand, u.car_number or ""):
                logger.info("plate_resolve ok car=%s", u.car_number)
                return PlateResolveResponse(car_number=u.car_number, name=u.name)

    logger.info("plate_resolve no_match candidates=%s", candidates[:3])
    raise HTTPException(
        status_code=404,
        detail="No driver is registered for this car. Please register first.",
    )


@router.post("/pin-login", response_model=LoginResponse)
def pin_login(body: PinLoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    """Step 2 of driver login: confirm the 4-digit PIN for the (scanned) car number."""
    car = _norm_plate(body.car_number)
    if _pin_locked(car):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please wait a few minutes and try again.",
        )
    user = db.execute(
        select(User).where(User.car_number == car, User.role == "driver")
    ).scalar_one_or_none()
    if user is None or not user.active or not verify_password(body.pin, user.password_hash):
        _pin_fail(car)
        logger.info("pin_login_failed car=%s", car)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Wrong PIN for this car")
    _PIN_FAILS.pop(car, None)
    token = create_access_token(user)
    logger.info("pin_login_ok user_id=%s", user.id)
    return LoginResponse(access_token=token, role=user.role, name=user.name, car_number=user.car_number)


@router.post("/push-token")
def save_push_token(
    body: PushTokenRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    token = body.push_token
    # An FCM token identifies a DEVICE, not a person. If several drivers signed in on the same
    # phone, the token would otherwise stay attached to every one of those accounts and a push for
    # one driver's inspection would hit that phone no matter whose it was. Enforce that a token
    # belongs to exactly ONE account -- the most recent to register it -- by clearing it from any
    # other user first.
    if token:
        db.execute(
            update(User).where(User.push_token == token, User.id != user.id).values(push_token=None)
        )
    user.push_token = token
    db.commit()
    return {"ok": True}


@router.post("/test-push", response_model=TestPushResponse)
def test_push(
    body: TestPushRequest, _admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> TestPushResponse:
    """Admin: send a test notification to a driver by car number and report the exact outcome.
    status 'skipped' means the driver has no registered push token (the app must be opened, the
    driver signed in, and notification permission granted); 'unregistered' means the token was
    stale and has now been cleared; 'ok' means it was delivered to the device."""
    car = _norm_plate(body.car_number)
    driver = db.execute(
        select(User).where(User.car_number == car, User.role == "driver")
    ).scalar_one_or_none()
    if driver is None:
        raise HTTPException(status_code=404, detail="No driver with that car number")
    had_token = bool(driver.push_token)
    status = push.send_to_driver(
        db, driver, "BluCheck test",
        "This is a test notification from BluCheck.", {"type": "test"})
    detail = {
        push.OK: "Delivered to the device.",
        push.UNREGISTERED: "Token was stale (app reinstalled); cleared. Re-open the app to re-register.",
        push.ERROR: "Push service returned an error; see logs.",
        push.SKIPPED: "No push token registered. Open the app, sign in, and allow notifications.",
    }.get(status, status)
    logger.info("test_push car=%s had_token=%s status=%s", car, had_token, status)
    return TestPushResponse(
        car_number=car, driver=driver.name, had_token=had_token, status=status, detail=detail
    )
