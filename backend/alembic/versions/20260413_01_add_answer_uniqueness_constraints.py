"""add answer uniqueness constraints

Revision ID: 20260413_01_answer_uniqueness
Revises: 20260412_01_add_pvp
Create Date: 2026-04-13

Adds unique constraints to prevent duplicate answer submissions under concurrency:
  - challenge_answers(session_id, question_id)
  - pvp_match_answers(match_id, user_id, question_index)
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260413_01_answer_uniqueness"
down_revision = "20260412_01_add_pvp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Deduplicate historical challenge answers before adding unique constraint.
    op.execute(
        """
        DELETE FROM challenge_answers ca
        USING (
            SELECT id
            FROM (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY session_id, question_id
                        ORDER BY created_at ASC, id ASC
                    ) AS rn
                FROM challenge_answers
            ) t
            WHERE t.rn > 1
        ) d
        WHERE ca.id = d.id;
        """
    )

    # Deduplicate historical PvP answers before adding unique constraint.
    op.execute(
        """
        DELETE FROM pvp_match_answers pa
        USING (
            SELECT id
            FROM (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY match_id, user_id, question_index
                        ORDER BY answered_at ASC, id ASC
                    ) AS rn
                FROM pvp_match_answers
            ) t
            WHERE t.rn > 1
        ) d
        WHERE pa.id = d.id;
        """
    )

    op.create_unique_constraint(
        "uq_challenge_answer_session_question",
        "challenge_answers",
        ["session_id", "question_id"],
    )

    op.create_unique_constraint(
        "uq_pvp_answer_match_user_index",
        "pvp_match_answers",
        ["match_id", "user_id", "question_index"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_pvp_answer_match_user_index",
        "pvp_match_answers",
        type_="unique",
    )

    op.drop_constraint(
        "uq_challenge_answer_session_question",
        "challenge_answers",
        type_="unique",
    )
