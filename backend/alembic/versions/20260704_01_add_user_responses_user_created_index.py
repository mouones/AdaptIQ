"""add composite index on user_responses (user_id, created_at)

Supports the hot per-user "seen questions" history scan
(classic_service.get_user_seen_question_ids, which filters by user_id and orders
by created_at) and the dashboard stats queries in routers/auth.py that filter by
user_id over a created_at window (daily counts, points, streak). Complements the
existing (user_id, topic) index without duplicating it.

Revision ID: 20260704_01
Revises: 20260611_02
Create Date: 2026-07-04
"""
from alembic import op

revision = "20260704_01"
down_revision = "20260611_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_responses_user_created "
        "ON user_responses (user_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_user_responses_user_created")
