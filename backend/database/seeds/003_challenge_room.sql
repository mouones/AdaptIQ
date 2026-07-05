-- migrations/003_challenge_room.sql
-- Run this once against your PostgreSQL database.
-- These tables are completely additive — they do NOT modify existing tables.

-- ── challenge_sessions ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS challenge_sessions (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID        NOT NULL,
    topic           VARCHAR(30) NOT NULL,
    starting_level  INTEGER     NOT NULL CHECK (starting_level BETWEEN 1 AND 5),
    current_level   INTEGER     NOT NULL CHECK (current_level  BETWEEN 1 AND 5),
    rank_points     INTEGER     NOT NULL DEFAULT 0,
    streak_correct  INTEGER     NOT NULL DEFAULT 0,
    streak_wrong    INTEGER     NOT NULL DEFAULT 0,
    total_questions INTEGER     NOT NULL DEFAULT 0,
    correct_answers INTEGER     NOT NULL DEFAULT 0,
    started_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMP,
    is_completed    BOOLEAN     NOT NULL DEFAULT FALSE,
    CONSTRAINT fk_cs_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_challenge_sessions_user_id
    ON challenge_sessions (user_id);


-- ── challenge_answers ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS challenge_answers (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID        NOT NULL,
    question_id     UUID        NOT NULL,
    chosen_answer   TEXT        NOT NULL,
    is_correct      BOOLEAN     NOT NULL,
    points_change   INTEGER     NOT NULL,
    level_at_answer INTEGER     NOT NULL,
    time_taken      FLOAT,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_ca_session  FOREIGN KEY (session_id)  REFERENCES challenge_sessions(id) ON DELETE CASCADE,
    CONSTRAINT fk_ca_question FOREIGN KEY (question_id) REFERENCES question_bank(id)      ON DELETE RESTRICT,
    -- Anti-abuse: same question cannot be answered twice in one session
    CONSTRAINT uq_session_question UNIQUE (session_id, question_id)
);

CREATE INDEX IF NOT EXISTS ix_challenge_answers_session_id
    ON challenge_answers (session_id);


-- ── challenge_ranking ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS challenge_ranking (
    user_id         UUID        PRIMARY KEY,
    current_rank    VARCHAR(1)  NOT NULL DEFAULT 'E' CHECK (current_rank IN ('E','D','C','B','A')),
    rank_points     INTEGER     NOT NULL DEFAULT 0,
    total_sessions  INTEGER     NOT NULL DEFAULT 0,
    total_questions INTEGER     NOT NULL DEFAULT 0,
    highest_streak  INTEGER     NOT NULL DEFAULT 0,
    updated_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_cr_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
