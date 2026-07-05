"""add visual session streak counters

Revision ID: 20260611_02
Revises: 20260611_01
Create Date: 2026-06-11
"""
from alembic import op

revision = "20260611_02"
down_revision = "20260611_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE visual_sessions ADD COLUMN IF NOT EXISTS streak_correct INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE visual_sessions ADD COLUMN IF NOT EXISTS streak_wrong INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE visual_sessions DROP COLUMN IF EXISTS streak_correct")
    op.execute("ALTER TABLE visual_sessions DROP COLUMN IF EXISTS streak_wrong")
