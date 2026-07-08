"""Re-inspection loop: link a re-clean submission back to the inspection it re-does.

Revision ID: 0004_reinspection
Revises: 0003_onboarding
Create Date: 2026-07-06
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_reinspection"
down_revision: Union[str, None] = "0003_onboarding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inspections",
        sa.Column("reinspection_of", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_inspections_reinspection_of",
        "inspections",
        "inspections",
        ["reinspection_of"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_inspections_reinspection_of", "inspections", ["reinspection_of"])


def downgrade() -> None:
    op.drop_index("ix_inspections_reinspection_of", table_name="inspections")
    op.drop_constraint("fk_inspections_reinspection_of", "inspections", type_="foreignkey")
    op.drop_column("inspections", "reinspection_of")
