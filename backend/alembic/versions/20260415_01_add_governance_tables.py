"""Add governance columns and audit/blocklist tables

Revision ID: 20260415_01_governance
Revises: 20260413_01_answer_uniqueness
Create Date: 2026-04-15

Adds:
  - question_bank governance columns (gov_approved, scores, sources)
  - governance_block_rules (DB-persisted block topics/keywords)
  - question_audits (persist + serve governance decisions)
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260415_01_governance"
down_revision = "20260413_01_answer_uniqueness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── question_bank: governance fields ─────────────────────────────────
    op.execute(
        """
        ALTER TABLE question_bank
        ADD COLUMN IF NOT EXISTS gov_approved BOOLEAN NOT NULL DEFAULT TRUE;
        """
    )

    op.execute(
        """
        ALTER TABLE question_bank
        ADD COLUMN IF NOT EXISTS gov_safe BOOLEAN NOT NULL DEFAULT TRUE;
        """
    )

    op.execute(
        """
        ALTER TABLE question_bank
        ADD COLUMN IF NOT EXISTS gov_confidence DOUBLE PRECISION;
        """
    )

    op.execute(
        """
        ALTER TABLE question_bank
        ADD COLUMN IF NOT EXISTS gov_fact_trust DOUBLE PRECISION;
        """
    )

    op.execute(
        """
        ALTER TABLE question_bank
        ADD COLUMN IF NOT EXISTS gov_narrative_quality DOUBLE PRECISION;
        """
    )

    op.execute(
        """
        ALTER TABLE question_bank
        ADD COLUMN IF NOT EXISTS gov_sources_json TEXT;
        """
    )

    op.execute(
        """
        ALTER TABLE question_bank
        ADD COLUMN IF NOT EXISTS gov_flags_json TEXT;
        """
    )

    op.execute(
        """
        ALTER TABLE question_bank
        ADD COLUMN IF NOT EXISTS gov_checked_at TIMESTAMP WITHOUT TIME ZONE;
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_question_bank_gov_approved
        ON question_bank (gov_approved);
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_question_bank_gov_safe
        ON question_bank (gov_safe);
        """
    )

    # ── governance_block_rules ───────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS governance_block_rules (
            id UUID PRIMARY KEY,
            kind VARCHAR(20) NOT NULL,
            pattern TEXT NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE,
            created_by UUID
        );
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_governance_block_rules_kind_pattern
        ON governance_block_rules (kind, pattern);
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_governance_block_rules_active
        ON governance_block_rules (is_active);
        """
    )

    # ── question_audits ──────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS question_audits (
            id UUID PRIMARY KEY,
            question_id UUID,
            room VARCHAR(30),
            topic VARCHAR(80),
            action VARCHAR(30) NOT NULL,
            approved BOOLEAN NOT NULL,
            reasons_json TEXT,
            confidence DOUBLE PRECISION,
            sources_json TEXT,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            question_text TEXT,
            correct_answer TEXT,
            options_json TEXT,
            explanation TEXT
        );
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_question_audits_question_id
        ON question_audits (question_id);
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_question_audits_created_at
        ON question_audits (created_at DESC);
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_question_audits_action_approved
        ON question_audits (action, approved);
        """
    )


def downgrade() -> None:
    # ── question_audits ──────────────────────────────────────────────────
    op.execute("DROP INDEX IF EXISTS ix_question_audits_action_approved;")
    op.execute("DROP INDEX IF EXISTS ix_question_audits_created_at;")
    op.execute("DROP INDEX IF EXISTS ix_question_audits_question_id;")
    op.execute("DROP TABLE IF EXISTS question_audits;")

    # ── governance_block_rules ───────────────────────────────────────────
    op.execute("DROP INDEX IF EXISTS ix_governance_block_rules_active;")
    op.execute("DROP INDEX IF EXISTS uq_governance_block_rules_kind_pattern;")
    op.execute("DROP TABLE IF EXISTS governance_block_rules;")

    # ── question_bank ────────────────────────────────────────────────────
    op.execute("DROP INDEX IF EXISTS ix_question_bank_gov_safe;")
    op.execute("DROP INDEX IF EXISTS ix_question_bank_gov_approved;")

    op.execute("ALTER TABLE question_bank DROP COLUMN IF EXISTS gov_checked_at;")
    op.execute("ALTER TABLE question_bank DROP COLUMN IF EXISTS gov_flags_json;")
    op.execute("ALTER TABLE question_bank DROP COLUMN IF EXISTS gov_sources_json;")
    op.execute("ALTER TABLE question_bank DROP COLUMN IF EXISTS gov_narrative_quality;")
    op.execute("ALTER TABLE question_bank DROP COLUMN IF EXISTS gov_fact_trust;")
    op.execute("ALTER TABLE question_bank DROP COLUMN IF EXISTS gov_confidence;")
    op.execute("ALTER TABLE question_bank DROP COLUMN IF EXISTS gov_safe;")
    op.execute("ALTER TABLE question_bank DROP COLUMN IF EXISTS gov_approved;")
