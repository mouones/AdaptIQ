"""
database/visual_models.py
SQLAlchemy ORM model for the VisualRoom.

Shares the same Base as all other models — create_all() in lifespan picks it up.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, Text, Index, CHAR, ForeignKey
)
from sqlalchemy.dialects.postgresql import UUID

from database.models import Base   # shared metadata — NEVER create a new Base()


class VisualQuestion(Base):
    """
    One row per ingested COCO/VQA image.

    Lifecycle:
      1. Populated once by services/visual_ingestion.py (offline script).
      2. difficulty_actual is recomputed from n_correct/n_attempts after each submit.
      3. correct_answer + options_json are written at first-use (LLM generates them
         on the first GET /next call, then stored so subsequent calls don't re-generate).
    """
    __tablename__ = "visual_questions"

    # ── Identity ──────────────────────────────────────────────────────────────
        # ── Identity ──────────────────────────────────────────────────────────────
    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    coco_image_id   = Column(Integer, nullable=True, index=True)   # original COCO id for tracing
    image_url       = Column(Text, nullable=False)
    iso2            = Column(String(2), nullable=True, index=True)  # ISO 3166-1 alpha-2, geography only
    shape_svg       = Column(Text, nullable=True)                   # SVG silhouette, geography only)

    # ── Content ───────────────────────────────────────────────────────────────
    paragraph       = Column(Text, nullable=True)    # joined captions / VQA text
    topic           = Column(String(20), nullable=False, index=True)  # history|geography|mix

    # ── Difficulty ────────────────────────────────────────────────────────────
    difficulty_base   = Column(Float, nullable=False, default=3.0)
    difficulty_actual = Column(Float, nullable=False, default=3.0)

    # ── Level metadata ────────────────────────────────────────────────────────
    options_count   = Column(Integer, nullable=False, default=4)  # 2 for L1, 4 for L2-4, 0 for L5
    question_type   = Column(CHAR(1), nullable=False, default='M')  # 'M'=MCQ, 'T'=Text-input

    # ── LLM-generated content (written on first use, never regenerated) ───────
    question_text   = Column(Text, nullable=True)    # generated question
    correct_answer  = Column(Text, nullable=True)    # stored server-side only
    options_json    = Column(Text, nullable=True)    # JSON list of shuffled options
    explanation     = Column(Text, nullable=True)    # educational explanation

    # ── Adaptive stats ────────────────────────────────────────────────────────
    n_attempts      = Column(Integer, nullable=False, default=0)
    n_correct       = Column(Integer, nullable=False, default=0)

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at      = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at      = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
                             onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    __table_args__ = (
        # Primary query pattern: topic + difficulty range
        Index("ix_visual_topic_diff", "topic", "difficulty_actual"),
        # Fast lookup by COCO id for ingestion deduplication
        Index("ix_visual_coco_id", "coco_image_id"),
    )


class VisualSession(Base):
    """
    One row per user VisualRoom session.
    Tracks which questions were shown so we never repeat within a session.
    """
    __tablename__ = "visual_sessions"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id         = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    topic           = Column(String(20), nullable=False)
    level           = Column(Integer, nullable=False)
    current_index   = Column(Integer, nullable=False, default=0)
    total_questions = Column(Integer, nullable=False, default=10)
    score           = Column(Integer, nullable=False, default=0)
    streak_correct  = Column(Integer, nullable=False, default=0)
    streak_wrong    = Column(Integer, nullable=False, default=0)
    total_time_ms   = Column(Integer, nullable=False, default=0)
    # JSON list of question UUIDs already served in this session
    seen_ids_json   = Column(Text, nullable=False, default="[]")
    started_at      = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    ended_at        = Column(DateTime, nullable=True)
    is_completed    = Column(Boolean, nullable=False, default=False)
