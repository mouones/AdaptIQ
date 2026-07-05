"""Add concept-aware tables and auth/admin user columns

Revision ID: 20260411_01
Revises:
Create Date: 2026-04-11
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260411_01"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Core auth/classic tables so fresh databases can migrate from zero.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY,
            email VARCHAR(255) NOT NULL UNIQUE,
            username VARCHAR(100) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            points INTEGER DEFAULT 0,
            level VARCHAR(30) DEFAULT 'Novice',
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            last_login TIMESTAMP WITHOUT TIME ZONE,
            is_active BOOLEAN DEFAULT TRUE
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS question_bank (
            id UUID PRIMARY KEY,
            question_text TEXT NOT NULL,
            correct_answer TEXT NOT NULL,
            options_json TEXT NOT NULL,
            explanation TEXT NOT NULL,
            topic VARCHAR(20) NOT NULL,
            difficulty_irt DOUBLE PRECISION DEFAULT 2.5,
            discrimination DOUBLE PRECISION DEFAULT 1.0,
            usage_count INTEGER DEFAULT 0,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
            source VARCHAR(30) DEFAULT 'llm'
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_question_bank_topic ON question_bank (topic);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_question_bank_topic_diff ON question_bank (topic, difficulty_irt);"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_responses (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL,
            session_id UUID NOT NULL,
            question_id UUID NOT NULL,
            topic VARCHAR(20) NOT NULL,
            difficulty_sent INTEGER NOT NULL,
            answered_correct BOOLEAN NOT NULL,
            time_taken INTEGER NOT NULL,
            used_hint BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_user_responses_user_id ON user_responses (user_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_user_responses_session_id ON user_responses (session_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_user_responses_user_topic ON user_responses (user_id, topic);")

    # Challenge room tables.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS challenge_sessions (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL,
            topic VARCHAR(30) NOT NULL,
            starting_level INTEGER NOT NULL,
            current_level INTEGER NOT NULL,
            rank_points INTEGER DEFAULT 0,
            streak_correct INTEGER DEFAULT 0,
            streak_wrong INTEGER DEFAULT 0,
            total_questions INTEGER DEFAULT 0,
            correct_answers INTEGER DEFAULT 0,
            started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            ended_at TIMESTAMP WITHOUT TIME ZONE,
            is_completed BOOLEAN DEFAULT FALSE,
            CONSTRAINT fk_challenge_sessions_user_id FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_challenge_sessions_user_id ON challenge_sessions (user_id);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS challenge_answers (
            id UUID PRIMARY KEY,
            session_id UUID NOT NULL,
            question_id UUID NOT NULL,
            chosen_answer TEXT NOT NULL,
            is_correct BOOLEAN NOT NULL,
            points_change INTEGER NOT NULL,
            level_at_answer INTEGER NOT NULL,
            time_taken DOUBLE PRECISION,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_challenge_answers_session_id FOREIGN KEY (session_id) REFERENCES challenge_sessions(id) ON DELETE CASCADE,
            CONSTRAINT fk_challenge_answers_question_id FOREIGN KEY (question_id) REFERENCES question_bank(id) ON DELETE RESTRICT
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_challenge_answers_session_id ON challenge_answers (session_id);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS challenge_ranking (
            user_id UUID PRIMARY KEY,
            current_rank VARCHAR(1) NOT NULL DEFAULT 'E',
            rank_points INTEGER NOT NULL DEFAULT 0,
            total_sessions INTEGER DEFAULT 0,
            total_questions INTEGER DEFAULT 0,
            highest_streak INTEGER DEFAULT 0,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_challenge_ranking_user_id FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
    )

    # Custom room tables.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_topics (
            id UUID PRIMARY KEY,
            type VARCHAR(50) NOT NULL,
            slug VARCHAR(100) NOT NULL UNIQUE,
            name VARCHAR(200) NOT NULL,
            description TEXT,
            total_facts_count INTEGER NOT NULL DEFAULT 0
        );
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_facts (
            id UUID PRIMARY KEY,
            topic VARCHAR(200) NOT NULL,
            content TEXT NOT NULL,
            difficulty_hint VARCHAR(20),
            total_questions_generated INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_custom_facts_topic ON custom_facts (topic);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_topic_mastery (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id),
            topic VARCHAR(200) NOT NULL,
            mastered_facts_count INTEGER NOT NULL DEFAULT 0,
            total_facts_count INTEGER NOT NULL DEFAULT 0,
            last_session_at TIMESTAMP WITHOUT TIME ZONE,
            completion_percentage DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            CONSTRAINT uq_user_topic_mastery UNIQUE (user_id, topic)
        );
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_fact_progress (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id),
            fact_id UUID NOT NULL REFERENCES custom_facts(id),
            is_mastered BOOLEAN NOT NULL DEFAULT FALSE,
            attempts INTEGER NOT NULL DEFAULT 0,
            correct_hits INTEGER NOT NULL DEFAULT 0,
            CONSTRAINT uq_user_fact_progress UNIQUE (user_id, fact_id)
        );
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_sessions (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id),
            topic VARCHAR(200) NOT NULL,
            started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            ended_at TIMESTAMP WITHOUT TIME ZONE,
            total_questions INTEGER NOT NULL DEFAULT 0,
            correct_count INTEGER NOT NULL DEFAULT 0
        );
        """
    )

    # Onboarding tables.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_onboarding_flags (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL UNIQUE REFERENCES users(id),
            first_login BOOLEAN NOT NULL DEFAULT TRUE,
            onboarding_completed BOOLEAN NOT NULL DEFAULT FALSE,
            tour_seen BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        );
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_onboarding_topics (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id),
            topic VARCHAR(200) NOT NULL,
            category VARCHAR(20) NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_user_onboarding_topic UNIQUE (user_id, topic, category)
        );
        """
    )

    # Auth and concept extensions.
    op.execute(
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE;
        """
    )
    op.execute(
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS elo_global DOUBLE PRECISION NOT NULL DEFAULT 0.0;
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS concepts (
            id UUID PRIMARY KEY,
            name VARCHAR(255) NOT NULL UNIQUE,
            topic VARCHAR(50) NOT NULL,
            description TEXT,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        );
        """
    )

    op.execute("CREATE INDEX IF NOT EXISTS ix_concepts_name ON concepts (name);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_concepts_topic ON concepts (topic);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS question_concepts (
            id UUID PRIMARY KEY,
            question_id UUID NOT NULL REFERENCES question_bank(id) ON DELETE CASCADE,
            concept_id UUID NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
            is_primary BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_question_concept UNIQUE (question_id, concept_id)
        );
        """
    )

    op.execute("CREATE INDEX IF NOT EXISTS ix_question_concepts_question_id ON question_concepts (question_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_question_concepts_concept_id ON question_concepts (concept_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_question_concepts_primary ON question_concepts (question_id, is_primary);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_concept_theta (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            concept_id UUID NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
            theta DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            theta_variance DOUBLE PRECISION NOT NULL DEFAULT 1.0,
            response_count INTEGER NOT NULL DEFAULT 0,
            exposure_count INTEGER NOT NULL DEFAULT 0,
            mastery_level VARCHAR(20) NOT NULL DEFAULT 'BEGINNER',
            first_seen_at TIMESTAMP WITHOUT TIME ZONE,
            last_played_at TIMESTAMP WITHOUT TIME ZONE,
            last_updated TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_user_concept_theta UNIQUE (user_id, concept_id)
        );
        """
    )

    op.execute("CREATE INDEX IF NOT EXISTS ix_user_concept_theta_user_id ON user_concept_theta (user_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_user_concept_theta_concept_id ON user_concept_theta (concept_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_user_concept_theta_last_updated ON user_concept_theta (last_updated);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_concept_repeat_queue (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            concept_id UUID NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
            question_id UUID NOT NULL REFERENCES question_bank(id) ON DELETE CASCADE,
            repeat_probability DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            due_after_session INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        );
        """
    )

    op.execute("CREATE INDEX IF NOT EXISTS ix_user_concept_repeat_queue_user_id ON user_concept_repeat_queue (user_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_user_concept_repeat_queue_concept_id ON user_concept_repeat_queue (concept_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_user_concept_repeat_queue_question_id ON user_concept_repeat_queue (question_id);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_onboarding_topics;")
    op.execute("DROP TABLE IF EXISTS user_onboarding_flags;")
    op.execute("DROP TABLE IF EXISTS custom_sessions;")
    op.execute("DROP TABLE IF EXISTS user_fact_progress;")
    op.execute("DROP TABLE IF EXISTS user_topic_mastery;")
    op.execute("DROP TABLE IF EXISTS custom_facts;")
    op.execute("DROP TABLE IF EXISTS custom_topics;")
    op.execute("DROP TABLE IF EXISTS challenge_answers;")
    op.execute("DROP TABLE IF EXISTS challenge_sessions;")
    op.execute("DROP TABLE IF EXISTS challenge_ranking;")
    op.execute("DROP TABLE IF EXISTS user_concept_repeat_queue;")
    op.execute("DROP TABLE IF EXISTS user_concept_theta;")
    op.execute("DROP TABLE IF EXISTS question_concepts;")
    op.execute("DROP TABLE IF EXISTS concepts;")

    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS elo_global;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_admin;")
    op.execute("DROP TABLE IF EXISTS user_responses;")
    op.execute("DROP TABLE IF EXISTS question_bank;")
    op.execute("DROP TABLE IF EXISTS users;")
