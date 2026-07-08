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


@app.get("/healthz", tags=["ops"])
def healthz() -> dict:
    return {"status": "ok"}


# Routers imported after app config so settings/logging are ready.
from .routers import (  # noqa: E402
    admin,
    auth,
    inspections,
    metrics,
    model,
    review,
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
