"""
services/onboarding_service.py
Business logic for onboarding:
  - Get / create onboarding flags for a user
  - Submit or skip the survey
  - Mark tour as seen
  - Compute a lightweight difficulty prior from topic self-assessments

Includes helper accessors for bulk topic priors and suggested learning topics.
"""
from __future__ import annotations

import uuid
import logging
from typing import List, Optional

from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from database.onboarding_models import UserOnboardingFlags, UserOnboardingTopic

logger = logging.getLogger(__name__)

# ─── Difficulty prior constants ───────────────────────────────────────────────
# Applied ONCE at session start as a warm-start.
# IRT takes over after the first ~5 questions.
PRIOR_CONFIDENT     = +0.3   # bump difficulty up slightly
PRIOR_WANT_TO_LEARN = -0.3   # start slightly easier
PRIOR_NEUTRAL       =  0.0   # no data → no change


def _sanitize_topics(topics: List[str], limit: int = 3) -> list[str]:
    """Normalize, dedupe, and cap user-submitted topic lists."""
    sanitized: list[str] = []
    seen: set[str] = set()
    for raw in topics or []:
        topic = (raw or "").strip()
        if not topic:
            continue
        key = topic.lower()
        if key in seen:
            continue
        seen.add(key)
        sanitized.append(topic)
        if len(sanitized) >= limit:
            break
    return sanitized


# ─── Flag helpers ─────────────────────────────────────────────────────────────

# Load or create onboarding flag state for a user.
async def get_or_create_flags(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> UserOnboardingFlags:
    """
    Returns the onboarding flags row for this user.
    Creates it (with defaults) if it doesn't exist yet.
    This is called on every login — creation signals "first login".
    """
    result = await db.execute(
        select(UserOnboardingFlags).where(UserOnboardingFlags.user_id == user_id)
    )
    flags = result.scalar_one_or_none()
    if flags is None:
        flags = UserOnboardingFlags(user_id=user_id)
        db.add(flags)
        await db.flush()
    return flags


# ─── Status ───────────────────────────────────────────────────────────────────

# Return onboarding/tour status flags for frontend flow control.
async def get_onboarding_status(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> dict:
    flags = await get_or_create_flags(db, user_id)
    await db.commit()   # persist if just created

    return {
        "first_login":          flags.first_login,
        "onboarding_needed":    flags.first_login and not flags.onboarding_completed,
        "onboarding_completed": flags.onboarding_completed,
        "tour_needed":          flags.onboarding_completed and not flags.tour_seen,
    }


# ─── Survey ───────────────────────────────────────────────────────────────────

# Persist onboarding survey answers and mark onboarding complete.
async def submit_survey(
    db: AsyncSession,
    user_id: uuid.UUID,
    topics_confident:     List[str],
    topics_want_to_learn: List[str],
) -> bool:
    """
    Saves survey selections and marks onboarding as completed.
    Returns False if onboarding was already completed (idempotency guard).
    """
    flags = await get_or_create_flags(db, user_id)

    if flags.onboarding_completed:
        return False  # already done

    # Clear any previous partial submissions (shouldn't happen, but safe)
    await db.execute(
        delete(UserOnboardingTopic).where(UserOnboardingTopic.user_id == user_id)
    )

    confident_topics = _sanitize_topics(topics_confident, limit=3)
    learning_topics = _sanitize_topics(topics_want_to_learn, limit=3)

    # Insert confident topics
    for topic in confident_topics:
        db.add(UserOnboardingTopic(
            user_id  = user_id,
            topic    = topic,
            category = "confident",
        ))

    # Insert want-to-learn topics
    for topic in learning_topics:
        db.add(UserOnboardingTopic(
            user_id  = user_id,
            topic    = topic,
            category = "want_to_learn",
        ))

    flags.onboarding_completed = True
    flags.first_login          = False
    flags.tour_seen            = False   # tour will show next

    await db.commit()
    return True


# ─── Skip ─────────────────────────────────────────────────────────────────────

# Complete onboarding without survey answers.
async def skip_onboarding(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> None:
    """
    Marks onboarding as completed without saving any topic data.
    Tour will still show afterward.
    """
    flags = await get_or_create_flags(db, user_id)
    flags.onboarding_completed = True
    flags.first_login          = False
    flags.tour_seen            = False
    await db.commit()


# ─── Mark tour seen ───────────────────────────────────────────────────────────

# Mark the guided tour as completed for this user.
async def mark_tour_seen(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> None:
    flags = await get_or_create_flags(db, user_id)
    flags.tour_seen = True
    await db.commit()


# ─── Difficulty prior ─────────────────────────────────────────────────────────

# Compute startup difficulty prior from onboarding topic preferences.
async def get_difficulty_prior(
    db: AsyncSession,
    user_id: uuid.UUID,
    topic: str,
) -> float:
    """
    Returns a small difficulty adjustment based on onboarding survey data.

    Rules:
      - "confident" about this topic   → +0.3  (start slightly harder)
      - "want_to_learn" about this topic → -0.3  (start slightly easier)
      - No data for this topic          →  0.0  (no change)

    This is clamped by the caller to [1, 5].

    Usage in ClassicRoom / CustomRoom:
        prior = await get_difficulty_prior(db, user_id, topic)
        effective_difficulty = max(1, min(5, base_difficulty + prior))
    """
    # Only apply prior for users who haven't answered many questions yet.
    # Once IRT data accumulates, this function can be bypassed entirely.
    result = await db.execute(
        select(UserOnboardingTopic).where(
            UserOnboardingTopic.user_id == user_id,
            func.lower(UserOnboardingTopic.topic) == (topic or "").strip().lower(),
        )
    )
    row = result.scalar_one_or_none()

    if row is None:
        return PRIOR_NEUTRAL
    if row.category == "confident":
        return PRIOR_CONFIDENT
    if row.category == "want_to_learn":
        return PRIOR_WANT_TO_LEARN
    return PRIOR_NEUTRAL


# Return all topic priors for a user as a topic->prior map.
async def get_all_user_topic_priors(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> dict[str, float]:
    """
    Returns a dict of {topic: prior_value} for all topics in the user's survey.
    Useful for bulk warm-starting (e.g., CustomRoom topic suggestions).
    """
    result = await db.execute(
        select(UserOnboardingTopic).where(UserOnboardingTopic.user_id == user_id)
    )
    rows = result.scalars().all()

    priors = {}
    for row in rows:
        if row.category == "confident":
            priors[row.topic] = PRIOR_CONFIDENT
        elif row.category == "want_to_learn":
            priors[row.topic] = PRIOR_WANT_TO_LEARN
    return priors


# Return topics the user explicitly marked as want_to_learn.
async def get_want_to_learn_topics(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> list[str]:
    """
    Returns just the "want_to_learn" topics for a user.
    Used by CustomRoom to highlight suggested topics.
    """
    result = await db.execute(
        select(UserOnboardingTopic.topic).where(
            UserOnboardingTopic.user_id  == user_id,
            UserOnboardingTopic.category == "want_to_learn",
        )
    )
    return [row[0] for row in result.fetchall()]
