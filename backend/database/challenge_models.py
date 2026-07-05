"""
database/challenge_models.py - SQLAlchemy ORM models for Challenge Room.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, Text, UniqueConstraint, Index, ForeignKey
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from database.models import Base


class ChallengeSession(Base):
    __tablename__ = "challenge_sessions"

    id              = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id         = Column(PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    topic           = Column(String(30), nullable=False)
    starting_level  = Column(Integer, nullable=False)
    current_level   = Column(Integer, nullable=False)
    rank_points     = Column(Integer, default=0)
    streak_correct  = Column(Integer, default=0)
    streak_wrong    = Column(Integer, default=0)
    total_questions = Column(Integer, default=0)
    correct_answers = Column(Integer, default=0)
    started_at      = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
    ended_at        = Column(DateTime, nullable=True)
    is_completed    = Column(Boolean, default=False)


class ChallengeAnswer(Base):
    __tablename__ = "challenge_answers"

    id              = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id      = Column(PG_UUID(as_uuid=True), ForeignKey("challenge_sessions.id", ondelete="CASCADE"), nullable=False)
    question_id     = Column(PG_UUID(as_uuid=True), ForeignKey("question_bank.id", ondelete="CASCADE"), nullable=False)
    chosen_answer   = Column(Text, nullable=False)
    is_correct      = Column(Boolean, nullable=False)
    points_change   = Column(Integer, nullable=False)
    level_at_answer = Column(Integer, nullable=False)
    time_taken      = Column(Float, nullable=True)
    created_at      = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)

    __table_args__ = (
        UniqueConstraint("session_id", "question_id", name="uq_challenge_answer_session_question"),
    )


class ChallengeRanking(Base):
    __tablename__ = "challenge_ranking"

    user_id         = Column(PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    current_rank    = Column(String(1), nullable=False, default="E")
    rank_points     = Column(Integer, nullable=False, default=0)
    total_sessions  = Column(Integer, default=0)
    total_questions = Column(Integer, default=0)
    highest_streak  = Column(Integer, default=0)
    updated_at      = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)

