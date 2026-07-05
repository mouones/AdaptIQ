"""Add indexes to challenge_sessions.user_id and custom_sessions.user_id

Revision ID: 20260516_01
Revises: 20260415_01_add_governance_tables
Create Date: 2026-05-16

These indexes improve dashboard stats query performance, which filters
challenge_sessions and custom_sessions by user_id on every page load.
"""
from alembic import op

revision = "20260516_01"
down_revision = "20260415_01_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_challenge_sessions_user_id",
        "challenge_sessions",
        ["user_id"],
    )
    op.create_index(
        "ix_custom_sessions_user_id",
        "custom_sessions",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_custom_sessions_user_id", table_name="custom_sessions")
    op.drop_index("ix_challenge_sessions_user_id", table_name="challenge_sessions")
