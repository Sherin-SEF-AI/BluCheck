"""intelligence layer foundation: taxonomy, structured labels, scoring seams

Adds the data foundation for the BluCheck intelligence layer. Taxonomy and structured
review labels are used now; the model_versions / scoring_results / zone_scores tables are
created as clean seams for the (later) self-hosted VLM scoring stage and stay empty until
that stage is deployed.

Revision ID: 0002_intelligence
Revises: 0001_initial
Create Date: 2026-07-06
"""

from typing import Sequence, Union
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_intelligence"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)

ZONES = [
    ("exterior_body", "Exterior body"),
    ("windows_glass", "Windows and glass"),
    ("seats", "Seats"),
    ("floor_mats", "Floor and mats"),
    ("dashboard_console", "Dashboard and console"),
    ("boot", "Boot"),
]
ISSUES = [
    ("trash", "Trash"),
    ("stain", "Stain"),
    ("dust", "Dust"),
    ("smudge", "Smudge"),
    ("spill", "Spill"),
    ("mud", "Mud"),
]


def upgrade() -> None:
    # ----- Taxonomy -----
    op.create_table(
        "taxonomy_zones",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("key", sa.String(32), nullable=False, unique=True),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "taxonomy_issues",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("key", sa.String(32), nullable=False, unique=True),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.bulk_insert(
        sa.table(
            "taxonomy_zones",
            sa.column("id", UUID),
            sa.column("key", sa.String),
            sa.column("label", sa.String),
            sa.column("active", sa.Boolean),
        ),
        [{"id": uuid.uuid4(), "key": k, "label": l, "active": True} for k, l in ZONES],
    )
    op.bulk_insert(
        sa.table(
            "taxonomy_issues",
            sa.column("id", UUID),
            sa.column("key", sa.String),
            sa.column("label", sa.String),
            sa.column("active", sa.Boolean),
        ),
        [{"id": uuid.uuid4(), "key": k, "label": l, "active": True} for k, l in ISSUES],
    )

    # ----- frames: selection + classification columns -----
    op.add_column("frames", sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("frames", sa.Column("blur_score", sa.Float()))
    op.add_column("frames", sa.Column("exposure_score", sa.Float()))
    op.add_column("frames", sa.Column("phash", sa.String(32)))
    op.add_column("frames", sa.Column("zone_key", sa.String(32)))

    # ----- model_versions (seam for the VLM scoring stage) -----
    op.create_table(
        "model_versions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("vlm_model", sa.String(120)),
        sa.Column("prompt_version", sa.String(64)),
        sa.Column("thresholds", postgresql.JSONB()),
        sa.Column("mode", sa.String(16), nullable=False, server_default="shadow"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("mode IN ('shadow','assist','auto','disabled')", name="ck_model_versions_mode"),
    )

    # ----- scoring_results + zone_scores (seams) -----
    op.create_table(
        "scoring_results",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("inspection_id", UUID, sa.ForeignKey("inspections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_version_id", UUID, sa.ForeignKey("model_versions.id"), nullable=False),
        sa.Column("overall_score", sa.Float()),
        sa.Column("overall_confidence", sa.Float()),
        sa.Column("decision", sa.String(16), nullable=False, server_default="none"),
        sa.Column("raw_json", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("inspection_id", "model_version_id", name="uq_scoring_inspection_model"),
        sa.CheckConstraint("decision IN ('none','auto_approve','auto_reject','route_human')", name="ck_scoring_decision"),
    )
    op.create_index("ix_scoring_results_inspection_id", "scoring_results", ["inspection_id"])

    op.create_table(
        "zone_scores",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("scoring_result_id", UUID, sa.ForeignKey("scoring_results.id", ondelete="CASCADE"), nullable=False),
        sa.Column("zone_key", sa.String(32), nullable=False),
        sa.Column("score", sa.Float()),
        sa.Column("confidence", sa.Float()),
        sa.Column("issues", postgresql.JSONB()),
    )
    op.create_index("ix_zone_scores_result", "zone_scores", ["scoring_result_id"])

    # ----- reviews: source, scoring link, viewed frames (ground-truth capture) -----
    op.add_column("reviews", sa.Column("source", sa.String(24), nullable=False, server_default="human"))
    op.add_column("reviews", sa.Column("scoring_result_id", UUID, sa.ForeignKey("scoring_results.id")))
    op.add_column("reviews", sa.Column("viewed_frame_ids", postgresql.JSONB()))
    op.create_check_constraint(
        "ck_reviews_source", "reviews", "source IN ('human','model_confirmed','model_overridden')"
    )

    op.create_table(
        "review_zone_labels",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("review_id", UUID, sa.ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("zone_key", sa.String(32), nullable=False),
        sa.Column("issue_key", sa.String(32), nullable=False),
    )
    op.create_index("ix_review_zone_labels_review", "review_zone_labels", ["review_id"])


def downgrade() -> None:
    op.drop_table("review_zone_labels")
    op.drop_constraint("ck_reviews_source", "reviews", type_="check")
    op.drop_column("reviews", "viewed_frame_ids")
    op.drop_column("reviews", "scoring_result_id")
    op.drop_column("reviews", "source")
    op.drop_table("zone_scores")
    op.drop_table("scoring_results")
    op.drop_table("model_versions")
    op.drop_column("frames", "zone_key")
    op.drop_column("frames", "phash")
    op.drop_column("frames", "exposure_score")
    op.drop_column("frames", "blur_score")
    op.drop_column("frames", "selected")
    op.drop_table("taxonomy_issues")
    op.drop_table("taxonomy_zones")
