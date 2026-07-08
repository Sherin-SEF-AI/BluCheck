"""Performance indexes for fleet-scale aggregate queries (activity, performance, trends).

Revision ID: 0005_perf_indexes
Revises: 0004_reinspection
Create Date: 2026-07-07
"""

from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "0005_perf_indexes"
down_revision: Union[str, None] = "0004_reinspection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Latest-scoring-per-inspection lookups and recent-window scans.
    op.create_index(
        "ix_scoring_results_inspection_created",
        "scoring_results",
        ["inspection_id", "created_at"],
    )
    op.create_index("ix_scoring_results_created_at", "scoring_results", ["created_at"])
    # Zone-reason lookups per scoring result (used on every feed/detail render).
    op.create_index("ix_zone_scores_scoring_result_id", "zone_scores", ["scoring_result_id"])


def downgrade() -> None:
    op.drop_index("ix_zone_scores_scoring_result_id", table_name="zone_scores")
    op.drop_index("ix_scoring_results_created_at", table_name="scoring_results")
    op.drop_index("ix_scoring_results_inspection_created", table_name="scoring_results")
