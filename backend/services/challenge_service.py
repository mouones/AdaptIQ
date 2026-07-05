"""
services/challenge_service.py - Business logic for the Challenge Room.

Covers:
    - Rank-to-level access rules and streak-triggered level transitions
    - Point calculation and rank progression from cumulative session results
    - Challenge session lifecycle CRUD helpers
    - Ranking updates and duplicate-answer safety checks
"""

from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from config import (
    CHALLENGE_POINTS_LEVEL_1,
    CHALLENGE_POINTS_LEVEL_2,
    CHALLENGE_POINTS_LEVEL_3,
    CHALLENGE_POINTS_LEVEL_4,
    CHALLENGE_POINTS_LEVEL_5,
    CHALLENGE_RANK_A_MIN,
    CHALLENGE_RANK_B_MIN,
    CHALLENGE_RANK_C_MIN,
    CHALLENGE_RANK_D_MIN,
    CHALLENGE_STREAK_DOWN_THRESHOLD,
    CHALLENGE_STREAK_UP_THRESHOLD,
)
from database.challenge_models import ChallengeSession, ChallengeAnswer, ChallengeRanking

logger = logging.getLogger(__name__)


# -
# CONFIGURATION
# -

CHALLENGE_POINTS_TABLE: dict[int, tuple[int, int]] = {
    1: CHALLENGE_POINTS_LEVEL_1,
    2: CHALLENGE_POINTS_LEVEL_2,
    3: CHALLENGE_POINTS_LEVEL_3,
    4: CHALLENGE_POINTS_LEVEL_4,
    5: CHALLENGE_POINTS_LEVEL_5,
}

STREAK_UP_THRESHOLD = CHALLENGE_STREAK_UP_THRESHOLD
STREAK_DOWN_THRESHOLD = CHALLENGE_STREAK_DOWN_THRESHOLD

RANK_THRESHOLDS: list[tuple[int, str]] = [
    (0,     "E"),
    (CHALLENGE_RANK_D_MIN, "D"),
    (CHALLENGE_RANK_C_MIN, "C"),
    (CHALLENGE_RANK_B_MIN, "B"),
    (CHALLENGE_RANK_A_MIN, "A"),
]

# Which levels each rank can access. Rule: current rank level ±1, clamped to 1..5.
# E=1, D=2, C=3, B=4, A=5. There is never level 0 or level 6.
RANK_LEVEL_ACCESS: dict[str, list[int]] = {
    "E": [1, 2],
    "D": [1, 2, 3],
    "C": [2, 3, 4],
    "B": [3, 4, 5],
    "A": [4, 5],
}

ALL_RANKS = ["E", "D", "C", "B", "A"]


# -
# PURE LOGIC HELPERS
# -

# Return the list of levels unlocked for a given rank letter.
def get_available_levels(rank: str) -> list[int]:
    """Return level list available for this rank using rank ±1 clamped to 1..5."""
    return RANK_LEVEL_ACCESS.get(rank, [1, 2])


def clamp_challenge_level(level: int) -> int:
    """Keep Challenge level transitions inside the valid 1..5 range."""
    try:
        return max(1, min(5, int(level)))
    except Exception:
        return 1


# Validate that a requested level is available for the rank.
def is_level_allowed(rank: str, level: int) -> bool:
    """Check if a starting level is valid for this rank."""
    return level in get_available_levels(rank)


# Compute signed points for one answer at the given level.
def calculate_points(level: int, is_correct: bool) -> int:
    """Correct - positive points. Wrong - negative points."""
    level = clamp_challenge_level(level)
    if level not in CHALLENGE_POINTS_TABLE:
        logger.warning(
            "Challenge level missing from points table; using fallback level=%s",
            level,
        )
    correct_pts, wrong_pts = CHALLENGE_POINTS_TABLE.get(level, (3, -1))
    return correct_pts if is_correct else wrong_pts


# Detect whether streak thresholds require a forced level change.
def check_streak_trigger(
    streak_correct: int,
    streak_wrong: int,
) -> Optional[dict]:
    """
    Check if a streak threshold was hit.
    Returns direction + reason dict, or None.
    """
    if streak_correct >= STREAK_UP_THRESHOLD:
        return {
            "direction": "up",
            "reason": f"Outstanding! {streak_correct} correct in a row - advancing to next level.",
        }
    if streak_wrong >= STREAK_DOWN_THRESHOLD:
        return {
            "direction": "down",
            "reason": f"{streak_wrong} wrong answers - dropping to a more suitable level.",
        }
    return None


# Apply an up/down level change while clamping to rank limits.
def apply_level_change(current_level: int, direction: str, rank: str) -> int:
    """
    Apply a forced level change, clamped to the user's rank boundaries.

    A rank C player (levels 2-4) can never go below 2 or above 4,
    even with a long streak. This keeps users in their skill zone.
    """
    current_level = clamp_challenge_level(current_level)
    available = get_available_levels(rank)
    min_level = min(available)
    max_level = max(available)

    if direction == "up":
        return min(max_level, current_level + 1)
    elif direction == "down":
        return max(min_level, current_level - 1)
    return current_level


# Map total ranking points to the current rank letter.
def compute_rank_from_points(total_points: int) -> str:
    """Determine rank letter from cumulative rank_points."""
    rank = "E"
    for threshold, r in RANK_THRESHOLDS:
        if total_points >= threshold:
            rank = r
    return rank


# Update correct/wrong streak counters after an answer.
def update_streaks_after_answer(
    streak_correct: int,
    streak_wrong: int,
    is_correct: bool,
) -> tuple[int, int]:
    """
    Correct - increment correct streak, reset wrong streak.
    Wrong   - increment wrong streak, reset correct streak.
    Returns (new_streak_correct, new_streak_wrong).
    """
    if is_correct:
        return (streak_correct + 1, 0)
    else:
        return (0, streak_wrong + 1)


# -
# DB OPERATIONS
# -

# Load ranking row for a user, creating default rank state if absent.
async def get_or_create_ranking(
    db: AsyncSession,
    user_id: str,
) -> ChallengeRanking:
    uid = uuid.UUID(user_id)
    result = await db.execute(
        select(ChallengeRanking).where(ChallengeRanking.user_id == uid)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = ChallengeRanking(
            user_id      = uid,
            current_rank = "E",
            rank_points  = 0,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


# Create and persist a new challenge session row.
async def create_challenge_session(
    db: AsyncSession,
    user_id: str,
    topic: str,
    starting_level: int,
) -> ChallengeSession:
    row = ChallengeSession(
        id             = uuid.uuid4(),
        user_id        = uuid.UUID(user_id),
        topic          = topic,
        starting_level = starting_level,
        current_level  = starting_level,
        rank_points    = 0,
        streak_correct = 0,
        streak_wrong   = 0,
    )
    db.add(row)
    await db.flush()
    return row


# Fetch one challenge session by id.
async def get_challenge_session(
    db: AsyncSession,
    session_id: str,
) -> Optional[ChallengeSession]:
    try:
        sid = uuid.UUID(session_id)
    except (TypeError, ValueError):
        logger.warning("Invalid challenge session_id supplied: %s", session_id)
        return None

    result = await db.execute(
        select(ChallengeSession).where(
            ChallengeSession.id == sid
        )
    )
    return result.scalar_one_or_none()


# Persist one submitted challenge answer row.
async def record_challenge_answer(
    db: AsyncSession,
    session_id: str,
    question_id: str,
    chosen_answer: str,
    is_correct: bool,
    points_change: int,
    level_at_answer: int,
    time_taken: Optional[float],
) -> ChallengeAnswer:
    row = ChallengeAnswer(
        id              = uuid.uuid4(),
        session_id      = uuid.UUID(session_id),
        question_id     = uuid.UUID(question_id),
        chosen_answer   = chosen_answer,
        is_correct      = is_correct,
        points_change   = points_change,
        level_at_answer = level_at_answer,
        time_taken      = time_taken,
    )
    db.add(row)
    await db.flush()
    return row


# Apply per-answer updates to a session aggregate state.
async def update_session_after_answer(
    db: AsyncSession,
    session: ChallengeSession,
    is_correct: bool,
    points_change: int,
    new_streak_correct: int,
    new_streak_wrong: int,
    new_level: int,
) -> ChallengeSession:
    session.rank_points    += points_change
    session.streak_correct  = new_streak_correct
    session.streak_wrong    = new_streak_wrong
    session.current_level   = new_level
    session.total_questions += 1
    if is_correct:
        session.correct_answers += 1
    await db.flush()
    return session


# Mark a challenge session as completed with end timestamp.
async def finalize_session(
    db: AsyncSession,
    session: ChallengeSession,
) -> ChallengeSession:
    session.is_completed = True
    session.ended_at     = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    await db.refresh(session)
    return session


# Merge session outcomes into the user's long-term challenge ranking.
async def update_global_ranking(
    db: AsyncSession,
    user_id: str,
    session_points: int,
    session_questions: int,
    session_streak: int,
) -> ChallengeRanking:
    ranking = await get_or_create_ranking(db, user_id)
    old_rank = ranking.current_rank

    ranking.rank_points     += session_points
    ranking.total_sessions  += 1
    ranking.total_questions += session_questions
    ranking.highest_streak   = max(ranking.highest_streak, session_streak)
    ranking.updated_at       = datetime.now(timezone.utc).replace(tzinfo=None)
    ranking.current_rank     = compute_rank_from_points(ranking.rank_points)

    await db.commit()
    await db.refresh(ranking)

    if ranking.current_rank != old_rank:
        logger.info(
            f"User {user_id[:8]} promoted: {old_rank} - {ranking.current_rank} "
            f"({ranking.rank_points} pts)"
        )
    return ranking


# Check whether this session already contains an answer for the question.
async def has_answered_question(
    db: AsyncSession,
    session_id: str,
    question_id: str,
) -> bool:
    try:
        sid = uuid.UUID(session_id)
        qid = uuid.UUID(question_id)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid challenge IDs when checking duplicate answer: session_id=%s question_id=%s",
            session_id,
            question_id,
        )
        return False

    result = await db.execute(
        select(ChallengeAnswer).where(
            ChallengeAnswer.session_id  == sid,
            ChallengeAnswer.question_id == qid,
        )
    )
    return result.scalar_one_or_none() is not None

