"""Seed module runnable inside the API image via `python -m app.seed`.

Used by the one-off ECS task that seeds the private RDS instance, since the database is
not reachable from outside the VPC. Idempotent. Credentials come from environment:
ADMIN_EMAIL / ADMIN_PASSWORD / DRIVER_EMAIL / DRIVER_PASSWORD (defaults for convenience).
"""

from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import hash_password
from .db import get_engine
from .modelcfg import ensure_active_model_version
from .models import User, Vehicle

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@blurabbit.in")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ChangeMe!2026")
ADMIN_NAME = os.environ.get("ADMIN_NAME", "Fleet Administrator")


def main() -> None:
    # Only the admin is seeded. Drivers and their vehicles are created via self-onboarding
    # (name + car number) in the mobile app.
    with Session(get_engine()) as db:
        if db.execute(select(User).where(User.email == ADMIN_EMAIL)).scalar_one_or_none():
            print(f"admin exists: {ADMIN_EMAIL}", flush=True)
        else:
            db.add(User(email=ADMIN_EMAIL, name=ADMIN_NAME, role="admin", password_hash=hash_password(ADMIN_PASSWORD), active=True))
            print(f"created admin: {ADMIN_EMAIL}", flush=True)
        db.commit()
        mv = ensure_active_model_version(db)
        print(f"model_version: {mv.name} mode={mv.mode}", flush=True)
    print("seed complete", flush=True)


if __name__ == "__main__":
    main()
