"""driver onboarding (car-number login), plate OCR, and push tokens

Adds car-number based driver identity, an Expo push token, and per-inspection plate OCR
integrity fields. Email becomes nullable since drivers onboard with a car number instead.

Revision ID: 0003_onboarding
Revises: 0002_intelligence
Create Date: 2026-07-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_onboarding"
down_revision: Union[str, None] = "0002_intelligence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drivers log in with their car number; admins keep email. Either may be null.
    op.add_column("users", sa.Column("car_number", sa.String(32)))
    op.add_column("users", sa.Column("push_token", sa.String(512)))
    op.alter_column("users", "email", existing_type=sa.String(255), nullable=True)
    op.create_unique_constraint("uq_users_car_number", "users", ["car_number"])
    op.create_index("ix_users_car_number", "users", ["car_number"])

    # Pre-inspection plate OCR (soft-flagged integrity check).
    op.add_column("inspections", sa.Column("ocr_plate", sa.String(32)))
    op.add_column("inspections", sa.Column("ocr_matched", sa.Boolean()))


def downgrade() -> None:
    op.drop_column("inspections", "ocr_matched")
    op.drop_column("inspections", "ocr_plate")
    op.drop_index("ix_users_car_number", "users")
    op.drop_constraint("uq_users_car_number", "users", type_="unique")
    op.alter_column("users", "email", existing_type=sa.String(255), nullable=False)
    op.drop_column("users", "push_token")
    op.drop_column("users", "car_number")
