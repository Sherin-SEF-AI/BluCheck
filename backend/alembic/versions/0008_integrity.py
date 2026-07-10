"""Integrity / fraud signals per inspection.

Adds a nullable JSONB column holding the fraud-detection result (risk level + reasons + signals)
computed at decision time: reused footage (perceptual-hash match to another inspection), GPS
anomalies, and rapid resubmission. Nullable, so it is a no-op for existing rows.

Revision ID: 0008_integrity
Revises: 0007_api_keys
Create Date: 2026-07-10
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0008_integrity"
down_revision: Union[str, None] = "0007_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inspections", sa.Column("integrity", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("inspections", "integrity")
