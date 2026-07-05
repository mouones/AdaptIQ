"""
database/pvp_models.py - SQLAlchemy ORM models for PvP matchmaking and matches.

Tables:
  - pvp_matchmaking_queue: Players waiting for an opponent
  - pvp_matches: Active/completed 1v1 matches
  - pvp_match_answers: Per-question answer records for each player in a match
  - pvp_ratings: Elo-based rating and match history stats per user
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from database.models import Base


class PvPMatchmakingQueue(Base):
    """Players waiting in the matchmaking queue.

    When two players with similar Elo and shared concepts are found,
    a PvPMatch is created and both rows are deleted from the queue.
    """
    __tablename__ = "pvp_matchmaking_queue"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    topic      = Column(String(50), nullable=False, default="Mixed")
    elo_rating = Column(Float, nullable=False, default=1000.0)
    concepts_json = Column(Text, nullable=True)  # JSON array of concept IDs
    joined_at  = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    status     = Column(String(20), default="waiting")  # waiting, matched, expired


class PvPMatch(Base):
    """A 1v1 quiz match between two players.

    Both players receive the same set of questions (stored as JSON).
    The match ends when both players finish or the timer expires.
    """
    __tablename__ = "pvp_matches"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user1_id    = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    user2_id    = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    topic       = Column(String(50), nullable=False)
    status      = Column(String(20), default="active")  # active, completed, cancelled
    total_questions = Column(Integer, default=5)
    questions_json  = Column(Text, nullable=True)  # JSON array of question data

    user1_score = Column(Integer, default=0)
    user2_score = Column(Integer, default=0)
    user1_finished = Column(Boolean, default=False)
    user2_finished = Column(Boolean, default=False)

    winner_id   = Column(UUID(as_uuid=True), nullable=True)
    elo_change  = Column(Float, default=0.0)  # Signed Elo delta for user1 (stored for replay/idempotency)

    started_at  = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    ended_at    = Column(DateTime, nullable=True)
    created_at  = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class PvPMatchAnswer(Base):
    """Per-question answer record for a player in a PvP match.

    Both players answer the same question independently - their answers
    are recorded separately and compared after both finish.
    """
    __tablename__ = "pvp_match_answers"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    match_id    = Column(UUID(as_uuid=True), ForeignKey("pvp_matches.id"), nullable=False, index=True)
    user_id     = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    question_id = Column(UUID(as_uuid=True), nullable=False)
    question_index = Column(Integer, nullable=False)  # 0-based index in the shared quiz

    chosen_answer = Column(String(500), nullable=False)
    is_correct    = Column(Boolean, nullable=False)
    time_taken    = Column(Float, nullable=True)  # seconds

    answered_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    __table_args__ = (
        UniqueConstraint("match_id", "user_id", "question_index", name="uq_pvp_answer_match_user_index"),
    )


class PvPRating(Base):
    """Elo rating and match history stats for a user.

    Elo starts at 1000. Win = +K*(1-expected), Loss = -K*expected.
    K-factor is 32 for first 30 games, then 16.
    """
    __tablename__ = "pvp_ratings"

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id        = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True, index=True)
    elo_rating     = Column(Float, default=1000.0)
    total_matches  = Column(Integer, default=0)
    total_wins     = Column(Integer, default=0)
    total_losses   = Column(Integer, default=0)
    total_draws    = Column(Integer, default=0)
    win_streak     = Column(Integer, default=0)
    best_streak    = Column(Integer, default=0)
    updated_at     = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


