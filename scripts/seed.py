"""Idempotent seed: create the initial admin user and a few sample vehicles.

Reads DATABASE_URL from the environment. Admin credentials come from ADMIN_EMAIL /
ADMIN_PASSWORD (defaults provided for local development only).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the backend app importable.
BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth import hash_password  # noqa: E402
from app.models import User, Vehicle  # noqa: E402

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@blurabbit.in")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ChangeMe!2026")
ADMIN_NAME = os.environ.get("ADMIN_NAME", "Fleet Administrator")

DRIVER_EMAIL = os.environ.get("DRIVER_EMAIL", "driver@blurabbit.in")
DRIVER_PASSWORD = os.environ.get("DRIVER_PASSWORD", "ChangeMe!2026")

SAMPLE_VEHICLES = [
    ("MH01AB1234", "Tata Nexon EV"),
    ("MH02CD5678", "Mahindra XUV400"),
    ("MH03EF9012", "Maruti Dzire"),
    ("MH04GH3456", "Hyundai Aura"),
]


def upsert_user(db: Session, email: str, name: str, role: str, password: str) -> None:
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        print(f"user exists: {email}")
        return
    db.add(User(email=email, name=name, role=role, password_hash=hash_password(password), active=True))
    print(f"created {role}: {email}")


def main() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")

    engine = create_engine(url, future=True)
    with Session(engine) as db:
        upsert_user(db, ADMIN_EMAIL, ADMIN_NAME, "admin", ADMIN_PASSWORD)
        upsert_user(db, DRIVER_EMAIL, "Sample Driver", "driver", DRIVER_PASSWORD)

        for plate, model in SAMPLE_VEHICLES:
            exists = db.execute(
                select(Vehicle).where(Vehicle.registration_plate == plate)
            ).scalar_one_or_none()
            if exists:
                print(f"vehicle exists: {plate}")
                continue
            db.add(Vehicle(registration_plate=plate, model=model, active=True))
            print(f"created vehicle: {plate}")

        db.commit()
    print("seed complete")


if __name__ == "__main__":
    main()
