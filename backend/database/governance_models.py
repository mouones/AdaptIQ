"""database/governance_models.py - Governance blocklist + audit tables."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    String,
    Boolean,
    DateTime,
    Text,
    Float,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from database.models import Base


class GovernanceBlockRule(Base):
    __tablename__ = "governance_block_rules"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind = Column(String(20), nullable=False)  # "topic" | "keyword"
    pattern = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at = Column(DateTime, nullable=True)
    created_by = Column(PG_UUID(as_uuid=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("kind", "pattern", name="uq_governance_block_rules_kind_pattern"),
        Index("ix_governance_block_rules_active", "is_active"),
    )


class QuestionAudit(Base):
    __tablename__ = "question_audits"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_id = Column(PG_UUID(as_uuid=True), nullable=True)
    room = Column(String(30), nullable=True)
    topic = Column(String(80), nullable=True)
    action = Column(String(30), nullable=False)  # "persist" | "serve" | "override"
    approved = Column(Boolean, nullable=False)
    reasons_json = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    sources_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    question_text = Column(Text, nullable=True)
    correct_answer = Column(Text, nullable=True)
    options_json = Column(Text, nullable=True)
    explanation = Column(Text, nullable=True)
    user_id = Column(PG_UUID(as_uuid=True), nullable=True)

    __table_args__ = (
        Index("ix_question_audits_question_id", "question_id"),
        Index("ix_question_audits_created_at", "created_at"),
        Index("ix_question_audits_action_approved", "action", "approved"),
        Index("ix_question_audits_user_id", "user_id"),
    )


