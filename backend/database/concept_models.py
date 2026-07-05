"""
database/concept_models.py
Concept-aware persistence models for per-concept adaptation.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, JSON
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from database.models import Base


class Concept(Base):
    __tablename__ = "concepts"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False, index=True)
    topic = Column(String(50), nullable=False, index=True)
    scope = Column(String(200), nullable=False, default="general", index=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)

    __table_args__ = (
        UniqueConstraint("topic", "scope", "name", name="uq_concepts_topic_scope_name"),
        Index("ix_concepts_topic_scope", "topic", "scope"),
    )


class QuestionConcept(Base):
    __tablename__ = "question_concepts"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_id = Column(PG_UUID(as_uuid=True), ForeignKey("question_bank.id", ondelete="CASCADE"), nullable=False, index=True)
    concept_id = Column(PG_UUID(as_uuid=True), ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False, index=True)
    is_primary = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)

    __table_args__ = (
        UniqueConstraint("question_id", "concept_id", name="uq_question_concept"),
        Index("ix_question_concepts_primary", "question_id", "is_primary"),
    )


class UserConceptTheta(Base):
    __tablename__ = "user_concept_theta"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    concept_id = Column(PG_UUID(as_uuid=True), ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False, index=True)
    theta = Column(Float, nullable=False, default=0.0)
    theta_variance = Column(Float, nullable=False, default=1.0)
    response_count = Column(Integer, nullable=False, default=0)
    exposure_count = Column(Integer, nullable=False, default=0)
    mastery_level = Column(String(20), nullable=False, default="BEGINNER")
    first_seen_at = Column(DateTime, nullable=True)
    last_played_at = Column(DateTime, nullable=True)
    last_updated = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "concept_id", name="uq_user_concept_theta"),
    )


class UserConceptRepeatQueue(Base):
    __tablename__ = "user_concept_repeat_queue"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    concept_id = Column(PG_UUID(as_uuid=True), ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False, index=True)
    question_id = Column(PG_UUID(as_uuid=True), ForeignKey("question_bank.id", ondelete="CASCADE"), nullable=False, index=True)
    repeat_probability = Column(Float, nullable=False, default=0.5)
    due_after_session = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "concept_id", "question_id", name="uq_repeat_queue_entry"),
    )


class ClassicSession(Base):
    """Session tracking for Classic Room."""

    __tablename__ = "classic_sessions"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    topic = Column(String(50), nullable=False)
    questions_answered = Column(Integer, default=0)
    correct_count = Column(Integer, default=0)
    concepts = Column(JSON, nullable=True)  # List of selected concepts for this session
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
    ended_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_classic_sessions_created_at", "created_at"),
    )
