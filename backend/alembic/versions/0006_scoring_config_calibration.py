"""Per-version tunable scoring config and confidence calibration curve.

Adds two nullable JSONB columns to model_versions so the scoring math (blend weight, zone
weights, severity caps, borderline/zoom, image cap) and the confidence->correctness
calibration can be tuned and versioned per ModelVersion without a redeploy. Both are nullable:
when absent, the worker/decision layer fall back to today's hardcoded constants, so this
migration is a no-op for existing rows and existing behavior.

Revision ID: 0006_scoring_config_cal
Revises: 0005_perf_indexes
Create Date: 2026-07-07
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0006_scoring_config_cal"
down_revision: Union[str, None] = "0005_perf_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_versions", sa.Column("scoring_config", JSONB(), nullable=True))
    op.add_column("model_versions", sa.Column("calibration", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("model_versions", "calibration")
    op.drop_column("model_versions", "scoring_config")
