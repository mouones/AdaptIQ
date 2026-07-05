"""Drop global concept-name uniqueness.

Revision ID: 20260604_04
Revises: 20260604_03
Create Date: 2026-06-04
"""

from alembic import op


revision = "20260604_04"
down_revision = "20260604_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Some live databases have a unique index named ix_concepts_name from older
    # schema creation. Concept uniqueness now belongs to topic + scope + name.
    op.execute("DROP INDEX IF EXISTS ix_concepts_name;")
    op.execute("CREATE INDEX IF NOT EXISTS ix_concepts_name ON concepts (name);")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_concepts_name;")
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
