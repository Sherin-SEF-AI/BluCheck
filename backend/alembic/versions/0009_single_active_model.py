"""Enforce at most one active model_version.

`ensure_active_model_version` did a read-then-insert with no DB constraint, so two concurrent
first-requests could each insert an active row; every later single-row read then threw
MultipleResultsFound and 500'd the platform. This adds a partial-unique index so the database
guarantees a single active row. Any pre-existing duplicates are collapsed to the earliest one
before the index is created.

Revision ID: 0009_single_active_model
Revises: 0008_integrity
Create Date: 2026-07-16
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_single_active_model"
down_revision: Union[str, None] = "0008_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Collapse any existing duplicate active rows to the earliest, so the unique index can build.
    op.execute(
        """
        UPDATE model_versions SET active = false
        WHERE active = true
          AND id NOT IN (
            SELECT id FROM model_versions WHERE active = true
            ORDER BY created_at ASC LIMIT 1
          )
        """
    )
    op.create_index(
        "uq_model_versions_single_active",
        "model_versions",
        ["active"],
        unique=True,
        postgresql_where=sa.text("active"),
    )


def downgrade() -> None:
    op.drop_index("uq_model_versions_single_active", table_name="model_versions")
