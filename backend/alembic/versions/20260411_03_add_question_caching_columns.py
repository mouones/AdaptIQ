"""Add question caching columns to question_bank

Revision ID: 20260411_03
Revises: 20260411_02
Create Date: 2026-04-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260411_03"
down_revision: Union[str, None] = "20260411_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add missing columns for question caching and concept linking."""

    # Add times_seen column (track how many times this question was served)
    op.execute(
        """
        ALTER TABLE question_bank
        ADD COLUMN IF NOT EXISTS times_seen INTEGER NOT NULL DEFAULT 0;
        """
    )

    # Add last_served_at column (when was this question last used)
    op.execute(
        """
        ALTER TABLE question_bank
        ADD COLUMN IF NOT EXISTS last_served_at TIMESTAMP WITHOUT TIME ZONE;
        """
    )

    # Add primary_concept_id column (link question to its primary concept)
    op.execute(
        """
        ALTER TABLE question_bank
        ADD COLUMN IF NOT EXISTS primary_concept_id UUID REFERENCES concepts(id) ON DELETE SET NULL;
        """
    )

    # Create index on times_seen and last_served_at for efficient queries
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_question_bank_times_seen
        ON question_bank (times_seen DESC);
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_question_bank_last_served
        ON question_bank (last_served_at DESC);
        """
    )

    # Create index on primary_concept_id
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_question_bank_primary_concept
        ON question_bank (primary_concept_id);
        """
    )


def downgrade() -> None:
    """Remove added columns."""

    op.execute("DROP INDEX IF EXISTS ix_question_bank_primary_concept;")
    op.execute("DROP INDEX IF EXISTS ix_question_bank_last_served;")
    op.execute("DROP INDEX IF EXISTS ix_question_bank_times_seen;")

    op.execute(
        """
        ALTER TABLE question_bank
        DROP COLUMN IF EXISTS primary_concept_id;
        """
    )

    op.execute(
        """
        ALTER TABLE question_bank
        DROP COLUMN IF EXISTS last_served_at;
        """
    )

    op.execute(
        """
        ALTER TABLE question_bank
        DROP COLUMN IF EXISTS times_seen;
        """
    )
