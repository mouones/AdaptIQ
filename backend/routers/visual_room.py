"""
routers/visual_room.py
All VisualRoom API endpoints.

Endpoints:
  POST /api/visual/start-session         → session_id
  GET  /api/visual/next                  → VisualQuestionOut (no correct answer)
  POST /api/visual/submit                → is_correct + explanation + next_question
  GET  /api/visual/hint                  → hint text (no correct answer revealed)
  GET  /api/visual/explanation           → explanation for a question_id
  POST /api/visual/session/{id}/end      → session summary

Design notes:
  - correct_answer is NEVER sent to the frontend in the question payload.
    It is only revealed in the submit response AFTER the user answers.
  - Hints are generated from question_text + paragraph only — the correct
    answer is not passed to the hint generator.
  - Sessions track seen question IDs to avoid repeats within one session.
  - LLM generation happens on first use of a question, then the result is
    stored so subsequent calls are instant DB reads.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.visual_models import VisualQuestion, VisualSession
from dependencies import limiter
from config import VISUAL_PREGEN_BATCH_SIZE, VISUAL_QUESTIONS_PER_SESSION
from pydantic_visual import (
    StartVisualSessionRequest,
    StartVisualSessionResponse,
    VisualQuestionOut,
    SubmitVisualAnswerRequest,
    SubmitVisualAnswerResponse,
    VisualHintResponse,
    VisualExplanationResponse,
)
from services.visual_room_service import (
    create_visual_session,
    get_visual_session,
    get_next_question,
    generate_and_store_question,
    visual_question_needs_generation,
    verify_mcq_answer,
    verify_text_answer,
    update_question_stats,
    generate_visual_hint,
    get_visual_warmup_candidates,
    _add_seen_id,
    LEVEL_OPTIONS_COUNT,
    normalize_visual_options_for_level,
)
from routers.auth import get_current_user
from services.rate_limits import enforce_user_quota
from services.security_utils import safe_svg_shape_payload

logger = logging.getLogger(__name__)
visual_router = APIRouter(prefix="/api/visual", tags=["Visual Room"])
_visual_warmup_guard = asyncio.Lock()
_visual_warmup_inflight: set[tuple[str, int]] = set()


# ─── Dependencies ─────────────────────────────────────────────────────────────

async def _get_db(request: Request):
    factory = getattr(request.app.state, "db_session_factory", None)
    if factory is None:
        raise HTTPException(503, "Database not available")
    async with factory() as db:
        yield db


async def _get_llm(request: Request):
    llm = getattr(request.app.state, "llm_client", None)
    if llm is None:
        raise HTTPException(503, "LLM not available — set GROQ_API_KEY")
    return llm


def _require_session_owner(session: VisualSession, current) -> None:
    user, _ = current
    if str(session.user_id) != str(user.id):
        raise HTTPException(403, "Not authorized for this visual session")


def _close_visual_session_if_needed(session: VisualSession) -> bool:
    """Mark a Visual session completed and stamp `ended_at` once."""
    changed = False
    if not session.is_completed:
        session.is_completed = True
        changed = True
    if session.ended_at is None:
        session.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
        changed = True
    return changed


async def _warm_visual_question_batch(
    session_factory,
    llm_client,
    topic: str,
    level: int,
    limit: int,
) -> None:
    topic_key = (topic or "mixed").lower()
    warm_level = _clamp_visual_level(level)
    key = (topic_key, warm_level)

    async with _visual_warmup_guard:
        if key in _visual_warmup_inflight:
            return
        _visual_warmup_inflight.add(key)

    try:
        async with session_factory() as db:
            candidates = await get_visual_warmup_candidates(
                db,
                topic,
                warm_level,
                limit=limit,
            )
            if not candidates:
                return

            logger.info(
                "[VisualRoom] warmup start: topic=%s level=%s count=%s",
                topic_key,
                warm_level,
                len(candidates),
            )

            for candidate in candidates:
                try:
                    await generate_and_store_question(db, candidate, warm_level, llm_client)
                except Exception:
                    logger.exception(
                        "[VisualRoom] warmup failed for q=%s topic=%s level=%s",
                        str(candidate.id)[:8],
                        topic_key,
                        warm_level,
                    )

            logger.info(
                "[VisualRoom] warmup done: topic=%s level=%s",
                topic_key,
                warm_level,
            )
    except Exception:
        logger.exception(
            "[VisualRoom] warmup task failed: topic=%s level=%s",
            topic_key,
            warm_level,
        )
    finally:
        async with _visual_warmup_guard:
            _visual_warmup_inflight.discard(key)


def _queue_visual_warmup(
    background_tasks: BackgroundTasks,
    request: Request,
    topic: str,
    level: int,
    *,
    limit: int = VISUAL_PREGEN_BATCH_SIZE,
) -> None:
    if limit <= 0:
        return
    session_factory = getattr(request.app.state, "db_session_factory", None)
    if session_factory is None:
        return
    llm_client = getattr(request.app.state, "llm_client", None)
    background_tasks.add_task(
        _warm_visual_question_batch,
        session_factory,
        llm_client,
        topic,
        _clamp_visual_level(level),
        limit,
    )


VISUAL_STREAK_UP = 4
VISUAL_STREAK_DOWN = 2


def _clamp_visual_level(level: int) -> int:
    return max(1, min(5, int(level or 1)))


def _apply_visual_level_progression(session: VisualSession, is_correct: bool) -> tuple[int, int]:
    """Update visual session streaks and level. Returns (old_level, new_level)."""
    old_level = _clamp_visual_level(session.level)
    new_level = old_level
    if is_correct:
        session.streak_correct = int(getattr(session, "streak_correct", 0) or 0) + 1
        session.streak_wrong = 0
        if session.streak_correct >= VISUAL_STREAK_UP:
            new_level = _clamp_visual_level(old_level + 1)
            session.streak_correct = 0
    else:
        session.streak_wrong = int(getattr(session, "streak_wrong", 0) or 0) + 1
        session.streak_correct = 0
        if session.streak_wrong >= VISUAL_STREAK_DOWN:
            new_level = _clamp_visual_level(old_level - 1)
            session.streak_wrong = 0
    session.level = new_level
    return old_level, new_level


def _question_to_out(q: VisualQuestion, level: int) -> VisualQuestionOut:
    import json as _json
    from services.visual_room_service import should_show_shape

    try:
        options = _json.loads(q.options_json or "[]")
    except Exception:
        options = []

    if level == 5:
        options = []
        question_type = "T"
        options_count = 0
    else:
        question_type = "M"
        options_count = LEVEL_OPTIONS_COUNT.get(level, 4)
        options = normalize_visual_options_for_level(
            options,
            q.correct_answer or "",
            q.topic or "",
            level,
        )

    # L1 always flag only. L2+ use probability.
    shape_payload = None
    if level == 1:
        show_flag  = True
        show_shape = False
    else:
        use_shape  = should_show_shape(level=level, topic=q.topic, has_shape=bool(q.shape_svg))
        shape_payload = safe_svg_shape_payload(q.shape_svg) if use_shape else None
        show_shape = bool(shape_payload)
        show_flag  = not show_shape

    return VisualQuestionOut(
        id            = str(q.id),
        image_url     = q.image_url,
        text          = q.question_text or "What does this image depict?",
        options       = options,
        topic         = q.topic,
        level         = level,
        question_type = question_type,
        options_count = options_count,
        shape_svg     = None,
        shape_path    = shape_payload["path"] if shape_payload else None,
        shape_view_box = shape_payload["viewBox"] if shape_payload else None,
        show_flag     = show_flag,
        show_shape    = show_shape,
    )

# ═══════════════════════════════════════════════════════════════════════
# POST /api/visual/start-session
# ═══════════════════════════════════════════════════════════════════════

@visual_router.post("/start-session", response_model=StartVisualSessionResponse)
@limiter.limit("10/minute")
async def start_visual_session(
    body: StartVisualSessionRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    current=Depends(get_current_user),
):
    """
    Start a new VisualRoom session.
    Creates a VisualSession row that tracks seen questions and score.
    """
    user, _ = current
    uid = user.id
    await enforce_user_quota(request, uid, "visual_start", limit=60, window_seconds=3600)
    if body.user_id and str(body.user_id) != str(uid):
        raise HTTPException(403, "Not authorized to start a session for another user")

    async for db in _get_db(request):
        session = await create_visual_session(
            db,
            user_id         = str(uid),
            topic           = body.topic,
            level           = _clamp_visual_level(body.level),
            total_questions = VISUAL_QUESTIONS_PER_SESSION,
        )
        _queue_visual_warmup(background_tasks, request, body.topic, session.level)
        return StartVisualSessionResponse(
            session_id      = str(session.id),
            topic           = body.topic,
            level           = session.level,
            total_questions = session.total_questions,
        )


# ═══════════════════════════════════════════════════════════════════════
# GET /api/visual/next
# ═══════════════════════════════════════════════════════════════════════

@visual_router.get("/next", response_model=VisualQuestionOut)
@limiter.limit("40/minute")
async def get_next_visual_question(
    request:    Request,
    session_id: str = Query(..., description="Active session ID"),
    current=Depends(get_current_user),
):
    """
    Fetch the next question for an active session.

    If the question has no LLM-generated content yet (question_text IS NULL),
    generates it now and stores it before returning — so the next request is
    a fast DB read.

    Correct answer is NOT included in the response.
    """
    user, _ = current
    await enforce_user_quota(request, user.id, "visual_next", limit=240, window_seconds=3600)
    async for db in _get_db(request):
        llm = getattr(request.app.state, "llm_client", None)

        # Load session
        session = await get_visual_session(db, session_id)
        if session is None:
            raise HTTPException(404, f"Session {session_id} not found")
        _require_session_owner(session, current)
        if session.is_completed:
            raise HTTPException(400, "Session is already completed")

        # Select question
        visual_q = await get_next_question(db, session.topic, session.level, session)
        if visual_q is None:
            raise HTTPException(
                503,
                f"No visual questions available for topic={session.topic} level={session.level}. "
                "Run the ingestion script first."
            )

        # Generate content on first use OR if placeholders are present.
        # If llm is unavailable, generate_and_store_question will fall back to
        # a deterministic (caption-based) MCQ instead of "Option A/B/C/D".
        if visual_question_needs_generation(visual_q, session.level):
            visual_q = await generate_and_store_question(db, visual_q, session.level, llm)

        # Mark as seen in this session
        await _add_seen_id(db, session, str(visual_q.id))

        return _question_to_out(visual_q, session.level)


# ═══════════════════════════════════════════════════════════════════════
# POST /api/visual/submit
# ═══════════════════════════════════════════════════════════════════════

@visual_router.post("/submit", response_model=SubmitVisualAnswerResponse)
@limiter.limit("80/minute")
async def submit_visual_answer(
    body: SubmitVisualAnswerRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    current=Depends(get_current_user),
):
    """
    Submit an answer.

    Verifies server-side (correct_answer stored in DB, never exposed to frontend).
    Updates n_attempts / n_correct / difficulty_actual.
    Returns the correct answer + explanation AFTER submission.
    Optionally returns the next question in the same response to save a round-trip.
    """
    user, _ = current
    await enforce_user_quota(request, user.id, "visual_submit", limit=480, window_seconds=3600)
    async for db in _get_db(request):
        llm = getattr(request.app.state, "llm_client", None)

        # Load question
        result = await db.execute(
            select(VisualQuestion).where(VisualQuestion.id == uuid.UUID(body.question_id))
        )
        visual_q = result.scalar_one_or_none()
        if visual_q is None:
            raise HTTPException(404, f"Question {body.question_id} not found")
        if not visual_q.correct_answer:
            raise HTTPException(400, "Question has not been generated yet — call /next first")

        # Load session
        session = await get_visual_session(db, body.session_id)
        if session is None:
            raise HTTPException(404, f"Session {body.session_id} not found")
        _require_session_owner(session, current)
        user, _ = current
        if body.user_id and str(body.user_id) != str(user.id):
            raise HTTPException(403, "Not authorized to submit for another user")

        # Verify answer
        if visual_q.question_type == 'T':
            is_correct = await verify_text_answer(
                body.chosen_answer,
                visual_q.correct_answer,
                llm,
            )
        else:
            is_correct = verify_mcq_answer(body.chosen_answer, visual_q.correct_answer)

        # Update DB stats
        await update_question_stats(db, visual_q, is_correct)

        # Update session score, index and answer time so dashboard learning time
        # includes Visual Room too. Clamp client-reported milliseconds to keep
        # accidental huge values or negative timers from polluting stats.
        if is_correct:
            session.score += 1
        old_level, new_level = _apply_visual_level_progression(session, is_correct)
        if old_level != new_level:
            logger.info(
                "[VisualRoom] level change: session=%s %s -> %s",
                str(session.id)[:8], old_level, new_level,
            )
            _queue_visual_warmup(background_tasks, request, session.topic, new_level)
        reported_ms = int(body.user_time_ms or 0)
        reported_ms = max(0, min(reported_ms, 5 * 60 * 1000))
        session.total_time_ms = int(session.total_time_ms or 0) + reported_ms
        session.current_index += 1
        if session.current_index >= session.total_questions:
            _close_visual_session_if_needed(session)
        await db.commit()

        # Fetch next question (optional — saves a round trip for the frontend)
        next_q_out: Optional[VisualQuestionOut] = None
        if not session.is_completed:
            next_visual = await get_next_question(db, session.topic, session.level, session)
            if next_visual:
                if visual_question_needs_generation(next_visual, session.level):
                    _queue_visual_warmup(background_tasks, request, session.topic, session.level)
                else:
                    await _add_seen_id(db, session, str(next_visual.id))
                    next_q_out = _question_to_out(next_visual, session.level)

        logger.info(
            f"[VisualRoom] submit: q={body.question_id[:8]} "
            f"correct={is_correct} session_score={session.score}"
        )

        return SubmitVisualAnswerResponse(
            is_correct     = is_correct,
            correct_answer = visual_q.correct_answer,
            explanation    = visual_q.explanation or "No explanation available.",
            next_question  = next_q_out,
            current_level  = session.level,
        )


# ═══════════════════════════════════════════════════════════════════════
# GET /api/visual/hint
# ═══════════════════════════════════════════════════════════════════════

@visual_router.get("/hint", response_model=VisualHintResponse)
@limiter.limit("20/minute")
async def get_visual_hint(
    request:     Request,
    question_id: str = Query(..., description="Question UUID"),
    session_id:  str = Query(..., description="Active session ID"),
    current=Depends(get_current_user),
):
    """
    Generate a hint for the current question.

    IMPORTANT: This endpoint takes only question_id — it does NOT receive
    or reveal the correct answer. Hint is generated from question_text +
    paragraph context only.
    """
    user, _ = current
    await enforce_user_quota(request, user.id, "visual_hint", limit=120, window_seconds=3600)
    async for db in _get_db(request):
        llm = getattr(request.app.state, "llm_client", None)
        session = await get_visual_session(db, session_id)
        if session is None:
            raise HTTPException(404, f"Session {session_id} not found")
        _require_session_owner(session, current)

        result = await db.execute(
            select(VisualQuestion).where(VisualQuestion.id == uuid.UUID(question_id))
        )
        visual_q = result.scalar_one_or_none()
        if visual_q is None:
            raise HTTPException(404, f"Question {question_id} not found")
        if not visual_q.question_text:
            raise HTTPException(400, "Question not yet generated — call /next first")

        hint = await generate_visual_hint(
            question_text = visual_q.question_text,
            paragraph     = visual_q.paragraph or "",
            llm_client    = llm,
        )

        return VisualHintResponse(hint=hint or "Think carefully about what you see.")


# ═══════════════════════════════════════════════════════════════════════
# GET /api/visual/explanation
# ═══════════════════════════════════════════════════════════════════════

@visual_router.get("/explanation", response_model=VisualExplanationResponse)
@limiter.limit("40/minute")
async def get_visual_explanation(
    request:     Request,
    question_id: str = Query(..., description="Question UUID"),
    session_id:  str = Query(..., description="Active session ID"),
    current=Depends(get_current_user),
):
    """
    Fetch the stored explanation for a question.
    Only useful after the user has already submitted (explanation is revealed there too).
    """
    user, _ = current
    await enforce_user_quota(request, user.id, "visual_explanation", limit=180, window_seconds=3600)
    async for db in _get_db(request):
        session = await get_visual_session(db, session_id)
        if session is None:
            raise HTTPException(404, f"Session {session_id} not found")
        _require_session_owner(session, current)

        result = await db.execute(
            select(VisualQuestion).where(VisualQuestion.id == uuid.UUID(question_id))
        )
        visual_q = result.scalar_one_or_none()
        if visual_q is None:
            raise HTTPException(404, f"Question {question_id} not found")

        return VisualExplanationResponse(
            question_id = str(visual_q.id),
            explanation = visual_q.explanation or "No explanation available for this question.",
        )


# ═══════════════════════════════════════════════════════════════════════
# POST /api/visual/session/{session_id}/end
# ═══════════════════════════════════════════════════════════════════════

@visual_router.post("/session/{session_id}/end")
@limiter.limit("20/minute")
async def end_visual_session(session_id: str, request: Request, current=Depends(get_current_user)):
    """End a session early or finalize it. Returns final score."""
    async for db in _get_db(request):
        session = await get_visual_session(db, session_id)
        if session is None:
            raise HTTPException(404, f"Session {session_id} not found")
        _require_session_owner(session, current)

        _close_visual_session_if_needed(session)
        await db.commit()

        accuracy = round(session.score / max(session.current_index, 1) * 100, 1)
        return {
            "session_id":       str(session.id),
            "topic":            session.topic,
            "level":            session.level,
            "score":            session.score,
            "questions_seen":   session.current_index,
            "total_questions":  session.total_questions,
            "accuracy_percent": accuracy,
        }
