"""Add source question provenance to custom facts.

Revision ID: 20260604_01
Revises: 20260530_01
Create Date: 2026-06-04
"""

from alembic import op


revision = "20260604_01"
down_revision = "20260530_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE custom_facts
        ADD COLUMN IF NOT EXISTS source_question_id UUID NULL
        REFERENCES question_bank(id) ON DELETE SET NULL;
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_custom_facts_source_question_id
        ON custom_facts (source_question_id)
        WHERE source_question_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_custom_facts_source_question_id;")
    op.execute("ALTER TABLE custom_facts DROP COLUMN IF EXISTS source_question_id;")
