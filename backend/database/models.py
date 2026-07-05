"""
database/models.py - SQLAlchemy ORM models.
Added: users table so login/signup works with the backend.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, Text, Index, ForeignKey
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class User(Base):
    """
    Registered users - created on signup, looked up on login.
    Stores hashed password (never plaintext).
    """
    __tablename__ = "users"

    id           = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email        = Column(String(255), unique=True, nullable=False, index=True)
    username     = Column(String(100), unique=True, nullable=False)
    password_hash= Column(String(255), nullable=False)
    points       = Column(Integer, default=0)
    level        = Column(String(30), default="Novice")
    elo_global   = Column(Float, default=0.0, nullable=False)
    created_at   = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
    last_login   = Column(DateTime, nullable=True)
    is_active    = Column(Boolean, default=True)
    is_admin     = Column(Boolean, default=False, nullable=False)
    ban_until    = Column(DateTime, nullable=True, index=True)
    ban_reason   = Column(Text, nullable=True)
    profile_picture = Column(String(255), nullable=True)


class UserResponse(Base):
    """One row per answer submitted. Drives IRT recalibration."""
    __tablename__ = "user_responses"

    id               = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id          = Column(PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id       = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    question_id      = Column(PG_UUID(as_uuid=True), ForeignKey("question_bank.id", ondelete="CASCADE"), nullable=False)
    topic            = Column(String(20), nullable=False)
    difficulty_sent  = Column(Integer, nullable=False)
    answered_correct = Column(Boolean, nullable=False)
    time_taken       = Column(Integer, nullable=False)
    used_hint        = Column(Boolean, default=False)
    created_at       = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)

    __table_args__ = (
        Index("ix_user_responses_user_topic", "user_id", "topic"),
        Index("ix_user_responses_user_created", "user_id", "created_at"),
    )


class QuestionBank(Base):
    """Cached questions with IRT calibration params."""
    __tablename__ = "question_bank"

    id             = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_text  = Column(Text, nullable=False)
    correct_answer = Column(Text, nullable=False)
    options_json   = Column(Text, nullable=False)
    explanation    = Column(Text, nullable=False)
    topic          = Column(String(20), nullable=False, index=True)
    sub_topic      = Column(String(50), nullable=True, index=True)
    difficulty_irt = Column(Float, default=2.5)
    # Shadow column written by the offline recalibration job (roadmap item 2).
    # difficulty_irt stays the served value until a calibrated value is reviewed
    # and promoted; this keeps recalibration off the request path and reversible.
    difficulty_irt_calibrated = Column(Float, nullable=True)
    calibrated_at  = Column(DateTime, nullable=True)
    calibration_sample = Column(Integer, nullable=True)
    discrimination = Column(Float, default=1.0)
    usage_count    = Column(Integer, default=0)
    times_seen     = Column(Integer, default=0)
    last_served_at = Column(DateTime, nullable=True)
    created_at     = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    source         = Column(String(30), default="llm")
    primary_concept_id = Column(PG_UUID(as_uuid=True), nullable=True)

    # Governance / trust signals (feature-flagged at runtime).
    gov_approved        = Column(Boolean, nullable=False, default=True)
    gov_safe            = Column(Boolean, nullable=False, default=True)
    gov_confidence      = Column(Float, nullable=True)
    gov_fact_trust      = Column(Float, nullable=True)
    gov_narrative_quality = Column(Float, nullable=True)
    gov_sources_json    = Column(Text, nullable=True)
    gov_flags_json      = Column(Text, nullable=True)
    gov_checked_at      = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_question_bank_topic_diff", "topic", "difficulty_irt"),
        Index("ix_question_bank_times_seen", "times_seen"),
        Index("ix_question_bank_last_served", "last_served_at"),
        Index("ix_question_bank_primary_concept", "primary_concept_id"),
        Index("ix_question_bank_gov_approved", "gov_approved"),
        Index("ix_question_bank_gov_safe", "gov_safe"),
    )

