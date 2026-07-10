"""API keys for third-party integrations.

Adds an api_keys table so an admin can mint keys in the dashboard that external apps use (via the
X-API-Key header) to call the public /v1 endpoints. Only a hash of each key is stored; the
plaintext is shown once at creation.

Revision ID: 0007_api_keys
Revises: 0006_scoring_config_cal
Create Date: 2026-07-08
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0007_api_keys"
down_revision: Union[str, None] = "0006_scoring_config_cal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("key_prefix", sa.String(24), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("api_keys")
