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
    from datetime import datetime

    from .db import SessionLocal
    from . import self_heal
    from .routers import metrics as metrics_router

    last_overdue_day = {"d": None}

    def _self_heal() -> None:
        try:
            with SessionLocal() as db:
                self_heal.run(db)
        except Exception:  # noqa: BLE001 - a background error must never crash the API
            logger.exception("self_heal sweep failed")

    def _overdue() -> None:
        # Run at most once per IST day, at/after the sweep hour.
        now = datetime.now(_IST)
        if now.hour < OVERDUE_SWEEP_HOUR_IST or last_overdue_day["d"] == now.date():
            return
        try:
            with SessionLocal() as db:
                result = metrics_router.overdue_sweep(db)
            last_overdue_day["d"] = now.date()
            logger.info("overdue sweep: %s", result)
        except Exception:  # noqa: BLE001
            logger.exception("overdue sweep failed")

    async def _loop() -> None:
        while True:
            await asyncio.sleep(SELF_HEAL_INTERVAL_S)
            await asyncio.to_thread(_self_heal)
            await asyncio.to_thread(_overdue)

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
