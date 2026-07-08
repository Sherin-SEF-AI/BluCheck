"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-05

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('driver','admin')", name="ck_users_role"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "vehicles",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("registration_plate", sa.String(32), nullable=False),
        sa.Column("model", sa.String(120)),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("registration_plate", name="uq_vehicles_plate"),
    )
    op.create_index("ix_vehicles_plate", "vehicles", ["registration_plate"])

    op.create_table(
        "inspections",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("vehicle_id", UUID, sa.ForeignKey("vehicles.id"), nullable=False),
        sa.Column("driver_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="uploading"),
        sa.Column("gps_lat", sa.Float()),
        sa.Column("gps_lon", sa.Float()),
        sa.Column("gps_accuracy_m", sa.Float()),
        sa.Column("captured_at_utc", sa.DateTime(timezone=True)),
        sa.Column("captured_at_local", sa.String(64)),
        sa.Column("device_meta", postgresql.JSONB()),
        sa.Column("reviewed_by", UUID, sa.ForeignKey("users.id")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reject_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('uploading','processing','pending','approved','rejected','failed')",
            name="ck_inspections_status",
        ),
    )
    op.create_index("ix_inspections_vehicle_id", "inspections", ["vehicle_id"])
    op.create_index("ix_inspections_driver_id", "inspections", ["driver_id"])
    op.create_index("ix_inspections_status", "inspections", ["status"])
    op.create_index("ix_inspections_created_at", "inspections", ["created_at"])

    op.create_table(
        "captures",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("inspection_id", UUID, sa.ForeignKey("inspections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("video_s3_key", sa.String(512), nullable=False),
        sa.Column("duration_s", sa.Float()),
        sa.Column("recorded_at_utc", sa.DateTime(timezone=True)),
        sa.Column("gps_lat", sa.Float()),
        sa.Column("gps_lon", sa.Float()),
        sa.Column("resolution", sa.String(32)),
        sa.Column("frame_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="uploading"),
        sa.UniqueConstraint("inspection_id", "kind", name="uq_capture_inspection_kind"),
        sa.CheckConstraint("kind IN ('exterior','interior')", name="ck_captures_kind"),
    )

    op.create_table(
        "frames",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("capture_id", UUID, sa.ForeignKey("captures.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("offset_ms", sa.Integer(), nullable=False),
        sa.Column("absolute_ts_utc", sa.DateTime(timezone=True)),
        sa.Column("gps_lat", sa.Float()),
        sa.Column("gps_lon", sa.Float()),
        sa.Column("s3_key_full", sa.String(512), nullable=False),
        sa.Column("s3_key_thumb", sa.String(512), nullable=False),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.UniqueConstraint("capture_id", "seq", name="uq_frame_capture_seq"),
    )

    op.create_table(
        "reviews",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("inspection_id", UUID, sa.ForeignKey("inspections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("admin_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("action IN ('approve','reject')", name="ck_reviews_action"),
    )
    op.create_index("ix_reviews_inspection_id", "reviews", ["inspection_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("actor_id", UUID, sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("detail", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("reviews")
    op.drop_table("frames")
    op.drop_table("captures")
    op.drop_table("inspections")
    op.drop_table("vehicles")
    op.drop_table("users")
