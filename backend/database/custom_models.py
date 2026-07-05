"""
database/custom_models.py
Custom Room SQLAlchemy models.
Imports Base from database.models (shared metadata) - same pattern as challenge_models.py.

FIX: user_id must be UUID (not Integer) to match the users.id primary key type.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Float, Text, DateTime, ForeignKey,
    Boolean, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID
from database.models import Base  # shared metadata - one create_all


class Topic(Base):
    __tablename__ = "custom_topics"

    id                = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type              = Column(String(50),  nullable=False)
    slug              = Column(String(100), nullable=False, unique=True)
    name              = Column(String(200), nullable=False)
    description       = Column(Text,        nullable=True)
    total_facts_count = Column(Integer,     nullable=False, default=0)
    is_active         = Column(Boolean,     nullable=False, default=True, server_default="true")


class Fact(Base):
    __tablename__ = "custom_facts"

    id                        = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    topic                     = Column(String(200), nullable=False, index=True)
    content                   = Column(Text,        nullable=False)
    difficulty_hint           = Column(String(20),  nullable=True)
    total_questions_generated = Column(Integer,     nullable=False, default=0)
    source_question_id        = Column(UUID(as_uuid=True), ForeignKey("question_bank.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        UniqueConstraint("topic", "source_question_id", name="uq_custom_facts_topic_source"),
    )


class UserTopicMastery(Base):
    __tablename__ = "user_topic_mastery"

    id                    = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id               = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    topic                 = Column(String(200), nullable=False)
    mastered_facts_count  = Column(Integer,     nullable=False, default=0)
    total_facts_count     = Column(Integer,     nullable=False, default=0)
    last_session_at       = Column(DateTime,    nullable=True)
    completion_percentage = Column(Float,       nullable=False, default=0.0)

    __table_args__ = (
        UniqueConstraint("user_id", "topic", name="uq_user_topic_mastery"),
    )


class UserFactProgress(Base):
    __tablename__ = "user_fact_progress"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id      = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),        nullable=False, index=True)
    fact_id      = Column(UUID(as_uuid=True), ForeignKey("custom_facts.id", ondelete="CASCADE"), nullable=False)
    is_mastered  = Column(Boolean,            nullable=False, default=False)
    attempts     = Column(Integer,            nullable=False, default=0)
    correct_hits = Column(Integer,            nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("user_id", "fact_id", name="uq_user_fact_progress"),
    )


class CustomSession(Base):
    __tablename__ = "custom_sessions"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id         = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    topic           = Column(String(200), nullable=False)
    started_at      = Column(DateTime,    nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    ended_at        = Column(DateTime,    nullable=True)
    total_questions = Column(Integer,     nullable=False, default=0)
    correct_count   = Column(Integer,     nullable=False, default=0)

