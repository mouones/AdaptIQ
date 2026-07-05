"""Create Visual Room tables.

Revision ID: 20260604_03
Revises: 20260604_02
Create Date: 2026-06-04
"""

from alembic import op


revision = "20260604_03"
down_revision = "20260604_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The earlier Visual autogen migration is an empty chain placeholder. These
    # idempotent statements make fresh and existing databases converge.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS visual_questions (
            id UUID PRIMARY KEY,
            coco_image_id INTEGER,
            image_url TEXT NOT NULL,
            iso2 VARCHAR(2),
            shape_svg TEXT,
            paragraph TEXT,
            topic VARCHAR(20) NOT NULL,
            difficulty_base DOUBLE PRECISION NOT NULL DEFAULT 3.0,
            difficulty_actual DOUBLE PRECISION NOT NULL DEFAULT 3.0,
            options_count INTEGER NOT NULL DEFAULT 4,
            question_type CHAR(1) NOT NULL DEFAULT 'M',
            question_text TEXT,
            correct_answer TEXT,
            options_json TEXT,
            explanation TEXT,
            n_attempts INTEGER NOT NULL DEFAULT 0,
            n_correct INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("ALTER TABLE visual_questions ADD COLUMN IF NOT EXISTS coco_image_id INTEGER;")
    op.execute("ALTER TABLE visual_questions ADD COLUMN IF NOT EXISTS image_url TEXT NOT NULL DEFAULT '';")
    op.execute("ALTER TABLE visual_questions ADD COLUMN IF NOT EXISTS iso2 VARCHAR(2);")
    op.execute("ALTER TABLE visual_questions ADD COLUMN IF NOT EXISTS shape_svg TEXT;")
    op.execute("ALTER TABLE visual_questions ADD COLUMN IF NOT EXISTS paragraph TEXT;")
    op.execute("ALTER TABLE visual_questions ADD COLUMN IF NOT EXISTS topic VARCHAR(20) NOT NULL DEFAULT 'mixed';")
    op.execute("ALTER TABLE visual_questions ADD COLUMN IF NOT EXISTS difficulty_base DOUBLE PRECISION NOT NULL DEFAULT 3.0;")
    op.execute("ALTER TABLE visual_questions ADD COLUMN IF NOT EXISTS difficulty_actual DOUBLE PRECISION NOT NULL DEFAULT 3.0;")
    op.execute("ALTER TABLE visual_questions ADD COLUMN IF NOT EXISTS options_count INTEGER NOT NULL DEFAULT 4;")
    op.execute("ALTER TABLE visual_questions ADD COLUMN IF NOT EXISTS question_type CHAR(1) NOT NULL DEFAULT 'M';")
    op.execute("ALTER TABLE visual_questions ADD COLUMN IF NOT EXISTS question_text TEXT;")
    op.execute("ALTER TABLE visual_questions ADD COLUMN IF NOT EXISTS correct_answer TEXT;")
    op.execute("ALTER TABLE visual_questions ADD COLUMN IF NOT EXISTS options_json TEXT;")
    op.execute("ALTER TABLE visual_questions ADD COLUMN IF NOT EXISTS explanation TEXT;")
    op.execute("ALTER TABLE visual_questions ADD COLUMN IF NOT EXISTS n_attempts INTEGER NOT NULL DEFAULT 0;")
    op.execute("ALTER TABLE visual_questions ADD COLUMN IF NOT EXISTS n_correct INTEGER NOT NULL DEFAULT 0;")
    op.execute("ALTER TABLE visual_questions ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW();")
    op.execute("ALTER TABLE visual_questions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW();")
    op.execute("CREATE INDEX IF NOT EXISTS ix_visual_questions_coco_image_id ON visual_questions (coco_image_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_visual_questions_iso2 ON visual_questions (iso2);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_visual_questions_topic ON visual_questions (topic);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_visual_topic_diff ON visual_questions (topic, difficulty_actual);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_visual_coco_id ON visual_questions (coco_image_id);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS visual_sessions (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL,
            topic VARCHAR(20) NOT NULL,
            level INTEGER NOT NULL,
            current_index INTEGER NOT NULL DEFAULT 0,
            total_questions INTEGER NOT NULL DEFAULT 10,
            score INTEGER NOT NULL DEFAULT 0,
            seen_ids_json TEXT NOT NULL DEFAULT '[]',
            started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            ended_at TIMESTAMP WITHOUT TIME ZONE,
            is_completed BOOLEAN NOT NULL DEFAULT FALSE
        );
        """
    )
    op.execute("ALTER TABLE visual_sessions ADD COLUMN IF NOT EXISTS user_id UUID NOT NULL;")
    op.execute("ALTER TABLE visual_sessions ADD COLUMN IF NOT EXISTS topic VARCHAR(20) NOT NULL DEFAULT 'mixed';")
    op.execute("ALTER TABLE visual_sessions ADD COLUMN IF NOT EXISTS level INTEGER NOT NULL DEFAULT 1;")
    op.execute("ALTER TABLE visual_sessions ADD COLUMN IF NOT EXISTS current_index INTEGER NOT NULL DEFAULT 0;")
    op.execute("ALTER TABLE visual_sessions ADD COLUMN IF NOT EXISTS total_questions INTEGER NOT NULL DEFAULT 10;")
    op.execute("ALTER TABLE visual_sessions ADD COLUMN IF NOT EXISTS score INTEGER NOT NULL DEFAULT 0;")
    op.execute("ALTER TABLE visual_sessions ADD COLUMN IF NOT EXISTS seen_ids_json TEXT NOT NULL DEFAULT '[]';")
    op.execute("ALTER TABLE visual_sessions ADD COLUMN IF NOT EXISTS started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW();")
    op.execute("ALTER TABLE visual_sessions ADD COLUMN IF NOT EXISTS ended_at TIMESTAMP WITHOUT TIME ZONE;")
    op.execute("ALTER TABLE visual_sessions ADD COLUMN IF NOT EXISTS is_completed BOOLEAN NOT NULL DEFAULT FALSE;")
    op.execute("CREATE INDEX IF NOT EXISTS ix_visual_sessions_user_id ON visual_sessions (user_id);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS visual_sessions;")
    op.execute("DROP TABLE IF EXISTS visual_questions;")
