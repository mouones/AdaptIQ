"""Add user_id to question_audits for traceability

Revision ID: 20260516_02
Revises: 20260516_01
Create Date: 2026-05-16

Adds user_id column to question_audits so governance audit entries
can record which user triggered the content generation.
"""
from alembic import op

revision = "20260516_02"
down_revision = "20260516_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE question_audits
        ADD COLUMN IF NOT EXISTS user_id UUID;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_question_audits_user_id
        ON question_audits (user_id);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_question_audits_user_id;")
    op.execute("ALTER TABLE question_audits DROP COLUMN IF EXISTS user_id;")
