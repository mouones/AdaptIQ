"""Database models for queue-driven generation telemetry and future PvP queue audits."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from database.models import Base


class QuestionGenerationEvent(Base):
    """Durable record of one background generation attempt."""

    __tablename__ = "question_generation_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    room = Column(String(32), nullable=False, index=True)
    queue_key = Column(String(255), nullable=False, index=True)
    topic = Column(String(200), nullable=False, index=True)
    concept_id = Column(UUID(as_uuid=True), ForeignKey("concepts.id", ondelete="SET NULL"), nullable=True, index=True)
    fact_id = Column(UUID(as_uuid=True), ForeignKey("custom_facts.id", ondelete="SET NULL"), nullable=True, index=True)
    provider = Column(String(64), nullable=True)
    provider_status = Column(Integer, nullable=True)
    generation_ms = Column(Float, nullable=False, default=0.0)
    accepted = Column(Boolean, nullable=False, default=False)
    rejection_reason = Column(Text, nullable=True)
    created_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        index=True,
    )

    __table_args__ = (
        Index("ix_question_generation_room_created", "room", "created_at"),
    )


class PvPQueueEvent(Base):
    """Reserved durable audit trail for a future Redis-backed matchmaking flow."""

    __tablename__ = "pvp_queue_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    topic = Column(String(64), nullable=False, index=True)
    event_type = Column(String(32), nullable=False, index=True)
    opponent_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    match_id = Column(UUID(as_uuid=True), ForeignKey("pvp_matches.id", ondelete="SET NULL"), nullable=True, index=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        index=True,
    )

    __table_args__ = (
        Index("ix_pvp_queue_events_topic_created", "topic", "created_at"),
    )
