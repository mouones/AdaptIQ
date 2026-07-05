"""
services/pvp_service.py - PvP matchmaking, live scoreboard, answers, forfeit, and Elo.

Main design:
  - PostgreSQL stores durable records: queue, matches, answers, ratings.
  - Redis stores the live scoreboard for fast polling during a match.
  - After each answer, Redis is updated first, then the PostgreSQL match row is
    synchronized from Redis. Redis does not update PostgreSQL by itself; the
    backend performs the sync.

Important rule:
  - submit_answer() must NOT set the match to completed.
  - end_match() and forfeit_match() are the only functions that finalize Elo.
"""

from __future__ import annotations

import json
import uuid
import random
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Any
from math import log10 as math_log10, pow as math_pow

from sqlalchemy import select, delete, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from config import PVP_CANDIDATE_POOL_SIZE, PVP_QUESTIONS_PER_MATCH
from database.pvp_models import (
    PvPMatchmakingQueue,
    PvPMatch,
    PvPMatchAnswer,
    PvPRating,
)
from database.models import User, QuestionBank
from database.concept_models import UserConceptTheta

logger = logging.getLogger(__name__)

# Elo constants
ELO_K_NEW = 32
ELO_K_REGULAR = 16
ELO_DEFAULT = 1000.0
ELO_MAX_DIFF = 300

# Redis/live-state constants
PVP_SCOREBOARD_TTL_SECONDS = int(os.getenv("PVP_SCOREBOARD_TTL_SECONDS", "3600"))
PVP_REMATCH_COOLDOWN_SECONDS = int(os.getenv("PVP_REMATCH_COOLDOWN_SECONDS", "60"))


# ═══════════════════════════════════════════════════════════════════════════
# Small helpers
# ═══════════════════════════════════════════════════════════════════════════


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _same_uuid(a: Any, b: Any) -> bool:
    return str(a) == str(b)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_load_json_list(raw: Optional[str]) -> list:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _safe_load_concepts(raw: Optional[str]) -> set[str]:
    parsed = _safe_load_json_list(raw)
    return {str(item) for item in parsed if str(item).strip()}


def _scoreboard_key(match_id: uuid.UUID | str) -> str:
    return f"pvp:match:{match_id}:scoreboard"


def _bool_to_redis(value: bool) -> str:
    return "1" if value else "0"


def _redis_to_bool(value: Any) -> bool:
    return str(value) == "1"


def _match_questions(match: PvPMatch) -> list[dict]:
    return _safe_load_json_list(match.questions_json)


def _visible_question(raw_question: dict, fallback_index: int) -> dict:
    options = raw_question.get("options") if isinstance(raw_question.get("options"), list) else []
    return {
        "id": str(raw_question.get("id", "")),
        "text": str(raw_question.get("text", "")),
        "options": [str(opt) for opt in options],
        "index": int(raw_question.get("index", fallback_index)),
    }


def _result_for_user(match: PvPMatch, user_id: uuid.UUID) -> str:
    if match.winner_id is None:
        return "draw"
    return "win" if _same_uuid(match.winner_id, user_id) else "loss"


def _scores_for_user(match: PvPMatch, user_id: uuid.UUID) -> tuple[int, int]:
    if _same_uuid(user_id, match.user1_id):
        return int(match.user1_score or 0), int(match.user2_score or 0)
    return int(match.user2_score or 0), int(match.user1_score or 0)


# ═══════════════════════════════════════════════════════════════════════════
# Redis scoreboard helpers
# ═══════════════════════════════════════════════════════════════════════════


async def _write_scoreboard(redis_client, match: PvPMatch) -> None:
    """Write the current match row into Redis as live scoreboard state."""
    if redis_client is None:
        return

    key = _scoreboard_key(match.id)
    await redis_client.hset(
        key,
        mapping={
            "match_id": str(match.id),
            "status": str(match.status or "active"),
            "user1_id": str(match.user1_id),
            "user2_id": str(match.user2_id),
            "user1_score": int(match.user1_score or 0),
            "user2_score": int(match.user2_score or 0),
            "user1_finished": _bool_to_redis(bool(match.user1_finished)),
            "user2_finished": _bool_to_redis(bool(match.user2_finished)),
            "winner_id": str(match.winner_id) if match.winner_id else "",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    await redis_client.expire(key, PVP_SCOREBOARD_TTL_SECONDS)


async def _read_scoreboard(redis_client, match: PvPMatch) -> dict:
    """Read live scoreboard from Redis, falling back to PostgreSQL match row."""
    fallback = {
        "match_id": str(match.id),
        "status": str(match.status or "active"),
        "user1_score": int(match.user1_score or 0),
        "user2_score": int(match.user2_score or 0),
        "user1_finished": bool(match.user1_finished),
        "user2_finished": bool(match.user2_finished),
        "winner_id": str(match.winner_id) if match.winner_id else "",
    }

    if redis_client is None:
        return fallback

    key = _scoreboard_key(match.id)
    raw = await redis_client.hgetall(key)
    if not raw:
        await _write_scoreboard(redis_client, match)
        raw = await redis_client.hgetall(key)

    if not raw:
        return fallback

    return {
        "match_id": str(match.id),
        "status": str(raw.get("status", fallback["status"])),
        "user1_score": _safe_int(raw.get("user1_score"), fallback["user1_score"]),
        "user2_score": _safe_int(raw.get("user2_score"), fallback["user2_score"]),
        "user1_finished": _redis_to_bool(raw.get("user1_finished", _bool_to_redis(fallback["user1_finished"]))),
        "user2_finished": _redis_to_bool(raw.get("user2_finished", _bool_to_redis(fallback["user2_finished"]))),
        "winner_id": str(raw.get("winner_id", fallback["winner_id"]) or ""),
    }


async def _sync_scoreboard_to_postgres(
    db: AsyncSession,
    redis_client,
    match: PvPMatch,
) -> None:
    """Copy live Redis scoreboard values into the PostgreSQL pvp_matches row."""
    state = await _read_scoreboard(redis_client, match)

    match.user1_score = int(state["user1_score"])
    match.user2_score = int(state["user2_score"])
    match.user1_finished = bool(state["user1_finished"])
    match.user2_finished = bool(state["user2_finished"])
    match.status = str(state.get("status") or match.status or "active")

    winner_raw = state.get("winner_id") or ""
    if winner_raw:
        try:
            match.winner_id = uuid.UUID(str(winner_raw))
        except ValueError:
            pass
    elif match.status != "completed":
        match.winner_id = None

    await db.flush()


async def get_match_state(
    db: AsyncSession,
    match_id: uuid.UUID,
    user_id: uuid.UUID,
    redis_client=None,
) -> dict:
    """Return live match state for a participant."""
    match = await get_match(db, match_id)
    if not match:
        raise ValueError("Match not found")
    if user_id not in (match.user1_id, match.user2_id):
        raise ValueError("You are not in this match")

    state = await _read_scoreboard(redis_client, match)
    return {
        "match_id": str(match.id),
        "status": state["status"],
        "user1_score": state["user1_score"],
        "user2_score": state["user2_score"],
        "user1_finished": state["user1_finished"],
        "user2_finished": state["user2_finished"],
        "winner_id": state.get("winner_id") or None,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Matchmaking score helpers
# ═══════════════════════════════════════════════════════════════════════════


def _elo_closeness_score(elo_a: float, elo_b: float) -> float:
    diff = abs(float(elo_a) - float(elo_b))
    if diff > ELO_MAX_DIFF:
        return 0.0
    return max(0.0, 1.0 - (diff / float(ELO_MAX_DIFF)))


def _recency_score(joined_at: Optional[datetime], now: datetime) -> float:
    if not joined_at:
        return 0.3
    return 1.0 if (now - joined_at).total_seconds() <= 600 else 0.3


def _theta_affinity(theta_a: float, theta_b: float) -> float:
    return 1.0 / (1.0 + abs(theta_a - theta_b))


async def _load_user_matchmaking_concepts(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    mastered_rows = await db.execute(
        select(UserConceptTheta.concept_id)
        .where(
            UserConceptTheta.user_id == user_id,
            UserConceptTheta.mastery_level.in_(["INTERMEDIATE", "ADVANCED", "EXPERT"]),
            UserConceptTheta.response_count >= 5,
        )
        .order_by(UserConceptTheta.theta.desc())
        .limit(15)
    )
    mastered = [str(row[0]) for row in mastered_rows.fetchall() if row[0] is not None]
    if mastered:
        return mastered

    fallback_rows = await db.execute(
        select(UserConceptTheta.concept_id)
        .where(UserConceptTheta.user_id == user_id)
        .order_by(UserConceptTheta.response_count.desc())
        .limit(10)
    )
    return [str(row[0]) for row in fallback_rows.fetchall() if row[0] is not None]


async def _load_theta_map(db: AsyncSession, user_id: uuid.UUID, concept_ids: set[str]) -> dict[str, float]:
    concept_uuids: list[uuid.UUID] = []
    for raw_id in concept_ids:
        try:
            concept_uuids.append(uuid.UUID(str(raw_id)))
        except ValueError:
            continue

    if not concept_uuids:
        return {}

    result = await db.execute(
        select(UserConceptTheta.concept_id, UserConceptTheta.theta).where(
            UserConceptTheta.user_id == user_id,
            UserConceptTheta.concept_id.in_(concept_uuids),
        )
    )
    return {str(concept_id): float(theta or 0.0) for concept_id, theta in result.fetchall()}


async def _calculate_concept_affinity(
    db: AsyncSession,
    user1_id: uuid.UUID,
    user2_id: uuid.UUID,
    shared_concept_ids: set[str],
) -> float:
    if not shared_concept_ids:
        return 0.0

    theta_1 = await _load_theta_map(db, user1_id, shared_concept_ids)
    theta_2 = await _load_theta_map(db, user2_id, shared_concept_ids)

    total = 0.0
    counted = 0
    for concept_id in shared_concept_ids:
        if concept_id not in theta_1 or concept_id not in theta_2:
            continue
        total += _theta_affinity(theta_1[concept_id], theta_2[concept_id])
        counted += 1

    if counted == 0:
        return 0.0

    affinity_average = total / float(counted)
    overlap_coverage = min(1.0, len(shared_concept_ids) / 10.0)
    return (affinity_average * 0.8) + (overlap_coverage * 0.2)


async def _score_candidate(db: AsyncSession, entry: PvPMatchmakingQueue, candidate: PvPMatchmakingQueue) -> float:
    elo_score = _elo_closeness_score(float(entry.elo_rating), float(candidate.elo_rating))
    if elo_score <= 0.0:
        return 0.0

    shared_concepts = _safe_load_concepts(entry.concepts_json) & _safe_load_concepts(candidate.concepts_json)
    affinity_score = await _calculate_concept_affinity(db, entry.user_id, candidate.user_id, shared_concepts)
    recency = _recency_score(getattr(candidate, "joined_at", None), _now())

    # If both users have no concept history yet, do not block matchmaking.
    # Elo + recency should still produce a valid demo match.
    final_score = (affinity_score * 0.50) + (elo_score * 0.40) + (recency * 0.10)
    return final_score


# ═══════════════════════════════════════════════════════════════════════════
# Ratings
# ═══════════════════════════════════════════════════════════════════════════


async def get_or_create_rating(db: AsyncSession, user_id: uuid.UUID) -> PvPRating:
    result = await db.execute(select(PvPRating).where(PvPRating.user_id == user_id))
    rating = result.scalar_one_or_none()
    if rating is None:
        rating = PvPRating(user_id=user_id, elo_rating=ELO_DEFAULT)
        db.add(rating)
        await db.flush()
        logger.info("Created PvP rating for user=%s", str(user_id)[:8])
    return rating


# ═══════════════════════════════════════════════════════════════════════════
# Queue / matchmaking
# ═══════════════════════════════════════════════════════════════════════════


async def join_queue(
    db: AsyncSession,
    user_id: uuid.UUID,
    topic: str,
    redis_client=None,
) -> PvPMatchmakingQueue:
    """Add a player to queue and immediately attempt matchmaking."""
    await db.execute(delete(PvPMatchmakingQueue).where(PvPMatchmakingQueue.user_id == user_id))

    rating = await get_or_create_rating(db, user_id)
    concept_ids = await _load_user_matchmaking_concepts(db, user_id)

    entry = PvPMatchmakingQueue(
        user_id=user_id,
        topic=topic or "Mixed",
        elo_rating=float(rating.elo_rating or ELO_DEFAULT),
        concepts_json=json.dumps(concept_ids),
        status="waiting",
    )
    db.add(entry)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ValueError("Already in matchmaking queue")

    await db.refresh(entry)
    logger.info("User %s joined PvP queue topic=%s elo=%.0f", str(user_id)[:8], entry.topic, entry.elo_rating)

    match = await _try_matchmaking(db, entry, redis_client=redis_client)
    if match:
        logger.info("Immediate PvP match found: %s", str(match.id)[:8])

    return entry


async def leave_queue(db: AsyncSession, user_id: uuid.UUID) -> bool:
    result = await db.execute(
        delete(PvPMatchmakingQueue).where(
            PvPMatchmakingQueue.user_id == user_id,
            PvPMatchmakingQueue.status == "waiting",
        )
    )
    await db.commit()
    return bool(result.rowcount and result.rowcount > 0)


async def get_queue_status(
    db: AsyncSession,
    user_id: uuid.UUID,
    redis_client=None,
) -> dict:
    active_matches_result = await db.execute(
        select(PvPMatch)
        .where(
            and_(
                PvPMatch.status == "active",
                (PvPMatch.user1_id == user_id) | (PvPMatch.user2_id == user_id),
            )
        )
        .order_by(PvPMatch.created_at.desc(), PvPMatch.started_at.desc())
        .limit(2)
    )
    active_matches = active_matches_result.scalars().all()

    if active_matches:
        match_row = active_matches[0]
        await _write_scoreboard(redis_client, match_row)
        opponent_id = match_row.user2_id if match_row.user1_id == user_id else match_row.user1_id
        opponent = await db.get(User, opponent_id)
        return {
            "status": "matched",
            "match_id": str(match_row.id),
            "opponent_username": opponent.username if opponent else "Unknown",
            "topic": match_row.topic,
            "message": f"Match found! Playing against {opponent.username if opponent else 'Unknown'}",
        }

    result = await db.execute(
        select(PvPMatchmakingQueue)
        .where(PvPMatchmakingQueue.user_id == user_id)
        .order_by(PvPMatchmakingQueue.joined_at.desc())
        .limit(1)
    )
    entry = result.scalars().first()

    if entry and entry.status == "matched":
        return {
            "status": "waiting",
            "match_id": None,
            "opponent_username": None,
            "topic": entry.topic,
            "message": "Match was prepared but is not active yet; waiting for synchronization...",
        }

    if entry and entry.status == "waiting":
        match = await _try_matchmaking(db, entry, redis_client=redis_client)
        if match:
            opponent_id = match.user2_id if match.user1_id == user_id else match.user1_id
            opponent = await db.get(User, opponent_id)
            return {
                "status": "matched",
                "match_id": str(match.id),
                "opponent_username": opponent.username if opponent else "Unknown",
                "topic": match.topic,
                "message": f"Match found! Playing against {opponent.username if opponent else 'Unknown'}",
            }
        return {
            "status": "waiting",
            "match_id": None,
            "opponent_username": None,
            "topic": entry.topic,
            "message": "Still searching for an opponent...",
        }

    return {"status": "not_in_queue", "match_id": None, "opponent_username": None, "topic": None, "message": "Not in queue"}


async def _try_matchmaking(
    db: AsyncSession,
    entry: PvPMatchmakingQueue,
    redis_client=None,
) -> Optional[PvPMatch]:
    stale_cutoff = _now() - timedelta(minutes=10)
    await db.execute(
        delete(PvPMatchmakingQueue).where(
            PvPMatchmakingQueue.status == "waiting",
            PvPMatchmakingQueue.joined_at < stale_cutoff,
        )
    )

    query = (
        select(PvPMatchmakingQueue)
        .where(
            PvPMatchmakingQueue.user_id != entry.user_id,
            PvPMatchmakingQueue.status == "waiting",
            PvPMatchmakingQueue.elo_rating >= float(entry.elo_rating) - ELO_MAX_DIFF,
            PvPMatchmakingQueue.elo_rating <= float(entry.elo_rating) + ELO_MAX_DIFF,
        )
        .order_by(func.random())
        .limit(10)
    )

    if entry.topic != "Mixed":
        query = query.where((PvPMatchmakingQueue.topic == entry.topic) | (PvPMatchmakingQueue.topic == "Mixed"))

    result = await db.execute(query)
    candidates = result.scalars().all()
    if not candidates:
        logger.info("PvP no candidates for user=%s", str(entry.user_id)[:8])
        return None

    recent_opponent_ids: set[uuid.UUID] = set()
    if PVP_REMATCH_COOLDOWN_SECONDS > 0:
        cooldown_cutoff = _now() - timedelta(seconds=PVP_REMATCH_COOLDOWN_SECONDS)
        recent_opponents_result = await db.execute(
            select(PvPMatch.user1_id, PvPMatch.user2_id).where(
                PvPMatch.status == "completed",
                PvPMatch.ended_at >= cooldown_cutoff,
                (PvPMatch.user1_id == entry.user_id) | (PvPMatch.user2_id == entry.user_id),
            )
        )
        for u1, u2 in recent_opponents_result.fetchall():
            recent_opponent_ids.add(u2 if u1 == entry.user_id else u1)

    best: Optional[PvPMatchmakingQueue] = None
    best_score = 0.0
    for candidate in candidates:
        if candidate.user_id in recent_opponent_ids:
            logger.info(
                "PvP skip candidate due rematch cooldown user=%s opponent=%s",
                str(entry.user_id)[:8],
                str(candidate.user_id)[:8],
            )
            continue
        score = await _score_candidate(db, entry, candidate)
        logger.info(
            "PvP candidate score user=%s candidate=%s score=%.2f",
            str(entry.user_id)[:8],
            str(candidate.user_id)[:8],
            score,
        )
        if score > best_score:
            best = candidate
            best_score = score

    if best is None or best_score < 0.1:
        logger.info("PvP no valid match user=%s best_score=%.2f", str(entry.user_id)[:8], best_score)
        return None

    topic = entry.topic if entry.topic != "Mixed" else best.topic
    match = await _create_match(db, entry.user_id, best.user_id, topic, redis_client=redis_client)

    entry.status = "matched"
    best.status = "matched"
    await db.commit()
    await db.refresh(match)
    await _write_scoreboard(redis_client, match)

    return match


async def _create_match(
    db: AsyncSession,
    user1_id: uuid.UUID,
    user2_id: uuid.UUID,
    topic: str,
    redis_client=None,
) -> PvPMatch:
    """Create an active match with shared questions. Does not finalize Elo."""
    target_question_count = max(1, int(PVP_QUESTIONS_PER_MATCH))
    candidate_pool_size = max(target_question_count, int(PVP_CANDIDATE_POOL_SIZE))

    governance_enabled = False
    try:
        from services.governance_service import GovernanceService
        governance_enabled = GovernanceService.enabled()
    except Exception:
        governance_enabled = False

    union_seen_ids: set[uuid.UUID] = set()
    try:
        from services.classic_service import ClassicService
        seen1 = await ClassicService.get_user_seen_question_ids(db, user1_id, "mix")
        seen2 = await ClassicService.get_user_seen_question_ids(db, user2_id, "mix")
        union_seen_ids = set(seen1) | set(seen2)
    except Exception:
        union_seen_ids = set()

    stmt = select(QuestionBank)
    if topic and topic != "Mixed":
        stmt = stmt.where(QuestionBank.topic.ilike(f"%{topic.lower()}%"))
    if union_seen_ids:
        stmt = stmt.where(QuestionBank.id.notin_(list(union_seen_ids)))
    if governance_enabled:
        stmt = stmt.where(QuestionBank.gov_approved == True)  # noqa: E712
        stmt = stmt.where(QuestionBank.gov_safe == True)  # noqa: E712
    stmt = stmt.order_by(func.random()).limit(candidate_pool_size)

    result = await db.execute(stmt)
    candidates = result.scalars().all()

    questions: list[QuestionBank] = []
    if governance_enabled:
        try:
            from services.governance_service import GovernanceService
            for candidate in candidates:
                decision = await GovernanceService.evaluate_bank_row_for_serving(db, row=candidate, room="pvp", topic=topic)
                if decision.approved:
                    questions.append(candidate)
                if len(questions) >= target_question_count:
                    break
        except Exception:
            questions = candidates[:target_question_count]
    else:
        questions = candidates[:target_question_count]

    if len(questions) < target_question_count:
        used_ids = [q.id for q in questions]
        extra_stmt = select(QuestionBank)
        if used_ids:
            extra_stmt = extra_stmt.where(QuestionBank.id.notin_(used_ids))
        if union_seen_ids:
            extra_stmt = extra_stmt.where(QuestionBank.id.notin_(list(union_seen_ids)))
        if governance_enabled:
            extra_stmt = extra_stmt.where(QuestionBank.gov_approved == True)  # noqa: E712
            extra_stmt = extra_stmt.where(QuestionBank.gov_safe == True)  # noqa: E712
        extra_stmt = extra_stmt.order_by(func.random()).limit(target_question_count - len(questions))
        extra_result = await db.execute(extra_stmt)
        questions.extend(extra_result.scalars().all())

    questions_data: list[dict] = []
    for i, q in enumerate(questions[:target_question_count]):
        try:
            options = json.loads(q.options_json or "[]")
        except Exception:
            options = []
        if not isinstance(options, list):
            options = []
        random.shuffle(options)
        questions_data.append(
            {
                "id": str(q.id),
                "text": q.question_text,
                "options": [str(opt) for opt in options],
                "correctAnswer": str(q.correct_answer),
                "explanation": q.explanation or "",
                "index": i,
            }
        )

    if not questions_data:
        raise ValueError("No PvP questions available")

    match = PvPMatch(
        user1_id=user1_id,
        user2_id=user2_id,
        topic=topic or "Mixed",
        status="active",
        total_questions=len(questions_data),
        questions_json=json.dumps(questions_data),
        user1_score=0,
        user2_score=0,
        user1_finished=False,
        user2_finished=False,
    )
    db.add(match)
    await db.flush()
    await _write_scoreboard(redis_client, match)

    logger.info(
        "PvP match created: %s (%s vs %s, %d questions)",
        str(match.id)[:8],
        str(user1_id)[:8],
        str(user2_id)[:8],
        len(questions_data),
    )
    return match


# ═══════════════════════════════════════════════════════════════════════════
# Match gameplay
# ═══════════════════════════════════════════════════════════════════════════


async def get_match(db: AsyncSession, match_id: uuid.UUID) -> Optional[PvPMatch]:
    return await db.get(PvPMatch, match_id)


async def _count_player_answers(db: AsyncSession, match_id: uuid.UUID, user_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(PvPMatchAnswer).where(
            PvPMatchAnswer.match_id == match_id,
            PvPMatchAnswer.user_id == user_id,
        )
    )
    
    if hasattr(result, "scalar_one"):
        return int(result.scalar_one() or 0)
    if hasattr(result, "scalar_one_or_none"):
        return int(result.scalar_one_or_none() or 0)
    return 0


async def submit_answer(
    db: AsyncSession,
    match_id: uuid.UUID,
    user_id: uuid.UUID,
    question_id: str,
    question_index: int,
    answer: str,
    time_taken: Optional[float],
    redis_client=None,
) -> dict:
    """Validate, record, update Redis scoreboard, sync PostgreSQL, return next question."""
    match = await get_match(db, match_id)
    if not match:
        raise ValueError("Match not found")
    if match.status != "active":
        raise ValueError("Match is not active")
    if user_id not in (match.user1_id, match.user2_id):
        raise ValueError("User is not in this match")

    questions = _match_questions(match)
    total_questions = int(getattr(match, 'total_questions', None) or len(questions))
    if total_questions <= 0 or not questions:
        raise ValueError("Match has no questions")

    answered_before = await _count_player_answers(db, match_id, user_id)
    if answered_before >= total_questions:
        raise ValueError("All questions in this match are already answered")
    if int(question_index) != answered_before:
        raise ValueError(f"Question must be answered in order. Expected question index {answered_before}")
    if question_index < 0 or question_index >= len(questions):
        raise ValueError("Invalid question index")

    existing = await db.execute(
        select(PvPMatchAnswer).where(
            PvPMatchAnswer.match_id == match_id,
            PvPMatchAnswer.user_id == user_id,
            PvPMatchAnswer.question_index == question_index,
        )
    )
    if existing.scalar_one_or_none():
        raise ValueError("Already answered this question")

    q_data = questions[question_index]
    expected_question_id = str(q_data.get("id", "")).strip()
    try:
        normalized_question_id = str(uuid.UUID(question_id))
        normalized_expected_id = str(uuid.UUID(expected_question_id))
    except ValueError:
        raise ValueError("Invalid question ID")
    if normalized_question_id != normalized_expected_id:
        raise ValueError("Question payload mismatch")

    correct_answer = str(q_data.get("correctAnswer", ""))
    submitted_answer = (answer or "").strip() or "__timeout__"
    is_correct = submitted_answer.lower() == correct_answer.strip().lower()

    stored_time_taken = None
    if time_taken is not None:
        try:
            stored_time_taken = max(0.0, min(20.0, float(time_taken)))
        except (TypeError, ValueError):
            stored_time_taken = None

    db.add(
        PvPMatchAnswer(
            match_id=match_id,
            user_id=user_id,
            question_id=uuid.UUID(normalized_question_id),
            question_index=question_index,
            chosen_answer=submitted_answer,
            is_correct=is_correct,
            time_taken=stored_time_taken,
        )
    )
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ValueError("Already answered this question")

    player_is_user1 = _same_uuid(user_id, match.user1_id)

    # Redis is the live scoreboard. If Redis is unavailable, update the match
    # row directly as a safe fallback.
    if redis_client is not None:
        await _write_scoreboard(redis_client, match)
        key = _scoreboard_key(match.id)
        if is_correct:
            await redis_client.hincrby(key, "user1_score" if player_is_user1 else "user2_score", 1)
        await redis_client.expire(key, PVP_SCOREBOARD_TTL_SECONDS)
    elif is_correct:
        if player_is_user1:
            match.user1_score = int(match.user1_score or 0) + 1
        else:
            match.user2_score = int(match.user2_score or 0) + 1

    answers_count = await _count_player_answers(db, match_id, user_id)
    if answers_count >= total_questions:
        if redis_client is not None:
            key = _scoreboard_key(match.id)
            await redis_client.hset(key, "user1_finished" if player_is_user1 else "user2_finished", "1")
            await redis_client.expire(key, PVP_SCOREBOARD_TTL_SECONDS)
        else:
            if player_is_user1:
                match.user1_finished = True
            else:
                match.user2_finished = True

    # Copy Redis live scoreboard into PostgreSQL after every answer.
    await _sync_scoreboard_to_postgres(db, redis_client, match)
    match_finished = bool(match.user1_finished and match.user2_finished)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ValueError("Already answered this question")

    your_score, opponent_score = _scores_for_user(match, user_id)

    next_question = None
    # This prevents fake question 6/5. Next question is returned only when it
    # actually exists and the player still has unanswered questions.
    if answers_count < total_questions and answers_count < len(questions):
        next_question = _visible_question(questions[answers_count], answers_count)

    return {
        "is_correct": is_correct,
        "correct_answer": correct_answer,
        "explanation": str(q_data.get("explanation", "") or ""),
        "your_score": your_score,
        "opponent_score": opponent_score,
        "questions_answered": answers_count,
        "match_finished": match_finished,
        "next_question": next_question,
    }


# ═══════════════════════════════════════════════════════════════════════════
# End / forfeit / Elo
# ═══════════════════════════════════════════════════════════════════════════


def _compute_elo_change(
    elo1: float,
    elo2: float,
    winner_id: Optional[uuid.UUID],
    user1_id: uuid.UUID,
    user2_id: uuid.UUID,
    total_matches: int,
) -> float:
    k = ELO_K_NEW if int(total_matches or 0) < 30 else ELO_K_REGULAR
    expected = 1.0 / (1.0 + math_pow(10, (float(elo2) - float(elo1)) / 400.0))

    if winner_id is None:
        actual = 0.5
    elif _same_uuid(winner_id, user1_id):
        actual = 1.0
    else:
        actual = 0.0

    return round(k * (actual - expected), 1)


def _normalize_user1_delta_from_match(match, rating_user1=None, rating_user2=None) -> float:
    """Return signed delta from user1 perspective, including legacy absolute storage."""
    raw = float(getattr(match, "elo_change", 0.0) or 0.0)
    winner_id = getattr(match, "winner_id", None)
    user1_id = getattr(match, "user1_id", None)
    user2_id = getattr(match, "user2_id", None)
    if winner_id is None:
        return raw
    if _same_uuid(winner_id, user1_id):
        return abs(raw)
    if _same_uuid(winner_id, user2_id):
        return -abs(raw)
    return raw


def _infer_user2_delta_from_post_state(match, rating_user1, rating_user2, user1_delta: float) -> float:
    """Infer user2's signed Elo delta for old rows that only store user1 delta."""
    user1_matches_after = int(getattr(rating_user1, "total_matches", 0) or 0)
    user2_matches_after = int(getattr(rating_user2, "total_matches", 0) or 0)
    user1_k = ELO_K_NEW if max(0, user1_matches_after - 1) < 30 else ELO_K_REGULAR
    user2_k = ELO_K_NEW if max(0, user2_matches_after - 1) < 30 else ELO_K_REGULAR
    if not user1_k:
        return round(-float(user1_delta or 0.0), 1)
    return round(-float(user1_delta or 0.0) * (user2_k / user1_k), 1)


async def _sync_user_elo_global(db: AsyncSession, user_id: uuid.UUID, elo_rating: float) -> None:
    """Mirror the authoritative PvPRating.elo_rating onto the denormalized
    User.elo_global column (roadmap item 7).

    User.elo_global was previously never updated by match play, so any reader of
    that column saw a stale value. This keeps it consistent with the rating. It is
    additive and does not affect the Elo math or PvPRating (the source of truth).
    """
    user = await db.get(User, user_id)
    if user is not None:
        user.elo_global = float(elo_rating if elo_rating is not None else ELO_DEFAULT)


def _update_rating_after_match(rating: PvPRating, winner_id: Optional[uuid.UUID], user_id: uuid.UUID, elo_delta: float) -> None:
    rating.elo_rating = float(rating.elo_rating or ELO_DEFAULT) + float(elo_delta)
    rating.total_matches = int(rating.total_matches or 0) + 1
    rating.updated_at = _now()

    if winner_id is None:
        rating.total_draws = int(rating.total_draws or 0) + 1
        rating.win_streak = 0
    elif _same_uuid(winner_id, user_id):
        rating.total_wins = int(rating.total_wins or 0) + 1
        rating.win_streak = int(rating.win_streak or 0) + 1
        rating.best_streak = max(int(rating.best_streak or 0), int(rating.win_streak or 0))
    else:
        rating.total_losses = int(rating.total_losses or 0) + 1
        rating.win_streak = 0


async def end_match(
    db: AsyncSession,
    match_id: uuid.UUID,
    user_id: uuid.UUID,
    redis_client=None,
    forced_winner_id: Optional[uuid.UUID] = None,
) -> dict:
    """Finalize match exactly once and update Elo."""
    match = await get_match(db, match_id)
    if not match:
        raise ValueError("Match not found")
    if user_id not in (match.user1_id, match.user2_id):
        raise ValueError("You are not in this match")

    # Always sync the latest Redis scoreboard into the durable row.
    await _sync_scoreboard_to_postgres(db, redis_client, match)

    is_user1 = _same_uuid(user_id, match.user1_id)
    opponent_id = match.user2_id if is_user1 else match.user1_id
    opponent = await db.get(User, opponent_id)

    # Idempotent replay: do not apply Elo twice.
    if match.status != "active":
        rating_row = await get_or_create_rating(db, user_id)
        your_score, opponent_score = _scores_for_user(match, user_id)
        user1_delta = float(match.elo_change or 0.0)
        signed_delta = user1_delta if is_user1 else round(-user1_delta, 1)
        return {
            "match_id": str(match.id),
            "winner_id": str(match.winner_id) if match.winner_id else None,
            "result": _result_for_user(match, user_id),
            "your_score": your_score,
            "opponent_score": opponent_score,
            "elo_change": signed_delta,
            "new_elo": float(rating_row.elo_rating or ELO_DEFAULT),
            "opponent_username": opponent.username if opponent else "Unknown",
        }

    if forced_winner_id is not None:
        match.winner_id = forced_winner_id
        match.user1_finished = True
        match.user2_finished = True
    else:
        if not (match.user1_finished and match.user2_finished):
            raise ValueError("Match is still waiting for opponent")
        if int(match.user1_score or 0) > int(match.user2_score or 0):
            match.winner_id = match.user1_id
        elif int(match.user2_score or 0) > int(match.user1_score or 0):
            match.winner_id = match.user2_id
        else:
            match.winner_id = None

    rating1 = await get_or_create_rating(db, match.user1_id)
    rating2 = await get_or_create_rating(db, match.user2_id)

    elo_change_user1 = _compute_elo_change(
        float(rating1.elo_rating or ELO_DEFAULT),
        float(rating2.elo_rating or ELO_DEFAULT),
        match.winner_id,
        match.user1_id,
        match.user2_id,
        int(rating1.total_matches or 0),
    )
    elo_change_user2 = _compute_elo_change(
        float(rating2.elo_rating or ELO_DEFAULT),
        float(rating1.elo_rating or ELO_DEFAULT),
        match.winner_id,
        match.user2_id,
        match.user1_id,
        int(rating2.total_matches or 0),
    )

    match.status = "completed"
    match.ended_at = _now()
    match.elo_change = elo_change_user1

    _update_rating_after_match(rating1, match.winner_id, match.user1_id, elo_change_user1)
    _update_rating_after_match(rating2, match.winner_id, match.user2_id, elo_change_user2)

    # Keep the denormalized User.elo_global in sync with the authoritative rating.
    await _sync_user_elo_global(db, match.user1_id, rating1.elo_rating)
    await _sync_user_elo_global(db, match.user2_id, rating2.elo_rating)

    await db.execute(delete(PvPMatchmakingQueue).where(PvPMatchmakingQueue.user_id.in_([match.user1_id, match.user2_id])))
    await db.flush()
    await _write_scoreboard(redis_client, match)
    await db.commit()

    logger.info(
        "PvP match finalized match=%s winner=%s score=%s-%s delta1=%.1f delta2=%.1f",
        str(match.id)[:8],
        str(match.winner_id)[:8] if match.winner_id else "draw",
        match.user1_score,
        match.user2_score,
        elo_change_user1,
        elo_change_user2,
    )

    your_score, opponent_score = _scores_for_user(match, user_id)
    your_rating = rating1 if is_user1 else rating2
    your_delta = elo_change_user1 if is_user1 else elo_change_user2

    return {
        "match_id": str(match.id),
        "winner_id": str(match.winner_id) if match.winner_id else None,
        "result": _result_for_user(match, user_id),
        "your_score": your_score,
        "opponent_score": opponent_score,
        "elo_change": your_delta,
        "new_elo": float(your_rating.elo_rating or ELO_DEFAULT),
        "opponent_username": opponent.username if opponent else "Unknown",
    }


async def forfeit_match(
    db: AsyncSession,
    match_id: uuid.UUID,
    leaver_id: uuid.UUID,
    redis_client=None,
) -> dict:
    """User leaves an active match; opponent wins immediately."""
    match = await get_match(db, match_id)
    if not match:
        raise ValueError("Match not found")
    if leaver_id not in (match.user1_id, match.user2_id):
        raise ValueError("You are not in this match")

    if match.status != "active":
        return await end_match(db, match_id, leaver_id, redis_client=redis_client)

    winner_id = match.user2_id if _same_uuid(leaver_id, match.user1_id) else match.user1_id

    # Set live Redis state first, then finalization will sync it to PostgreSQL.
    if redis_client is not None:
        await _write_scoreboard(redis_client, match)
        key = _scoreboard_key(match.id)
        await redis_client.hset(
            key,
            mapping={
                "user1_finished": "1",
                "user2_finished": "1",
                "status": "active",
                "winner_id": str(winner_id),
            },
        )
        await redis_client.expire(key, PVP_SCOREBOARD_TTL_SECONDS)
    else:
        match.user1_finished = True
        match.user2_finished = True
        match.winner_id = winner_id

    logger.info(
        "PvP forfeit match=%s leaver=%s winner=%s",
        str(match.id)[:8],
        str(leaver_id)[:8],
        str(winner_id)[:8],
    )

    return await end_match(
        db,
        match_id,
        leaver_id,
        redis_client=redis_client,
        forced_winner_id=winner_id,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Rating / leaderboard
# ═══════════════════════════════════════════════════════════════════════════


async def get_user_rating(db: AsyncSession, user_id: uuid.UUID) -> dict:
    user_exists = await db.scalar(select(User.id).where(User.id == user_id))
    if user_exists is None:
        raise ValueError("User not found")

    rating = await get_or_create_rating(db, user_id)
    total = int(rating.total_matches or 0)
    return {
        "user_id": str(user_id),
        "elo_rating": float(rating.elo_rating or ELO_DEFAULT),
        "total_matches": total,
        "total_wins": int(rating.total_wins or 0),
        "total_losses": int(rating.total_losses or 0),
        "total_draws": int(rating.total_draws or 0),
        "win_streak": int(rating.win_streak or 0),
        "best_streak": int(rating.best_streak or 0),
        "win_rate": round((int(rating.total_wins or 0) / total) * 100, 1) if total > 0 else 0.0,
    }


async def get_leaderboard(db: AsyncSession, limit: int = 20) -> dict:
    capped = max(1, min(50, int(limit or 20)))
    total_players = await db.scalar(select(func.count()).select_from(PvPRating)) or 0

    rows = await db.execute(
        select(PvPRating, User.username)
        .join(User, PvPRating.user_id == User.id)
        .order_by(PvPRating.elo_rating.desc())
        .limit(capped)
    )

    entries = []
    for i, (rating, username) in enumerate(rows.all(), 1):
        total = int(rating.total_matches or 0)
        entries.append(
            {
                "rank": i,
                "user_id": str(rating.user_id),
                "username": username,
                "elo_rating": float(rating.elo_rating or ELO_DEFAULT),
                "total_wins": int(rating.total_wins or 0),
                "total_matches": total,
                "win_rate": round((int(rating.total_wins or 0) / total) * 100, 1) if total > 0 else 0.0,
            }
        )

    return {"entries": entries, "total_players": int(total_players)}
