"""add visual session total time

Revision ID: 20260611_01
Revises: 20260604_04
Create Date: 2026-06-11
"""

from alembic import op


revision = "20260611_01"
down_revision = "20260604_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE visual_sessions "
        "ADD COLUMN IF NOT EXISTS total_time_ms INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE visual_sessions DROP COLUMN IF EXISTS total_time_ms")
