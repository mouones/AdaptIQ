"""Harden challenge and concept constraints

Revision ID: 20260411_02
Revises: 20260411_01
Create Date: 2026-04-11
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260411_02"
down_revision: Union[str, None] = "20260411_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Challenge session level bounds.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_challenge_sessions_starting_level'
            ) THEN
                ALTER TABLE challenge_sessions
                ADD CONSTRAINT ck_challenge_sessions_starting_level
                CHECK (starting_level BETWEEN 1 AND 5);
            END IF;
        END
        $$;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_challenge_sessions_current_level'
            ) THEN
                ALTER TABLE challenge_sessions
                ADD CONSTRAINT ck_challenge_sessions_current_level
                CHECK (current_level BETWEEN 1 AND 5);
            END IF;
        END
        $$;
        """
    )

    # Challenge anti-abuse and rank integrity.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'uq_challenge_answers_session_question'
            ) THEN
                ALTER TABLE challenge_answers
                ADD CONSTRAINT uq_challenge_answers_session_question
                UNIQUE (session_id, question_id);
            END IF;
        END
        $$;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_challenge_ranking_current_rank'
            ) THEN
                ALTER TABLE challenge_ranking
                ADD CONSTRAINT ck_challenge_ranking_current_rank
                CHECK (current_rank IN ('E', 'D', 'C', 'B', 'A'));
            END IF;
        END
        $$;
        """
    )

    # Concept theta bounds.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_user_concept_theta_range'
            ) THEN
                ALTER TABLE user_concept_theta
                ADD CONSTRAINT ck_user_concept_theta_range
                CHECK (theta >= -3.0 AND theta <= 3.0);
            END IF;
        END
        $$;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_user_concept_theta_variance_positive'
            ) THEN
                ALTER TABLE user_concept_theta
                ADD CONSTRAINT ck_user_concept_theta_variance_positive
                CHECK (theta_variance > 0.0);
            END IF;
        END
        $$;
        """
    )

    # Helps fetch due repeats per user quickly.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_repeat_queue_user_due
        ON user_concept_repeat_queue (user_id, due_after_session);
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE challenge_sessions DROP CONSTRAINT IF EXISTS ck_challenge_sessions_starting_level;")
    op.execute("ALTER TABLE challenge_sessions DROP CONSTRAINT IF EXISTS ck_challenge_sessions_current_level;")
    op.execute("ALTER TABLE challenge_answers DROP CONSTRAINT IF EXISTS uq_challenge_answers_session_question;")
    op.execute("ALTER TABLE challenge_ranking DROP CONSTRAINT IF EXISTS ck_challenge_ranking_current_rank;")
    op.execute("ALTER TABLE user_concept_theta DROP CONSTRAINT IF EXISTS ck_user_concept_theta_range;")
    op.execute("ALTER TABLE user_concept_theta DROP CONSTRAINT IF EXISTS ck_user_concept_theta_variance_positive;")
    op.execute("DROP INDEX IF EXISTS ix_repeat_queue_user_due;")
