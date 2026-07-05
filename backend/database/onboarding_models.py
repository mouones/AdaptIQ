"""
database/onboarding_models.py
Onboarding models - user flags + topic selections.
Imports Base from database.models (shared metadata) - same pattern as custom/challenge models.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID
from database.models import Base  # shared metadata - one create_all


class UserOnboardingFlags(Base):
    """
    Stores the three onboarding flags per user.
    One row per user - created on first login.
    """
    __tablename__ = "user_onboarding_flags"

    id                    = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id               = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True)
    first_login           = Column(Boolean, nullable=False, default=True)
    onboarding_completed  = Column(Boolean, nullable=False, default=False)
    tour_seen             = Column(Boolean, nullable=False, default=False)
    created_at            = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at            = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class UserOnboardingTopic(Base):
    """
    Stores topic self-assessments from the onboarding survey.
    category is one of: "confident", "want_to_learn"
    topic strings match exactly the format used in ClassicRoom / CustomRoom.
    """
    __tablename__ = "user_onboarding_topics"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    topic      = Column(String(200), nullable=False)   # e.g. "History - World War II"
    category   = Column(String(20),  nullable=False)   # "confident" | "want_to_learn"
    created_at = Column(DateTime,    nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    __table_args__ = (
        UniqueConstraint("user_id", "topic", "category", name="uq_user_onboarding_topic"),
    )


