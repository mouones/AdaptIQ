"""add shadow calibration columns to question_bank

Supports the offline difficulty-recalibration job (roadmap item 2): the job writes
a learned difficulty into difficulty_irt_calibrated without touching the served
difficulty_irt, so recalibration stays off the request path and is reversible until
a value is reviewed and promoted.

Revision ID: 20260704_02
Revises: 20260704_01
Create Date: 2026-07-04
"""
from alembic import op

revision = "20260704_02"
down_revision = "20260704_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE question_bank ADD COLUMN IF NOT EXISTS difficulty_irt_calibrated DOUBLE PRECISION NULL")
    op.execute("ALTER TABLE question_bank ADD COLUMN IF NOT EXISTS calibrated_at TIMESTAMP NULL")
    op.execute("ALTER TABLE question_bank ADD COLUMN IF NOT EXISTS calibration_sample INTEGER NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE question_bank DROP COLUMN IF EXISTS difficulty_irt_calibrated")
    op.execute("ALTER TABLE question_bank DROP COLUMN IF EXISTS calibrated_at")
    op.execute("ALTER TABLE question_bank DROP COLUMN IF EXISTS calibration_sample")
