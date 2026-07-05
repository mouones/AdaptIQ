"""
routers/challenge.py — FastAPI router for the Challenge Room.

Covers:
    - GET  /api/challenge/user/{user_id}/rank
    - POST /api/challenge/start-session
    - GET  /api/challenge/session/{session_id}
    - POST /api/challenge/change-level
    - POST /api/challenge/generate-question
    - POST /api/challenge/submit-answer
    - POST /api/challenge/end-session

Internal helper groups:
    - Access/session guards and issued-question tracking
    - Level-aware LLM generation and prompt controls
    - Dependency loaders for app-level services (LLM, RAG, HTTP)
    - Server-side answer verification and ranking/session orchestration
"""



import json
import random
import logging
import uuid
from typing import Optional, Annotated
import asyncio

from fastapi import APIRouter, HTTPException, Depends, Request, Body
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from config import (
    CHALLENGE_SESSION_QUESTION_TTL_SECONDS,
    CHALLENGE_PREGEN_BATCH_SIZE,
    CHALLENGE_PREGEN_LEVEL_RADIUS,
    CHALLENGE_PREGEN_TARGET_PER_LEVEL,
    CHALLENGE_PREGEN_TOPUP_THRESHOLD,
    ENABLE_NO_INLINE_LLM,
)
from schemas.challenge import (
    UserRankOut,
    StartSessionRequest,
    StartSessionOut,
    ChallengeSessionOut,
    ChangeLevelRequest,
    ChangeLevelOut,
    GenerateChallengeQuestionRequest,
    ChallengeQuestionOut,
    SubmitChallengeAnswerRequest,
    SubmitChallengeAnswerOut,
    ForceLevelChange,
    EndSessionOut,
)

from database.challenge_models import ChallengeSession, ChallengeAnswer, ChallengeRanking
from database.models import QuestionBank

from services.challenge_service import (
    get_available_levels,
    is_level_allowed,
    calculate_points,
    check_streak_trigger,
    apply_level_change,
    update_streaks_after_answer,
    get_or_create_ranking,
    create_challenge_session,
    get_challenge_session,
    record_challenge_answer,
    update_session_after_answer,
    finalize_session,
    update_global_ranking,
    has_answered_question,
    CHALLENGE_POINTS_TABLE,
)
from services.classic_service import ClassicService
from services.question_sources import NON_CHALLENGE_SOURCE_PREFIXES, NON_CHALLENGE_SOURCE_VALUES
from services.rate_limits import enforce_user_quota
from services.question_queue import (
    challenge_ready_queue_key,
    mark_provider_backoff,
    pop_ready_question_id,
    provider_backoff_active,
    RefillRequest,
    request_refill,
)
from dependencies import limiter
from routers.auth import get_current_user, get_db

logger = logging.getLogger(__name__)
challenge_router = APIRouter(prefix="/api/challenge", tags=["Challenge Room"])


def _challenge_bank_source_filter():
    """Exclude room-specific generated rows from Challenge fallback pools."""
    source_expr = func.lower(func.coalesce(QuestionBank.source, ""))
    filters = [source_expr.notin_(list(NON_CHALLENGE_SOURCE_VALUES))]
    filters.extend(~source_expr.like(f"{prefix}_%") for prefix in NON_CHALLENGE_SOURCE_PREFIXES)
    return filters


def _challenge_pregen_levels(starting_level: int) -> list[int]:
    """Pre-generate the current level and nearby levels for faster transitions."""
    try:
        level = max(1, min(5, int(starting_level)))
    except Exception:
        level = 1
    radius = max(0, int(CHALLENGE_PREGEN_LEVEL_RADIUS))
    priority = [0]
    for step in range(1, radius + 1):
        priority.extend([-step, step])
    result: list[int] = []
    for offset in priority:
        item = max(1, min(5, level + offset))
        if item not in result:
            result.append(item)
    return result or [level]


async def _request_challenge_pregen(redis_client, *, topic: str, level: int, session_id: str | None = None, force: bool = False) -> None:
    """Ask the background worker to keep the configured ready-question pool for a level."""
    if redis_client is None:
        return
    try:
        level = max(1, min(5, int(level)))
        queue_key = challenge_ready_queue_key(topic, level)
        target_depth = max(1, int(CHALLENGE_PREGEN_TARGET_PER_LEVEL))
        topup_threshold = max(1, min(target_depth, int(CHALLENGE_PREGEN_TOPUP_THRESHOLD)))
        try:
            current_depth = int(await redis_client.llen(queue_key))
        except Exception:
            current_depth = 0
        # Session start can force a refill, but never enqueue useless work if the
        # queue is already full. Normal top-up waits until the queue drops below
        # the threshold, then fills it back toward target_depth.
        if force and current_depth >= target_depth:
            return
        if not force and current_depth >= topup_threshold:
            return
        await request_refill(
            redis_client,
            RefillRequest(
                room="challenge",
                queue_key=queue_key,
                topic=topic,
                difficulty_bucket=level,
                batch_size=max(1, int(CHALLENGE_PREGEN_BATCH_SIZE)),
                min_depth=target_depth,
                metadata={
                    "session_id": str(session_id or ""),
                    "prewarm": True,
                    "target_per_level": target_depth,
                    "topup_threshold": topup_threshold,
                },
            ),
            force=force,
        )
    except Exception as exc:
        logger.debug("challenge pregen request skipped: %s", exc)


# Ensure users can only operate on their own challenge resources.
def _ensure_user_match(target_user_id: str, current_user_id: str) -> None:
    try:
        target_uuid = uuid.UUID(str(target_user_id))
    except ValueError:
        raise HTTPException(422, "user_id must be a valid UUID")
    if str(target_uuid) != current_user_id:
        raise HTTPException(403, "You are not allowed to access this user data")


# Build the cache key used for issued-question tracking per session.
def _challenge_session_question_key(session_id: str) -> str:
    return f"challenge_session_questions:{session_id}"


# Remember a generated question as issued to this session (redis/in-memory).
async def _remember_issued_question(request: Request, session_id: str, question_id: str) -> bool:
    key = _challenge_session_question_key(session_id)
    qid = str(question_id)
    redis_client = getattr(request.app.state, "redis", None)

    if redis_client is not None:
        try:
            await redis_client.sadd(key, qid)
            await redis_client.expire(key, CHALLENGE_SESSION_QUESTION_TTL_SECONDS)
            return True
        except Exception as exc:
            logger.warning("challenge issued-question redis write failed: %s", exc)

    fallback = getattr(request.app.state, "challenge_session_questions", None)
    if fallback is None:
        fallback = {}
        request.app.state.challenge_session_questions = fallback

    values = set(fallback.get(key, []))
    values.add(qid)
    fallback[key] = list(values)
    try:
        logger.debug("Remembered issued challenge question: session=%s question=%s", session_id[:8], qid[:8])
    except Exception:
        pass
    return True


# Verify whether a question was already issued in this session.
async def _session_has_issued_question(request: Request, session_id: str, question_id: str) -> bool:
    key = _challenge_session_question_key(session_id)
    qid = str(question_id)
    redis_client = getattr(request.app.state, "redis", None)

    if redis_client is not None:
        try:
            return bool(await redis_client.sismember(key, qid))
        except Exception as exc:
            logger.warning("challenge issued-question redis read failed: %s", exc)

    fallback = getattr(request.app.state, "challenge_session_questions", None)
    if fallback is None:
        return False
    return qid in set(fallback.get(key, []))


def _challenge_pending_question_key(session_id: str) -> str:
    return f"challenge_pending_question:{session_id}"


async def _set_pending_challenge_question(request: Request, session_id: str, question_id: str) -> None:
    redis_client = getattr(request.app.state, "redis", None)
    key = _challenge_pending_question_key(session_id)
    if redis_client is not None:
        try:
            await redis_client.setex(key, CHALLENGE_SESSION_QUESTION_TTL_SECONDS, str(question_id))
            return
        except Exception as exc:
            logger.warning("challenge pending-question redis write failed: %s", exc)
    fallback = getattr(request.app.state, "challenge_pending_questions", None)
    if fallback is None:
        fallback = {}
        request.app.state.challenge_pending_questions = fallback
    fallback[key] = str(question_id)


async def _clear_pending_challenge_question(request: Request, session_id: str, question_id: str | None = None) -> None:
    redis_client = getattr(request.app.state, "redis", None)
    key = _challenge_pending_question_key(session_id)
    if redis_client is not None:
        try:
            if question_id is None:
                await redis_client.delete(key)
            else:
                current = await redis_client.get(key)
                if current is not None and str(current) == str(question_id):
                    await redis_client.delete(key)
            return
        except Exception as exc:
            logger.warning("challenge pending-question redis clear failed: %s", exc)
    fallback = getattr(request.app.state, "challenge_pending_questions", None)
    if fallback is not None:
        if question_id is None or str(fallback.get(key)) == str(question_id):
            fallback.pop(key, None)


async def _get_pending_challenge_question(request: Request, session_id: str) -> Optional[str]:
    redis_client = getattr(request.app.state, "redis", None)
    key = _challenge_pending_question_key(session_id)
    if redis_client is not None:
        try:
            value = await redis_client.get(key)
            return str(value) if value else None
        except Exception as exc:
            logger.warning("challenge pending-question redis read failed: %s", exc)
    fallback = getattr(request.app.state, "challenge_pending_questions", None)
    if fallback is None:
        return None
    value = fallback.get(key)
    return str(value) if value else None


def _challenge_topic_focus(topic: str) -> str:
    label = (topic or "Mixed").strip()
    if label.lower() == "mixed":
        return "History or Geography only: civilizations, empires, capitals, rivers, mountains, borders, exploration, maps, historical causes and consequences"
    if label.lower() == "history":
        return "History only: events, civilizations, empires, revolutions, leaders, treaties, causes and consequences"
    if label.lower() == "geography":
        return "Geography only: capitals, countries, regions, landforms, rivers, climate zones, borders and map reasoning"
    return label


def _is_low_quality_challenge_text(text: str) -> bool:
    lowered = (text or "").lower()
    blocked = [
        "gdp", "gross domestic", "income", "population", "census", "percentage", "%",
        "how many", "how much", "average annual", "per capita", "2024 census",
        "current", "latest", "as of 202", "estimated", "approximately what population",
    ]
    return any(term in lowered for term in blocked)


def _question_out_from_row(row: QuestionBank, effective_level: int) -> dict:
    return _normalize_challenge_options_for_level({
        "id": str(row.id),
        "text": row.question_text,
        "options": json.loads(row.options_json or "[]"),
        "correctAnswer": row.correct_answer,
        "explanation": row.explanation or "",
        "is_free_text": effective_level == 5,
    }, effective_level)


async def _row_is_unanswered_in_session(db: AsyncSession, session_id: str, question_id: str) -> bool:
    try:
        result = await db.execute(
            select(ChallengeAnswer.id).where(
                ChallengeAnswer.session_id == uuid.UUID(str(session_id)),
                ChallengeAnswer.question_id == uuid.UUID(str(question_id)),
            ).limit(1)
        )
        return result.scalar_one_or_none() is None
    except Exception:
        return False


async def _get_user_seen_challenge_signatures(
    db: AsyncSession,
    user_id: uuid.UUID,
    topic: str,
) -> set[str]:
    # Use topic='mix' to gather seen questions across all topics (global dedupe)
    seen_ids = await ClassicService.get_user_seen_question_ids(
        db=db,
        user_id=user_id,
        topic="mix",
    )
    if not seen_ids:
        return set()

    result = await db.execute(
        select(QuestionBank.question_text).where(QuestionBank.id.in_(list(seen_ids)))
    )
    try:
        logger.debug("Challenge seen signatures fetched: user=%s count=%d", str(user_id)[:8], len(seen_ids))
    except Exception:
        pass
    return {
        _challenge_signature(str(text))
        for text in result.scalars().all()
        if str(text).strip()
    }


# ─────────────────────────────────────────────────────────────────────────
# LEVEL PROMPT CONFIG
# Describes exactly what the LLM must produce for each level.
# ─────────────────────────────────────────────────────────────────────────

LEVEL_PROMPTS = {
    1: {
        "description": "VERY EASY — famous history/geography fact. Level 1 must be simple recall, no statistics or obscure numbers.",
        "options_rule": "Return ONLY 2 options: 'correct' and 'wrong1'. Leave 'wrong2' and 'wrong3' as empty strings.",
        "options_count": 2,
        "is_free_text": False,
    },
    2: {
        "description": "EASY — clear history/geography fact with 4 options. Avoid GDP, population, income, census, and numeric-stat trivia.",
        "options_rule": "Return 4 options. 'wrong1' and 'wrong2' must be obviously wrong. 'wrong3' should be slightly plausible.",
        "options_count": 4,
        "is_free_text": False,
    },
    3: {
        "description": "MEDIUM — requires connecting two historical/geographic facts; use real context, not random statistics.",
        "options_rule": "Return 4 options. 2 of the wrong answers must be plausible (same category, same region, similar era). 1 wrong answer can be obvious.",
        "options_count": 4,
        "is_free_text": False,
    },
    4: {
        "description": "HARD — multi-hop history/geography reasoning with plausible same-category distractors.",
        "options_rule": "Return 4 options. ALL 4 options must be plausible and from the same category. The user should genuinely be unsure.",
        "options_count": 4,
        "is_free_text": False,
    },
    5: {
        "description": "VERY HARD — typed-answer expert history/geography knowledge. Ask for a specific name/place/event/treaty, not a number.",
        "options_rule": "This is a FREE TEXT question. Do NOT generate options. Set 'wrong1', 'wrong2', 'wrong3' all to empty strings. The user will type their answer.",
        "options_count": 0,
        "is_free_text": True,
    },
}


def _normalize_challenge_options_for_level(question: dict, level: int) -> dict:
    """Force persisted/returned challenge payloads to match the active level rules."""
    q = dict(question)
    correct = str(q.get("correctAnswer") or q.get("correct") or "").strip()
    raw_options = [str(opt).strip() for opt in (q.get("options") or []) if str(opt).strip()]

    # Keep correct answer available for verification even if an old DB row lacked it in options.
    if correct and all(opt.lower() != correct.lower() for opt in raw_options):
        raw_options.insert(0, correct)

    unique: list[str] = []
    seen: set[str] = set()
    for opt in raw_options:
        key = opt.lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(opt)

    if level == 5:
        q["options"] = []
        q["is_free_text"] = True
        return q

    wrongs = [opt for opt in unique if correct and opt.lower() != correct.lower()]
    if level == 1:
        fallback_wrongs = ["Not this answer", "Cannot be determined", "None of the above"]
        while len(wrongs) < 1:
            wrongs.append(fallback_wrongs.pop(0))
        options = [correct, wrongs[0]] if correct else unique[:2]
        random.shuffle(options)
        q["options"] = options[:2]
        q["is_free_text"] = False
        return q

    fallback_wrongs = ["None of the above", "Cannot be determined", "All of the above", "Unknown"]
    while len(wrongs) < 3:
        candidate = fallback_wrongs.pop(0) if fallback_wrongs else f"Option {len(wrongs) + 2}"
        if candidate.lower() != correct.lower():
            wrongs.append(candidate)
    options = [correct] + wrongs[:3] if correct else unique[:4]
    random.shuffle(options)
    q["options"] = options[:4]
    q["is_free_text"] = False
    return q

# System prompt for the challenge LLM — stricter than ClassicRoom
CHALLENGE_SYSTEM_PROMPT = """You are an expert educational MCQ generator for a competitive quiz platform.
Return ONLY a valid JSON object — no markdown, no backticks, no extra text.

STRICT JSON structure:
{
  "text": "the question",
  "correct": "the single correct answer",
  "wrong1": "wrong answer or empty string",
  "wrong2": "wrong answer or empty string",
  "wrong3": "wrong answer or empty string",
  "explanation": "1-2 sentences with an interesting fact or context — NOT just restating the answer"
}

RULES:
- QUESTION QUALITY: The question MUST be a properly formatted interrogative sentence (starting with Who, What, Where, When, Why, How, or Which).
- DO NOT generate statement-like questions with a question mark at the end.
- NEVER include the correct answer in the question text.
- Follow the options_rule exactly for this level
- Question text must be concise: one sentence, maximum 22 words
- The explanation must be genuinely interesting — a fun fact, historical context, or surprising detail
- For History/Geography, prefer causes, locations, empires, maps, capitals, rivers, treaties and chronology.
- Avoid GDP, income, census, population, percentage, and random numeric-stat questions unless the topic explicitly asks for demography.
- Mixed still means History or Geography only; do not generate science, art, sports, or generic trivia.
- Return ONLY the JSON, nothing else"""


# Trim generated question text to a concise single-question sentence.
def _shorten_question_text(text: str, max_words: int = 22) -> str:
    cleaned = " ".join((text or "").strip().split())
    if not cleaned:
        return ""
    first_sentence = cleaned.split(".")[0].strip()
    words = first_sentence.split()
    if len(words) <= max_words:
        return first_sentence if first_sentence.endswith("?") else f"{first_sentence}?"
    shortened = " ".join(words[:max_words]).rstrip(" ,;:")
    if not shortened.endswith("?"):
        shortened += "?"
    return shortened


# Compute a normalized text signature for duplicate-detection checks.
def _challenge_signature(text: str) -> str:
    normalized = " ".join((text or "").strip().lower().split())
    return normalized


# Generate one level-aware challenge question payload using the LLM.
async def _generate_challenge_question_llm(
    llm,
    topic: str,
    level: int,
    context: str = "",
) -> Optional[dict]:
    """
    Call the LLM with a level-specific prompt.
    Returns a question dict or None on failure.
    """
    cfg = LEVEL_PROMPTS[level]

    user_prompt = f"""TOPIC: {_challenge_topic_focus(topic)}
LEVEL: {level}/5 — {cfg['description']}
OPTIONS RULE: {cfg['options_rule']}

{"CONTEXT (base your question on this):" + chr(10) + context[:600] if context else "Generate a unique question about " + topic + "."}

Generate ONE unique question following the level and options rule exactly.
Return ONLY the JSON."""

    try:
        response = await llm._chat_completion(
            system    = CHALLENGE_SYSTEM_PROMPT,
            user      = user_prompt,
            temperature = 0.92,
            max_tokens  = 400,
        )
        if not response:
            return None

        parsed = llm._parse_json_response(response)
        if not parsed:
            return None

        if not parsed.get("text") or not parsed.get("correct"):
            return None
        if _is_low_quality_challenge_text(str(parsed.get("text", ""))):
            return None

        correct = str(parsed["correct"]).strip()

        # ── Build options based on level ──────────────────────────────────
        if cfg["is_free_text"]:
            # Level 5: no options, free text input
            options = []
        elif cfg["options_count"] == 2:
            # Level 1: only 2 options
            wrong1 = str(parsed.get("wrong1", "")).strip()
            if not wrong1:
                # LLM didn't follow instructions — generate a generic wrong
                wrong1 = "None of the above"
            options = [correct, wrong1]
            random.shuffle(options)
        else:
            # Levels 2, 3, 4: 4 options
            wrongs = [
                str(parsed.get("wrong1", "")).strip(),
                str(parsed.get("wrong2", "")).strip(),
                str(parsed.get("wrong3", "")).strip(),
            ]
            # Filter out empty strings
            wrongs = [w for w in wrongs if w]
            # Pad if LLM returned fewer than 3 wrongs
            pads = ["None of the above", "Cannot be determined", "All of the above"]
            while len(wrongs) < 3:
                wrongs.append(pads.pop(0))
            options = [correct] + wrongs[:3]
            # Remove duplicates
            seen = set()
            unique = []
            for o in options:
                if o.lower() not in seen:
                    seen.add(o.lower())
                    unique.append(o)
            while len(unique) < 4:
                unique.append(pads.pop(0) if pads else "Unknown")
            options = unique[:4]
            random.shuffle(options)

        return _normalize_challenge_options_for_level({
            "id":           str(uuid.uuid4()),
            "text":         _shorten_question_text(str(parsed["text"])),
            "options":      options,
            "correctAnswer": correct,
            "explanation":  str(parsed.get("explanation", "")).strip(),
            "is_free_text": cfg["is_free_text"],
        }, level)

    except Exception as e:
        logger.error(f"Challenge LLM generation failed at level {level}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────
# DEPENDENCY INJECTORS
# Note: get_db is imported from routers.auth (line 52) and used by most endpoints.
# The helpers below are for LLM/RAG/HTTP dependencies only.
# ─────────────────────────────────────────────────────────────────────────

# Return app.state so helper dependencies can read shared services.
def _get_app_state(request: Request):
    """Get the FastAPI app.state object (contains llm_client, rag_pipeline, etc.)."""
    return request.app.state


# Provide the configured LLM client dependency.
async def get_llm(request: Request):
    """Get LLM client from app state. Raises 503 if unavailable."""
    app_st = _get_app_state(request)
    llm = getattr(app_st, "llm_client", None)
    if llm is None:
        raise HTTPException(503, "LLM service not available")
    return llm


# Provide the optional RAG pipeline dependency.
async def get_rag(request: Request):
    """Get RAG pipeline from app state. Returns None if unavailable (optional)."""
    app_st = _get_app_state(request)
    return getattr(app_st, "rag_pipeline", None)


# Provide the optional shared HTTP client dependency.
async def get_http(request: Request):
    """Get shared HTTP client from app state. Returns None if unavailable (optional)."""
    app_st = _get_app_state(request)
    return getattr(app_st, "http_client", None)


# ─────────────────────────────────────────────────────────────────────────
# ANSWER VERIFICATION
# ─────────────────────────────────────────────────────────────────────────

# Compare submitted answer with persisted answer key in question_bank.
async def _verify_answer(db: AsyncSession, question_id: str, selected: str) -> bool:
    """
    For MCQ levels (1-4): exact match against stored correct_answer.
    For free-text level 5: case-insensitive, strip whitespace.
    Both use the same comparison — level 5 is just more forgiving because
    the user typed it themselves.
    """
    from database.models import QuestionBank

    try:
        question_uuid = uuid.UUID(question_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid question_id format")

    try:
        stmt = select(QuestionBank.correct_answer).where(
            QuestionBank.id == question_uuid
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
    except SQLAlchemyError as exc:
        logger.error("Challenge answer verification query failed: %s", exc)
        raise HTTPException(status_code=503, detail="Answer verification temporarily unavailable")

    if row is None:
        raise HTTPException(status_code=404, detail="Question not found")

    # Normalize both for comparison
    selected_clean = str(selected).strip().lower()
    correct_clean = str(row).strip().lower()

    is_match = selected_clean == correct_clean

    logger.debug(
        f"Verification: selected_raw='{selected}' correct_raw='{row}' "
        f"selected_clean='{selected_clean}' correct_clean='{correct_clean}' "
        f"match={is_match}"
    )

    return is_match


# ═════════════════════════════════════════════════════════════════════════
# ENDPOINT 1 — GET /api/challenge/user/{user_id}/rank
# ═════════════════════════════════════════════════════════════════════════

@challenge_router.get("/user/{user_id}/rank", response_model=UserRankOut)
@limiter.limit("60/minute")
# Return current challenge rank, points, and unlocked levels for a user.
async def get_user_rank(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current=Depends(get_current_user),
):
    user, _ = current
    _ensure_user_match(user_id, str(user.id))
    ranking = await get_or_create_ranking(db, user_id)
    return UserRankOut(
        current_rank     = ranking.current_rank,
        rank_points      = ranking.rank_points,
        available_levels = get_available_levels(ranking.current_rank),
        total_sessions   = ranking.total_sessions,
        total_questions  = ranking.total_questions,
    )


# ═════════════════════════════════════════════════════════════════════════
# ENDPOINT 2 — POST /api/challenge/start-session
# ═════════════════════════════════════════════════════════════════════════

@challenge_router.post("/start-session", response_model=StartSessionOut)
@limiter.limit("20/minute")
# Start a new challenge session at a rank-allowed initial level.
async def start_session(
    body: Annotated[StartSessionRequest, Body()],
    request: Request,
    db: AsyncSession = Depends(get_db),
    current=Depends(get_current_user),
):
    user, _ = current
    await enforce_user_quota(request, user.id, "challenge_start", limit=60, window_seconds=3600)
    _ensure_user_match(body.user_id, str(user.id))
    ranking   = await get_or_create_ranking(db, body.user_id)
    available = get_available_levels(ranking.current_rank)

    if body.starting_level not in available:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Level {body.starting_level} is not available for rank "
                f"{ranking.current_rank}. Available levels: {available}"
            ),
        )

    session = await create_challenge_session(
        db,
        user_id        = body.user_id,
        topic          = body.topic,
        starting_level = body.starting_level,
    )
    await db.commit()
    await db.refresh(session)

    logger.info(
        f"Challenge session started: user={body.user_id[:8]} "
        f"rank={ranking.current_rank} level={body.starting_level} topic={body.topic}"
    )

    # Best-effort pre-generation before the learner starts answering.
    # Warm the current level and nearby levels first so forced deranks/rank-ups
    # stay responsive without flooding the provider across all five levels.
    redis_client = getattr(request.app.state, "redis", None)
    for level_to_warm in _challenge_pregen_levels(body.starting_level):
        await _request_challenge_pregen(
            redis_client,
            topic=body.topic,
            level=level_to_warm,
            session_id=str(session.id),
            force=True,
        )

    return StartSessionOut(
        session_id       = str(session.id),
        current_level    = session.current_level,
        rank_points      = 0,
        available_levels = available,
        current_rank     = ranking.current_rank,
        topic            = body.topic,
    )


# ═════════════════════════════════════════════════════════════════════════
# ENDPOINT 3 — GET /api/challenge/session/{session_id}
# ═════════════════════════════════════════════════════════════════════════

@challenge_router.get("/session/{session_id}", response_model=ChallengeSessionOut)
@limiter.limit("60/minute")
# Return full challenge session state for the owning user.
async def get_session(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current=Depends(get_current_user),
):
    user, _ = current
    session = await get_challenge_session(db, session_id)
    if session is None:
        raise HTTPException(404, f"Session {session_id} not found")
    if str(session.user_id) != str(user.id):
        raise HTTPException(403, "You are not allowed to access this session")

    return ChallengeSessionOut(
        session_id      = str(session.id),
        user_id         = str(session.user_id),
        topic           = session.topic,
        starting_level  = session.starting_level,
        current_level   = session.current_level,
        rank_points     = session.rank_points,
        streak_correct  = session.streak_correct,
        streak_wrong    = session.streak_wrong,
        total_questions = session.total_questions,
        correct_answers = session.correct_answers,
        is_completed    = session.is_completed,
    )


# ═════════════════════════════════════════════════════════════════════════
# ENDPOINT 4 — PATCH /api/challenge/session/{session_id}/change-level
# ═════════════════════════════════════════════════════════════════════════

@challenge_router.patch("/session/{session_id}/change-level", response_model=ChangeLevelOut)
@limiter.limit("30/minute")
# Move session level up/down within rank bounds.
async def change_level(
    session_id: str,
    body: Annotated[ChangeLevelRequest, Body()],
    request: Request,
    db: AsyncSession = Depends(get_db),
    current=Depends(get_current_user),
):
    user, _ = current
    session = await get_challenge_session(db, session_id)
    if session is None:
        raise HTTPException(404, f"Session {session_id} not found")
    if str(session.user_id) != str(user.id):
        raise HTTPException(403, "You are not allowed to modify this session")
    if session.is_completed:
        raise HTTPException(400, "Cannot change level on a completed session")

    # Get user rank to enforce rank-bounded level change
    ranking   = await get_or_create_ranking(db, str(session.user_id))
    new_level = apply_level_change(session.current_level, body.direction, ranking.current_rank)

    session.current_level = new_level
    await db.commit()

    logger.info(
        f"Level change: session={session_id[:8]} "
        f"{body.direction} → level {new_level} ({body.reason})"
    )

    return ChangeLevelOut(
        session_id = session_id,
        new_level  = new_level,
        direction  = body.direction,
        reason     = body.reason,
    )


# ═════════════════════════════════════════════════════════════════════════
# ENDPOINT 5 — POST /api/challenge/generate-question
# ═════════════════════════════════════════════════════════════════════════

@challenge_router.post("/generate-question", response_model=ChallengeQuestionOut)
@limiter.limit("40/minute")
# Generate and persist one new challenge question for the active session.
async def generate_challenge_question(
    request: Request,
    body: Annotated[GenerateChallengeQuestionRequest, Body()],
    db          : AsyncSession = Depends(get_db),
    llm                        = Depends(get_llm),
    rag                        = Depends(get_rag),
    http_client                = Depends(get_http),
    current=Depends(get_current_user),
):
    user, _ = current
    await enforce_user_quota(request, user.id, "challenge_generate", limit=160, window_seconds=3600)
    _ensure_user_match(body.user_id, str(user.id))
    session = await get_challenge_session(db, body.session_id)
    if session is None:
        raise HTTPException(404, f"Session {body.session_id} not found")
    if str(session.user_id) != str(user.id):
        raise HTTPException(403, "You are not allowed to use this session")
    if session.is_completed:
        raise HTTPException(400, "Session is already completed")

    # SECURITY: trust the server session level, not the client value. This is
    # essential after force derank: the next question must be generated at level 1.
    effective_level = int(session.current_level or body.level or 1)
    if body.level != effective_level:
        logger.warning(
            "Level mismatch: client sent level=%s but session is at level=%s. Using session level. user=%s",
            body.level,
            effective_level,
            body.user_id[:8],
        )

    correct_pts, _ = CHALLENGE_POINTS_TABLE[effective_level]

    async def _return_question(qdict: dict) -> ChallengeQuestionOut:
        normalized = _normalize_challenge_options_for_level(qdict, effective_level)
        await _set_pending_challenge_question(request, body.session_id, normalized["id"])
        await _remember_issued_question(request, body.session_id, normalized["id"])
        return ChallengeQuestionOut(
            id=str(normalized["id"]),
            text=str(normalized["text"]),
            options=list(normalized.get("options") or []),
            explanation="",
            level=effective_level,
            points_value=correct_pts,
            is_free_text=bool(normalized.get("is_free_text", False)),
        )

    # Retry-safe behavior: if a question was already returned but not answered,
    # return that same question. This avoids generating duplicates and prevents
    # the frontend from submitting an old question after a network/generation error.
    pending_qid = await _get_pending_challenge_question(request, body.session_id)
    if pending_qid and await _row_is_unanswered_in_session(db, body.session_id, pending_qid):
        try:
            pending_row = await db.get(QuestionBank, uuid.UUID(str(pending_qid)))
        except Exception:
            pending_row = None
        if pending_row is not None and not _is_low_quality_challenge_text(pending_row.question_text):
            return await _return_question(_question_out_from_row(pending_row, effective_level))

    # Best-effort queue prewarm. The worker fills Redis before/around the session,
    # then this endpoint pops a ready persisted question instantly when available.
    redis_client = getattr(request.app.state, "redis", None)
    queue_key = challenge_ready_queue_key(body.topic, effective_level)
    provider_name = "groq"
    provider_in_backoff = await provider_backoff_active(redis_client, provider_name)
    if not provider_in_backoff:
        await _request_challenge_pregen(
            redis_client,
            topic=body.topic,
            level=effective_level,
            session_id=body.session_id,
            force=False,
        )

    for _ in range(10):
        queued_id = await pop_ready_question_id(redis_client, queue_key)
        if not queued_id:
            break
        try:
            queued_row = await db.get(QuestionBank, uuid.UUID(str(queued_id)))
        except Exception:
            queued_row = None
        if queued_row is None:
            continue
        if _is_low_quality_challenge_text(queued_row.question_text):
            continue
        if not await _row_is_unanswered_in_session(db, body.session_id, str(queued_row.id)):
            continue
        # After consuming one ready question, top the same level back toward the configured target.
        if not provider_in_backoff:
            await _request_challenge_pregen(
                redis_client,
                topic=body.topic,
                level=effective_level,
                session_id=body.session_id,
                force=False,
            )
        return await _return_question(_question_out_from_row(queued_row, effective_level))

    # Recent signatures from this session and the user's wider history.
    recent_question_texts = (
        await db.execute(
            select(QuestionBank.question_text)
            .join(ChallengeAnswer, ChallengeAnswer.question_id == QuestionBank.id)
            .where(ChallengeAnswer.session_id == session.id)
            .order_by(ChallengeAnswer.created_at.desc())
            .limit(40)
        )
    ).scalars().all()
    recent_signatures = {
        _challenge_signature(str(text))
        for text in recent_question_texts
        if str(text).strip()
    }
    recent_signatures.update(await _get_user_seen_challenge_signatures(db, user.id, body.topic))

    question_dict: Optional[dict] = None
    gov_decision = None
    context = ""
    rag_candidate: Optional[dict] = None

    async def _select_candidate(candidate: Optional[dict]):
        if not candidate:
            return None, None

        normalized = _normalize_challenge_options_for_level(candidate, effective_level)
        signature = _challenge_signature(normalized.get("text", ""))
        if not signature or signature in recent_signatures:
            logger.warning("Rejected repeated challenge prompt signature session=%s", str(session.id)[:8])
            return None, None
        if _is_low_quality_challenge_text(normalized.get("text", "")):
            logger.warning("Rejected low-quality/stat-heavy challenge candidate")
            return None, None

        try:
            cand_text_norm = str(normalized.get("text", "")).strip().lower()
            dup_result = await db.execute(
                select(QuestionBank.id).where(func.lower(QuestionBank.question_text) == cand_text_norm).limit(1)
            )
            existing_id = dup_result.scalar_one_or_none()
            if existing_id and str(existing_id) not in {str(normalized.get("id"))}:
                existing_row = await db.get(QuestionBank, existing_id)
                if existing_row is not None and await _row_is_unanswered_in_session(db, body.session_id, str(existing_id)):
                    return _question_out_from_row(existing_row, effective_level), None
                return None, None
        except Exception:
            pass

        try:
            from services.governance_service import GovernanceService
            decision = await GovernanceService.evaluate_candidate(
                db,
                question_id=normalized.get("id"),
                room="challenge",
                action="persist",
                topic=body.topic,
                question_text=str(normalized.get("text", "")),
                correct_answer=str(normalized.get("correctAnswer", "")),
                explanation=str(normalized.get("explanation", "")),
                options=list(normalized.get("options") or []),
            )
            if decision is not None and not decision.approved:
                logger.warning("Challenge governance rejected candidate: %s", list(decision.reasons or []))
                return None, None
        except Exception as exc:
            logger.warning("Challenge governance evaluation failed: %s", exc)
            decision = None

        return normalized, decision

    # Use your RAG pipeline to recover the old quality: verified context first,
    # LLM generation second. Mixed is narrowed to history/geography only.
    rag_topic = random.choice(["History", "Geography"]) if body.topic == "Mixed" else body.topic
    if ENABLE_NO_INLINE_LLM:
        # Never block the request on RAG/LLM: enqueue a refill and use the DB
        # fallback bank below. See QUALITY_PERF_ROADMAP_2026-07-04.md item 4.
        await _request_challenge_pregen(
            redis_client, topic=rag_topic, level=effective_level, session_id=None, force=True
        )
    elif provider_in_backoff:
        logger.info("Challenge live generation skipped due to active provider backoff")
    elif effective_level >= 2 and rag is not None and http_client is not None:
        try:
            rag_result = await rag.run(
                topic=rag_topic,
                difficulty=effective_level,
                user_accuracy=0.5,
                llm_client=llm,
                http_client=http_client,
            )
            if isinstance(rag_result, dict):
                if rag_result.get("text") and (rag_result.get("correctAnswer") or rag_result.get("correct")):
                    rag_candidate = {
                        "id": str(rag_result.get("id") or uuid.uuid4()),
                        "text": str(rag_result.get("text") or ""),
                        "options": list(rag_result.get("options") or []),
                        "correctAnswer": str(rag_result.get("correctAnswer") or rag_result.get("correct") or ""),
                        "explanation": str(rag_result.get("explanation") or ""),
                        "is_free_text": effective_level == 5,
                    }
                    context = f"Reference question/context: {rag_candidate['text']}\nExplanation: {rag_candidate['explanation']}"
                elif rag_result.get("context_text"):
                    context = str(rag_result.get("context_text") or "")
            logger.info("Challenge RAG used for level %s topic=%s", effective_level, rag_topic)
            if getattr(llm, "last_status_code", None) == 429:
                await mark_provider_backoff(redis_client, provider_name)
                provider_in_backoff = True
        except Exception as exc:
            logger.warning("Challenge RAG context fetch failed: %s", exc)

    if rag_candidate is not None and not _is_low_quality_challenge_text(rag_candidate.get("text", "")):
        question_dict, gov_decision = await _select_candidate(rag_candidate)

    if not question_dict and not provider_in_backoff and not ENABLE_NO_INLINE_LLM:
        for _ in range(3):
            candidate = await _generate_challenge_question_llm(
                llm=llm,
                topic=_challenge_topic_focus(body.topic),
                level=effective_level,
                context=context,
            )
            if not candidate:
                if getattr(llm, "last_status_code", None) == 429:
                    await mark_provider_backoff(redis_client, provider_name)
                    provider_in_backoff = True
                    # Let fallback bank below try first; if empty we return a clear 429.
                    break
                continue
            question_dict, gov_decision = await _select_candidate(candidate)
            if question_dict:
                break

    # Fallback bank: include previous challenge_llm rows, but filter out stat-heavy
    # questions. This keeps quality while avoiding 503 after forced derank.
    if not question_dict:
        try:
            seen_ids = await ClassicService.get_user_seen_question_ids(db=db, user_id=user.id, topic="mix")
            stmt = select(QuestionBank).where(
                QuestionBank.gov_approved == True,  # noqa: E712
                QuestionBank.gov_safe == True,      # noqa: E712
                QuestionBank.difficulty_irt >= float(effective_level) - 0.5,
                QuestionBank.difficulty_irt < float(effective_level) + 0.5,
            )
            if body.topic != "Mixed":
                stmt = stmt.where(QuestionBank.topic.ilike(f"%{body.topic}%"))
            if seen_ids:
                stmt = stmt.where(QuestionBank.id.notin_(list(seen_ids)))
            rows = (await db.execute(stmt.order_by(func.random()).limit(30))).scalars().all()
            for row in rows:
                if _is_low_quality_challenge_text(row.question_text):
                    continue
                if not await _row_is_unanswered_in_session(db, body.session_id, str(row.id)):
                    continue
                question_dict = _question_out_from_row(row, effective_level)
                gov_decision = None
                break
        except Exception as exc:
            logger.warning("Challenge fallback bank lookup failed: %s", exc)

    if not question_dict:
        if provider_in_backoff or getattr(llm, "last_status_code", None) == 429:
            raise HTTPException(
                status_code=429,
                detail="LLM rate limit reached and no ready challenge question is available yet. Please retry in a few seconds.",
                headers={"Retry-After": "3"},
            )
        raise HTTPException(503, "Could not generate a fresh challenge question. Please retry.")

    # Persist generated questions before returning; bank rows selected as fallback
    # are already persisted, and store_question is idempotent for duplicate UUIDs.
    existing_row = None
    try:
        existing_row = await db.get(QuestionBank, uuid.UUID(str(question_dict["id"])))
    except Exception:
        existing_row = None

    if existing_row is None:
        from database import crud as classic_crud
        try:
            await classic_crud.store_question(
                db,
                question_id=str(question_dict["id"]),
                question_text=str(question_dict["text"]),
                correct_answer=str(question_dict["correctAnswer"]),
                options=list(question_dict.get("options") or []),
                explanation=str(question_dict.get("explanation", "")),
                topic=body.topic,
                difficulty=effective_level,
                source="challenge_llm",
            )
            if gov_decision is not None:
                try:
                    from services.governance_service import GovernanceService
                    stored_row = await db.get(QuestionBank, uuid.UUID(str(question_dict["id"])))
                    if stored_row is not None:
                        await GovernanceService.apply_decision_to_persisted_row(db, row=stored_row, decision=gov_decision)
                        await db.commit()
                except Exception as exc:
                    await db.rollback()
                    logger.warning("Challenge governance persistence hook failed: %s", exc)
        except Exception:
            await db.rollback()
            logger.exception("Could not persist challenge question before returning it")
            raise HTTPException(500, "Could not persist challenge question. Please retry.")

    return await _return_question(question_dict)


# ═════════════════════════════════════════════════════════════════════════
# ENDPOINT 6 — POST /api/challenge/submit-answer
# ═════════════════════════════════════════════════════════════════════════

@challenge_router.post("/submit-answer", response_model=SubmitChallengeAnswerOut)
@limiter.limit("80/minute")
# Verify one answer submission and apply points/streak/level updates.
async def submit_challenge_answer(
    request: Request,
    body: Annotated[SubmitChallengeAnswerRequest, Body()],
    db: AsyncSession = Depends(get_db),
    current=Depends(get_current_user),
):
    user, _ = current
    await enforce_user_quota(request, user.id, "challenge_submit", limit=320, window_seconds=3600)
    _ensure_user_match(body.user_id, str(user.id))
    # VALIDATION: Check answer is not empty
    if not body.answer or not str(body.answer).strip():
        logger.error(f"EMPTY ANSWER SUBMITTED: user={body.user_id} session={body.session_id} question={body.question_id}")
        raise HTTPException(
            status_code=400,
            detail="Answer cannot be empty. Please select an option or type your answer."
        )
    
    session = await get_challenge_session(db, body.session_id)
    if session is None:
        raise HTTPException(404, f"Session {body.session_id} not found")
    if str(session.user_id) != str(user.id):
        raise HTTPException(403, "You are not allowed to submit for this session")
    if session.is_completed:
        raise HTTPException(400, "Session already completed")

    if not await _session_has_issued_question(request, body.session_id, body.question_id):
        # If the issued-question tracker missed this ID (dev fallback), allow submit
        # when the question exists in the DB and then remember it for the session.
        try:
            q_uuid = uuid.UUID(str(body.question_id))
            q_row = await db.get(QuestionBank, q_uuid)
            if q_row is not None:
                logger.info(
                    "Issued-question tracker miss recovered from DB: session=%s question=%s",
                    str(body.session_id)[:8],
                    str(body.question_id)[:8],
                )
                await _remember_issued_question(request, body.session_id, body.question_id)
            else:
                logger.warning(
                    "Rejected challenge answer for unissued question: user=%s session=%s question=%s",
                    str(body.user_id)[:8],
                    str(body.session_id)[:8],
                    str(body.question_id)[:8],
                )
                raise HTTPException(409, "Question was not issued for this session")
        except Exception as exc:
            logger.warning(
                "Rejected challenge answer for unissued question (lookup failed): %s",
                exc,
            )
            raise HTTPException(409, "Question was not issued for this session")

    # Verify question exists in database
    from database.models import QuestionBank
    try:
        question_uuid = uuid.UUID(body.question_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid question_id format")

    question_check = await db.execute(
        select(QuestionBank).where(QuestionBank.id == question_uuid)
    )
    question_row = question_check.scalar_one_or_none()
    if question_row is None:
        raise HTTPException(
            status_code=400,
            detail="Question not found in database. Was it properly generated?"
        )

    # Cache question fields as plain values.
    # IMPORTANT: IntegrityError handling triggers db.rollback(), which expires ORM
    # attributes. Accessing expired ORM attributes in async context can raise
    # MissingGreenlet. Use cached scalar values in all return paths.
    question_correct_answer = str(question_row.correct_answer)
    question_explanation = (
        question_row.explanation
        or "Use elimination and topic context to identify the strongest answer."
    )

    try:
        session_uuid = uuid.UUID(str(body.session_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid challenge ID format")

    locked_session_result = await db.execute(
        select(ChallengeSession).where(ChallengeSession.id == session_uuid).with_for_update()
    )
    locked_session = locked_session_result.scalar_one_or_none()
    if locked_session is None:
        raise HTTPException(404, f"Session {body.session_id} not found")
    if str(locked_session.user_id) != str(user.id):
        raise HTTPException(403, "You are not allowed to submit for this session")
    if locked_session.is_completed:
        raise HTTPException(400, "Session already completed")
    session = locked_session

    # Anti-abuse: duplicate submits conflict and must not mutate score/streak.
    existing_answer_result = await db.execute(
        select(ChallengeAnswer).where(
            ChallengeAnswer.session_id == uuid.UUID(str(body.session_id)),
            ChallengeAnswer.question_id == uuid.UUID(str(body.question_id)),
        ).limit(1)
    )
    existing_answer = existing_answer_result.scalar_one_or_none()
    if existing_answer is not None:
        logger.warning(
            "Duplicate challenge answer replayed idempotently: user=%s session=%s question=%s",
            str(body.user_id)[:8],
            str(body.session_id)[:8],
            str(body.question_id)[:8],
        )
        return SubmitChallengeAnswerOut(
            id=str(existing_answer.id),
            is_correct=bool(existing_answer.is_correct),
            correct_answer=question_correct_answer,
            explanation=question_explanation,
            points_change=int(existing_answer.points_change or 0),
            new_rank_points=int(session.rank_points or 0),
            new_level=int(session.current_level or 1),
            streak_correct=int(session.streak_correct or 0),
            streak_wrong=int(session.streak_wrong or 0),
            force_level_change=None,
        )

    # Verify answer server-side
    is_correct    = await _verify_answer(db, body.question_id, body.answer)
    logger.info(
        f"ANSWER VERIFICATION: user={body.user_id[:8]} question={body.question_id[:8]} "
        f"submitted='{body.answer}' correct_stored='{question_correct_answer}' "
        f"match={is_correct}"
    )
    current_level = session.current_level
    points_change = calculate_points(current_level, is_correct)

    # Update streaks
    new_streak_correct, new_streak_wrong = update_streaks_after_answer(
        session.streak_correct, session.streak_wrong, is_correct
    )

    # Check streak trigger — use rank-bounded level change
    level_trigger = check_streak_trigger(new_streak_correct, new_streak_wrong)
    new_level     = current_level
    force_level_change_out: Optional[ForceLevelChange] = None

    if level_trigger:
        # Get user rank for boundary enforcement
        ranking   = await get_or_create_ranking(db, str(session.user_id))
        new_level = apply_level_change(
            current_level,
            level_trigger["direction"],
            ranking.current_rank,           # ← rank boundary enforced here
        )

        # Reset the streak that triggered the change
        if level_trigger["direction"] == "up":
            new_streak_correct = 0
        else:
            new_streak_wrong = 0

        force_level_change_out = ForceLevelChange(
            direction = level_trigger["direction"],
            reason    = level_trigger["reason"],
        )
        logger.info(
            f"Level change triggered: session={body.session_id[:8]} "
            f"{level_trigger['direction']} → level {new_level} "
            f"(rank boundary enforced)"
        )

    # Persist answer row and session aggregate atomically.
    stored_answer = None
    try:
        stored_answer = await record_challenge_answer(
            db,
            session_id      = body.session_id,
            question_id     = body.question_id,
            chosen_answer   = body.answer,
            is_correct      = is_correct,
            points_change   = points_change,
            level_at_answer = current_level,
            time_taken      = body.time_taken,
        )
        updated_session = await update_session_after_answer(
            db,
            session            = session,
            is_correct         = is_correct,
            points_change      = points_change,
            new_streak_correct = new_streak_correct,
            new_streak_wrong   = new_streak_wrong,
            new_level          = new_level,
        )
        await db.commit()
        await db.refresh(stored_answer)
        await db.refresh(updated_session)
        await _clear_pending_challenge_question(request, body.session_id, body.question_id)
    except IntegrityError:
        await db.rollback()
        logger.warning(
            "IntegrityError on challenge answer insert; duplicate blocked session=%s question=%s",
            str(body.session_id)[:8],
            str(body.question_id)[:8],
        )
        raise HTTPException(409, "Duplicate answer detected")
    except Exception:
        await db.rollback()
        raise

    logger.info(
        f"Challenge answer: correct={is_correct} pts={points_change:+d} "
        f"level={current_level}→{new_level} "
        f"session_pts={updated_session.rank_points}"
    )

    return SubmitChallengeAnswerOut(
        id                 = str(stored_answer.id) if stored_answer is not None else None,
        is_correct         = is_correct,
        correct_answer     = question_correct_answer,
        explanation        = question_explanation,
        points_change      = points_change,
        new_rank_points    = updated_session.rank_points,
        new_level          = new_level,
        streak_correct     = new_streak_correct,
        streak_wrong       = new_streak_wrong,
        force_level_change = force_level_change_out,
    )


# ═════════════════════════════════════════════════════════════════════════
# ENDPOINT 7 — POST /api/challenge/session/{session_id}/end
# ═════════════════════════════════════════════════════════════════════════

@challenge_router.post("/session/{session_id}/end", response_model=EndSessionOut)
@limiter.limit("20/minute")
# Finalize a session and update global ranking progression.
async def end_session(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current=Depends(get_current_user),
):
    user, _ = current
    session = await get_challenge_session(db, session_id)
    if session is None:
        raise HTTPException(404, f"Session {session_id} not found")
    if str(session.user_id) != str(user.id):
        raise HTTPException(403, "You are not allowed to end this session")

    if session.is_completed:
        ranking = await get_or_create_ranking(db, str(session.user_id))
        return EndSessionOut(
            session_id          = session_id,
            total_questions     = session.total_questions,
            correct_answers     = session.correct_answers,
            total_points_earned = session.rank_points,
            new_rank            = ranking.current_rank,
            new_rank_points     = ranking.rank_points,
            rank_changed        = False,
        )

    old_ranking = await get_or_create_ranking(db, str(session.user_id))
    old_rank    = old_ranking.current_rank

    await finalize_session(db, session)

    updated_ranking = await update_global_ranking(
        db,
        user_id           = str(session.user_id),
        session_points    = session.rank_points,
        session_questions = session.total_questions,
        session_streak    = max(session.streak_correct, session.streak_wrong),
    )

    rank_changed = updated_ranking.current_rank != old_rank

    logger.info(
        f"Session ended: session={session_id[:8]} "
        f"pts={session.rank_points} rank={old_rank}→{updated_ranking.current_rank}"
    )

    return EndSessionOut(
        session_id          = session_id,
        total_questions     = session.total_questions,
        correct_answers     = session.correct_answers,
        total_points_earned = session.rank_points,
        new_rank            = updated_ranking.current_rank,
        new_rank_points     = updated_ranking.rank_points,
        rank_changed        = rank_changed,
    )
