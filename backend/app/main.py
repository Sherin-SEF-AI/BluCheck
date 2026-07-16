"""BluCheck API application entrypoint."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .logging_config import configure_logging, request_id_var

configure_logging()
logger = logging.getLogger("blucheck.api")
settings = get_settings()

app = FastAPI(title="BluCheck API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.dashboard_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("x-request-id", uuid.uuid4().hex)
    token = request_id_var.set(rid)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("unhandled_error method=%s path=%s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})
    finally:
        request_id_var.reset(token)
    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    # Log method and path only; never the query string (may carry signed URLs).
    logger.info(
        "request method=%s path=%s status=%s ms=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    response.headers["x-request-id"] = rid
    return response


@app.on_event("startup")
def _validate_runtime_config() -> None:
    # Import stays AWS-free (config.resolve_secrets is best-effort); enforce presence here so
    # a misconfigured deployment fails fast at startup rather than on the first request.
    settings.validate_runtime()


@app.on_event("startup")
def _dedupe_push_tokens() -> None:
    """One-time-safe cleanup for tokens shared by more than one account (from several drivers
    signing in on the same phone). A shared FCM token is ambiguous -- it hits a device regardless
    of whose inspection triggered the push -- so null every shared copy. Each device re-registers
    its token on next app open, re-binding it to exactly one account."""
    from sqlalchemy import func, select, update

    from .db import SessionLocal
    from .models import User

    try:
        with SessionLocal() as db:
            dupes = [
                t for (t,) in db.execute(
                    select(User.push_token)
                    .where(User.push_token.isnot(None))
                    .group_by(User.push_token)
                    .having(func.count() > 1)
                ).all()
            ]
            if dupes:
                db.execute(update(User).where(User.push_token.in_(dupes)).values(push_token=None))
                db.commit()
                logger.warning("deduped %d shared push token(s) across accounts", len(dupes))
    except Exception:  # noqa: BLE001 - never block startup on cleanup
        logger.exception("push token dedupe failed")


SELF_HEAL_INTERVAL_S = 600  # every 10 minutes
# Fire the daily overdue sweep once per day, after the shift-start hour (IST).
OVERDUE_SWEEP_HOUR_IST = 9
_IST = __import__("datetime").timezone(__import__("datetime").timedelta(hours=5, minutes=30))


@app.on_event("startup")
async def _start_background_agents() -> None:
    """Background agents that keep the platform running without a human: self-healing (every 10
    min) and the daily overdue-inspection sweep. Both are bounded/idempotent and safe on every
    API task."""
    import asyncio
    from datetime import datetime, timezone

    from sqlalchemy import select, text

    from .db import SessionLocal
    from . import self_heal
    from .models import AuditLog
    from .routers import metrics as metrics_router

    # Cross-process idempotency: the markers below live in the DB (the audit_log rows each sweep
    # already writes), not in per-process memory, so running >1 API task cannot double-send. A
    # Postgres session-level advisory lock serializes the check-then-run so two tasks that tick at
    # the same instant can't both slip through. Lock keys are arbitrary but stable.
    _LOCK_OVERDUE = 918_270_001
    _LOCK_DIGEST = 918_270_002

    def _ran_since(db, action: str, since_utc: datetime) -> bool:
        return db.execute(
            select(AuditLog.id)
            .where(AuditLog.action == action, AuditLog.created_at >= since_utc)
            .limit(1)
        ).first() is not None

    def _try_lock(db, key: int) -> bool:
        return bool(db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar())

    def _unlock(db, key: int) -> None:
        db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})

    def _self_heal() -> None:
        try:
            with SessionLocal() as db:
                self_heal.run(db)
        except Exception:  # noqa: BLE001 - a background error must never crash the API
            logger.exception("self_heal sweep failed")

    def _overdue() -> None:
        # Run at most once per IST day, at/after the sweep hour — DB-gated across all tasks.
        now = datetime.now(_IST)
        if now.hour < OVERDUE_SWEEP_HOUR_IST:
            return
        day_start_utc = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        try:
            with SessionLocal() as db:
                if not _try_lock(db, _LOCK_OVERDUE):
                    return  # another task is running the sweep right now
                try:
                    if _ran_since(db, "overdue_sweep", day_start_utc):
                        return
                    result = metrics_router.overdue_sweep(db)
                    logger.info("overdue sweep: %s", result)
                finally:
                    _unlock(db, _LOCK_OVERDUE)
        except Exception:  # noqa: BLE001
            logger.exception("overdue sweep failed")

    def _weekly_digest() -> None:
        # Generate the fleet digest once per ISO week (Monday morning IST) — DB-gated across tasks.
        now = datetime.now(_IST)
        if now.weekday() != 0 or now.hour < OVERDUE_SWEEP_HOUR_IST:
            return
        week_start_utc = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        try:
            with SessionLocal() as db:
                if not _try_lock(db, _LOCK_DIGEST):
                    return
                try:
                    if _ran_since(db, metrics_router.DIGEST_ACTION, week_start_utc):
                        return
                    metrics_router.generate_and_store_digest(db)
                    logger.info("weekly digest generated for week %s", now.isocalendar()[:2])
                finally:
                    _unlock(db, _LOCK_DIGEST)
        except Exception:  # noqa: BLE001
            logger.exception("weekly digest failed")

    async def _loop() -> None:
        while True:
            await asyncio.sleep(SELF_HEAL_INTERVAL_S)
            await asyncio.to_thread(_self_heal)
            await asyncio.to_thread(_overdue)
            await asyncio.to_thread(_weekly_digest)

    asyncio.create_task(_loop())


@app.get("/healthz", tags=["ops"])
def healthz() -> dict:
    return {"status": "ok"}


# Routers imported after app config so settings/logging are ready.
from .routers import (  # noqa: E402
    admin,
    auth,
    inspections,
    apikeys,
    assistant,
    metrics,
    model,
    public_v1,
    review,
    rewards,
    taxonomy,
    uploads,
    vehicles,
)

app.include_router(auth.router)
app.include_router(vehicles.router)
app.include_router(inspections.router)
app.include_router(uploads.router)
app.include_router(review.router)
app.include_router(metrics.router)
app.include_router(admin.router)
app.include_router(taxonomy.router)
app.include_router(model.router)
app.include_router(rewards.router)
app.include_router(assistant.router)
app.include_router(apikeys.router)
app.include_router(public_v1.router)
