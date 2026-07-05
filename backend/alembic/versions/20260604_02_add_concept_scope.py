"""Add concept scope for cleaner concept names.

Revision ID: 20260604_02
Revises: 20260604_01
Create Date: 2026-06-04
"""

from alembic import op


revision = "20260604_02"
down_revision = "20260604_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE concepts
        ADD COLUMN IF NOT EXISTS scope VARCHAR(200) NOT NULL DEFAULT 'general';
        """
    )
    op.execute("UPDATE concepts SET scope = 'general' WHERE scope IS NULL OR btrim(scope) = '';")
    op.execute("CREATE INDEX IF NOT EXISTS ix_concepts_scope ON concepts (scope);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_concepts_topic_scope ON concepts (topic, scope);")

    # The old schema made `name` globally unique. Scope lets concept names be
    # direct labels while keeping their topic detail in a separate column.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'concepts_name_key'
            ) THEN
                ALTER TABLE concepts DROP CONSTRAINT concepts_name_key;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_concepts_topic_scope_name'
            ) THEN
                ALTER TABLE concepts
                ADD CONSTRAINT uq_concepts_topic_scope_name UNIQUE (topic, scope, name);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE concepts DROP CONSTRAINT IF EXISTS uq_concepts_topic_scope_name;")
    op.execute("DROP INDEX IF EXISTS ix_concepts_topic_scope;")
    op.execute("DROP INDEX IF EXISTS ix_concepts_scope;")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'concepts_name_key'
            ) THEN
                ALTER TABLE concepts ADD CONSTRAINT concepts_name_key UNIQUE (name);
            END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE concepts DROP COLUMN IF EXISTS scope;")
