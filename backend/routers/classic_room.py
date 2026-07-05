"""
routers/classic_room.py — Classic (Training) Room API router.

The Classic Room is the core learning environment. It uses IRT (Item Response Theory)
to adaptively select questions at the right difficulty for each user.

Endpoints:
  - POST /api/rooms/classic/questions  → Generate next adaptive question
  - POST /api/rooms/classic/hints      → Get a study hint (LLM-generated)
  - POST /api/rooms/classic/answers    → Submit answer, get feedback + next question

Session Flow:
  1. Client POSTs to /questions with topic (no session_id) → starts new session
  2. Server returns question with shuffled options (correct answer hidden)
  3. Client POSTs to /answers with selected_answer → server reveals correct + explanation
  4. Server returns next question in the response (or null if session complete)
  5. Client can POST to /hints during step 2 for a study hint

Internal helper groups:
    - Topic normalization and context shaping for LLM fallback generation
    - Session/current-question state adapters via SessionService
    - Governance-aware fallback logic when concept-targeted selection is exhausted
"""

import uuid
import logging
import json
import random
from datetime import datetime, timezone
from typing import Optional, Annotated

import structlog
from fastapi import APIRouter, HTTPException, Depends, Body, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from schemas.classic import (
    QuestionRequest,
    QuestionResponse,
    HintRequest,
    HintResponse,
    SubmitAnswerRequest,
    SubmitAnswerResponse,
)
from services.llm import LLMClient
from services.session import SessionService
from services.classic_service import ClassicService
from services.concept_service import ConceptDiscoveryService
from services.question_sources import NON_CLASSIC_SOURCE_PREFIXES, NON_CLASSIC_SOURCE_VALUES
from services.rate_limits import enforce_user_quota
from services.question_queue import (
    classic_ready_queue_key,
    pop_ready_question_id,
    RefillRequest,
    request_refill,
)
from config import (
    CLASSIC_PREGEN_BATCH_SIZE,
    CLASSIC_PREGEN_TARGET_PER_BUCKET,
    CLASSIC_PREGEN_TOPUP_THRESHOLD,
    ENABLE_NO_INLINE_LLM,
)
from dependencies import limiter
from routers.auth import get_current_user, get_db
from database.models import QuestionBank
from database.concept_models import QuestionConcept

logger = structlog.get_logger(__name__)
classic_router = APIRouter(prefix="/api/rooms/classic", tags=["Classic Room"])


def _classic_bank_source_filter():
    """Exclude Challenge and Custom generated bank rows from Classic fallback pools."""
    source_expr = func.lower(func.coalesce(QuestionBank.source, ""))
    filters = [source_expr.notin_(list(NON_CLASSIC_SOURCE_VALUES))]
    filters.extend(~source_expr.like(f"{prefix}_%") for prefix in NON_CLASSIC_SOURCE_PREFIXES)
    return filters


# ═══════════════════════════════════════════════════════════════════════════
# DEPENDENCY PROVIDERS
# ═══════════════════════════════════════════════════════════════════════════


# Load SessionService from app state for classic session persistence.
async def get_session_svc(request: Request) -> SessionService:
    """Get the SessionService instance from app.state.

    SessionService manages quiz session data in Redis (with in-memory fallback).
    Injected during lifespan startup in main.py.
    """
    return getattr(request.app.state, "session_service", None)


# Canonicalize incoming topic labels to supported classic buckets.
def _normalize_classic_topic(topic: str) -> str:
    normalized = (topic or "mix").strip().lower()
    if normalized in {"history", "geography", "mix"}:
        return normalized
    if "history" in normalized:
        return "history"
    if "geo" in normalized:
        return "geography"
    return "mix"


# Build a short topic-specific context hint for fallback LLM generation.
def _classic_context_hint(topic: str) -> str:
    normalized = _normalize_classic_topic(topic)
    if normalized == "history":
        return "Key historical events, causes, consequences, major civilizations, and timeline reasoning."
    if normalized == "geography":
        return "Countries, capitals, physical geography, regions, borders, and global location reasoning."
    return "Balanced history and geography knowledge with fact-based, non-trivial reasoning."


def _classic_pregen_difficulties(active_difficulty: int | float | None) -> list[int]:
    try:
        active = max(1, min(5, int(round(float(active_difficulty or 3)))))
    except Exception:
        active = 3
    priority = [active, max(1, active - 1), min(5, active + 1), 1, 2, 3, 4, 5]
    result: list[int] = []
    for item in priority:
        if item not in result:
            result.append(item)
    return result


async def _request_classic_pregen(
    redis_client,
    *,
    topic: str,
    difficulty: int | float | None,
    session_id: str | None = None,
    force: bool = False,
) -> None:
    if redis_client is None:
        return
    try:
        difficulty_bucket = max(1, min(5, int(round(float(difficulty or 3)))))
        normalized_topic = _normalize_classic_topic(topic)
        queue_key = classic_ready_queue_key(normalized_topic, None, difficulty_bucket)
        target_depth = max(1, int(CLASSIC_PREGEN_TARGET_PER_BUCKET))
        topup_threshold = max(1, min(target_depth, int(CLASSIC_PREGEN_TOPUP_THRESHOLD)))
        try:
            current_depth = int(await redis_client.llen(queue_key))
        except Exception:
            current_depth = 0
        if force and current_depth >= target_depth:
            return
        if not force and current_depth >= topup_threshold:
            return
        await request_refill(
            redis_client,
            RefillRequest(
                room="classic",
                queue_key=queue_key,
                topic=normalized_topic,
                difficulty_bucket=difficulty_bucket,
                batch_size=max(1, int(CLASSIC_PREGEN_BATCH_SIZE)),
                min_depth=target_depth,
                metadata={
                    "session_id": str(session_id or ""),
                    "prewarm": True,
                    "auto_continue": True,
                    "target_per_bucket": target_depth,
                    "topup_threshold": topup_threshold,
                    # Classic background fill should be quieter than challenge so
                    # live gameplay keeps priority when Groq TPM is tight.
                    "sleep_seconds": 0.75,
                },
            ),
            force=force,
        )
    except Exception as exc:
        logger.debug("classic pregen request skipped: %s", exc)


def _question_response_from_bank_row(row: QuestionBank, options: list[str], session_id: uuid.UUID) -> QuestionResponse:
    return QuestionResponse(
        id=row.id,
        text=row.question_text,
        options=options,
        correctAnswer=None,
        explanation=row.explanation,
        session_id=session_id,
    )


# Keep API compatibility: response exposes a single theta delta, while the
# service may return per-concept theta change objects.
def _resolve_theta_change(result: dict) -> Optional[float]:
    explicit_change = result.get("theta_change")
    if isinstance(explicit_change, (int, float)):
        return float(explicit_change)

    raw_changes = result.get("theta_changes")
    if not isinstance(raw_changes, list):
        return None

    deltas: list[float] = []
    for item in raw_changes:
        if not isinstance(item, dict):
            continue
        before = item.get("theta_before")
        after = item.get("theta_after")
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            deltas.append(float(after) - float(before))

    if not deltas:
        return None
    return sum(deltas) / float(len(deltas))


# Generate and persist a governance-checked fallback question via LLM.
async def _generate_classic_question_from_llm(
    request: Request,
    db: AsyncSession,
    topic: str,
    difficulty: int,
) -> Optional[dict]:
    llm_client = getattr(request.app.state, "llm_client", None)
    if not llm_client:
        return None

    normalized_topic = _normalize_classic_topic(topic)
    http_client = getattr(request.app.state, "http_client", None)
    try:
        from services.question_generator_enhanced import generate_question_enhanced
        data = await generate_question_enhanced(
            topic=normalized_topic,
            difficulty=max(1, min(5, int(difficulty))),
            llm_client=llm_client,
            http_client=http_client,
            db_session=db,
            user_accuracy=0.5,
        )
    except Exception as exc:
        logger.warning("Classic LLM generation failed", topic=normalized_topic, error=str(exc))
        return None

    if not data:
        return None

    question_text = str(data.get("text", "")).strip()
    correct_answer = str(data.get("correctAnswer", data.get("correct", ""))).strip()
    explanation = str(data.get("explanation", "")).strip() or (
        "Review each option against the core concept and eliminate distractors step by step."
    )
    raw_options = data.get("options", [])

    if not question_text or not correct_answer or not isinstance(raw_options, list):
        return None

    deduped_options = []
    seen = set()
    for opt in [str(o).strip() for o in raw_options if str(o).strip()]:
        key = opt.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped_options.append(opt)

    if correct_answer.lower() not in seen:
        deduped_options.append(correct_answer)

    while len(deduped_options) < 4:
        deduped_options.append(f"Option {len(deduped_options) + 1}")
    deduped_options = deduped_options[:4]
    random.shuffle(deduped_options)

    question_id = uuid.uuid4()

    decision = None
    try:
        from services.governance_service import GovernanceService

        decision = await GovernanceService.evaluate_candidate(
            db,
            question_id=question_id,
            room="classic",
            action="persist",
            topic=normalized_topic,
            question_text=question_text,
            correct_answer=correct_answer,
            explanation=explanation,
            options=deduped_options,
        )
        if decision is not None and not decision.approved:
            logger.warning(
                "Classic governance rejected LLM fallback question",
                topic=normalized_topic,
                reasons=list(decision.reasons or []),
            )
            return None
    except Exception as exc:
        # Governance must never break question generation.
        logger.warning("Classic governance evaluation failed", error=str(exc))

    try:
        row = QuestionBank(
            id=question_id,
            question_text=question_text,
            correct_answer=correct_answer,
            options_json=json.dumps(deduped_options),
            explanation=explanation,
            topic=normalized_topic,
            difficulty_irt=float(max(1, min(5, int(difficulty)))),
            source="classic_llm",
        )

        if decision is not None:
            try:
                from services.governance_service import GovernanceService

                await GovernanceService.apply_decision_to_persisted_row(db, row=row, decision=decision)
            except Exception as exc:
                logger.warning("Classic governance persistence hook failed", error=str(exc))

        db.add(row)
        await db.flush()

        concept_topic = "mixed" if normalized_topic == "mix" else normalized_topic
        inferred_concept = await ConceptDiscoveryService.ensure_question_has_concept(
            db=db,
            question_text=question_text,
            correct_answer=correct_answer,
            topic=concept_topic,
            explanation=explanation,
            topic_label=topic,
        )
        row.primary_concept_id = inferred_concept.id

        db.add(
            QuestionConcept(
                question_id=question_id,
                concept_id=inferred_concept.id,
                is_primary=True,
            )
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.warning("Failed to persist classic LLM fallback question", error=str(exc))
        return None

    return {
        "id": str(question_id),
        "text": question_text,
        "options": deduped_options,
        "correct_index": deduped_options.index(correct_answer),
        "correct_answer": correct_answer,
        "topic": normalized_topic,
        "difficulty": max(1, min(5, int(difficulty))),
        "explanation": explanation,
    }


# ═══════════════════════════════════════════════════════════════════════════
# POST /questions — Generate adaptive question
# ═══════════════════════════════════════════════════════════════════════════


@classic_router.post("/questions", response_model=QuestionResponse)
@limiter.limit("40/minute")
# Serve the next classic question, creating a session when needed.
async def generate_question(
    request: Request,
    body: Annotated[QuestionRequest, Body()],
    current_user_tuple=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    session_svc: SessionService = Depends(get_session_svc),
):
    """Generate an adaptive MCQ question using IRT-based difficulty targeting.

    Two modes:
      - With session_id: get next question in existing session
      - Without session_id: start new session, return first question

    The correct answer is hidden in the response (revealed on /answers submission).
    Options are shuffled server-side for anti-cheat.
    """
    try:
        current_user, issued_at = current_user_tuple
        await enforce_user_quota(request, current_user.id, "classic_question", limit=240, window_seconds=3600)
        logger.info("Question requested", user=str(current_user.id)[:8], topic=body.topic)

        if not body.topic:
            raise HTTPException(status_code=400, detail="Invalid topic")

        redis_client = getattr(request.app.state, "redis", None)
        # Background preparation: keep every classic difficulty bucket warm, but
        # do it via the serial worker so it never blocks the first question.
        if not body.session_id:
            for difficulty_to_warm in _classic_pregen_difficulties(body.difficulty):
                await _request_classic_pregen(
                    redis_client,
                    topic=body.topic,
                    difficulty=difficulty_to_warm,
                    force=True,
                )
        else:
            await _request_classic_pregen(
                redis_client,
                topic=body.topic,
                difficulty=body.difficulty,
                session_id=str(body.session_id),
                force=False,
            )

        session_id = body.session_id
        question = None
        session_state = None

        if body.session_id:
            # ── Continue existing session ──
            session_state = await session_svc.get_session_state(str(body.session_id))
            if not session_state:
                raise HTTPException(status_code=404, detail="Session not found")
            if str(session_state.get("user_id", "")) != str(current_user.id):
                raise HTTPException(status_code=403, detail="You are not allowed to access this session")
            if bool(session_state.get("is_finished")) or len(session_state.get("questions_asked", [])) >= ClassicService.MAX_QUESTIONS_PER_SESSION:
                logger.info(
                    "Rejected /questions for finished classic session",
                    user=str(current_user.id)[:8],
                    session=str(body.session_id)[:8],
                    asked=len(session_state.get("questions_asked", [])),
                )
                raise HTTPException(status_code=400, detail="Session already completed")

            asked_ids = list(session_state.get("questions_asked", []))
            current_q = await session_svc.get_current_question(str(body.session_id))
            if current_q and current_q.get("id"):
                current_qid = str(current_q.get("id"))
                if current_qid not in asked_ids:
                    asked_ids.append(current_qid)

            question = await ClassicService.select_next_question(
                db,
                current_user.id,
                session_state["topic"],
                concept_ids=[uuid.UUID(cid) for cid in session_state.get("concept_ids", [])],
                asked_question_ids=asked_ids,
                theta_snapshot=session_state.get("theta_snapshot", {}),
                redis_client=getattr(request.app.state, "redis", None),
            )

            if question:
                # Store current question in session for answer verification
                await session_svc.set_current_question(
                    str(body.session_id),
                    {
                        "id": question["id"],
                        "correct_answer": question["correct_answer"],
                        "shuffled_options": question["options"],
                        "correct_index_shuffled": question["correct_index"],
                        "question_sent_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
        else:
            # ── Start new session ──
            result = await ClassicService.start_session(
                db, current_user.id, body.topic, session_svc
            )
            session_id = uuid.UUID(result["session_id"])
            question = result["first_question"]

        if not question:
            # Try the pre-generated queue before doing a live LLM call. This keeps
            # Classic responsive while the worker slowly discovers more concepts.
            redis_client = getattr(request.app.state, "redis", None)
            queue_key = classic_ready_queue_key(_normalize_classic_topic(body.topic), None, body.difficulty)
            queued_seen_ids = await ClassicService.get_user_seen_question_ids(
                db=db,
                user_id=current_user.id,
                topic=body.topic if body.topic else "mix",
                asked_question_ids=(session_state.get("questions_asked", []) if body.session_id and session_state else []),
                redis_client=redis_client,
            )
            for _ in range(8):
                queued_id = await pop_ready_question_id(redis_client, queue_key)
                if not queued_id:
                    break
                try:
                    queued_row = await db.get(QuestionBank, uuid.UUID(str(queued_id)))
                except Exception:
                    queued_row = None
                if queued_row is None:
                    continue
                if queued_row.id in queued_seen_ids:
                    continue
                try:
                    options = json.loads(queued_row.options_json or "[]")
                except Exception:
                    options = []
                correct_answer = str(queued_row.correct_answer or "").strip()
                if len(options) < 2 or correct_answer not in options:
                    continue
                random.shuffle(options)
                await session_svc.set_current_question(
                    str(session_id),
                    {
                        "id": str(queued_row.id),
                        "correct_answer": queued_row.correct_answer,
                        "shuffled_options": options,
                        "correct_index_shuffled": options.index(correct_answer),
                        "question_sent_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                await _request_classic_pregen(
                    redis_client,
                    topic=body.topic,
                    difficulty=body.difficulty,
                    session_id=str(session_id),
                    force=False,
                )
                return _question_response_from_bank_row(queued_row, options, session_id)

            # Inline LLM generation is the slowest path (multi-second). Under
            # ENABLE_NO_INLINE_LLM we never block the request on it: enqueue a
            # background refill and fall through to serve a real DB question.
            # See QUALITY_PERF_ROADMAP_2026-07-04.md item 4.
            if ENABLE_NO_INLINE_LLM:
                await _request_classic_pregen(
                    redis_client,
                    topic=body.topic,
                    difficulty=body.difficulty,
                    session_id=str(session_id),
                    force=True,
                )
                generated_q = None
            else:
                generated_q = await _generate_classic_question_from_llm(
                    request=request,
                    db=db,
                    topic=body.topic,
                    difficulty=body.difficulty,
                )

            if generated_q:
                await session_svc.set_current_question(
                    str(session_id),
                    {
                        "id": generated_q["id"],
                        "correct_answer": generated_q["correct_answer"],
                        "shuffled_options": generated_q["options"],
                        "correct_index_shuffled": generated_q["correct_index"],
                        "question_sent_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                logger.warning(
                    "Concept-targeted selection empty; served LLM classic fallback",
                    user=str(current_user.id)[:8],
                    topic=body.topic,
                )
                return QuestionResponse(
                    id=uuid.UUID(generated_q["id"]),
                    text=generated_q["text"],
                    options=generated_q["options"],
                    correctAnswer=None,
                    explanation=generated_q.get("explanation"),
                    session_id=session_id,
                )

            # Safety fallback: serve a real random DB question so session can continue.
            fallback_seen_ids = await ClassicService.get_user_seen_question_ids(
                db=db,
                user_id=current_user.id,
                topic=body.topic if body.topic else "mix",
                asked_question_ids=(session_state.get("questions_asked", []) if body.session_id and session_state else []),
                redis_client=redis_client,
            )

            governance_enabled = False
            try:
                from services.governance_service import GovernanceService

                governance_enabled = GovernanceService.enabled()
            except Exception:
                governance_enabled = False

            candidate_limit = 20 if governance_enabled else 1

            stmt = select(QuestionBank).where(*_classic_bank_source_filter())
            if governance_enabled:
                stmt = stmt.where(QuestionBank.gov_approved == True)  # noqa: E712
                stmt = stmt.where(QuestionBank.gov_safe == True)  # noqa: E712
            if fallback_seen_ids:
                stmt = stmt.where(QuestionBank.id.notin_(list(fallback_seen_ids)))
            if body.topic and body.topic.lower() != "mix":
                stmt = stmt.where(func.lower(QuestionBank.topic) == body.topic.lower())
            stmt = stmt.order_by(func.random()).limit(candidate_limit)
            fallback_result = await db.execute(stmt)
            fallback_candidates = fallback_result.scalars().all()

            fallback_q = None
            for candidate in fallback_candidates:
                if governance_enabled:
                    try:
                        decision = await GovernanceService.evaluate_bank_row_for_serving(
                            db,
                            row=candidate,
                            room="classic",
                            topic=body.topic if body.topic else "mix",
                        )
                        if not decision.approved:
                            continue
                    except Exception:
                        pass
                fallback_q = candidate
                break

            if not fallback_q:
                any_stmt = (
                    select(QuestionBank)
                    .where(*_classic_bank_source_filter())
                )
                if governance_enabled:
                    any_stmt = any_stmt.where(QuestionBank.gov_approved == True)  # noqa: E712
                    any_stmt = any_stmt.where(QuestionBank.gov_safe == True)  # noqa: E712
                if fallback_seen_ids:
                    any_stmt = any_stmt.where(QuestionBank.id.notin_(list(fallback_seen_ids)))
                any_result = await db.execute(any_stmt.order_by(func.random()).limit(candidate_limit))
                any_candidates = any_result.scalars().all()
                for candidate in any_candidates:
                    if governance_enabled:
                        try:
                            decision = await GovernanceService.evaluate_bank_row_for_serving(
                                db,
                                row=candidate,
                                room="classic",
                                topic=body.topic if body.topic else "mix",
                            )
                            if not decision.approved:
                                continue
                        except Exception:
                            pass
                    fallback_q = candidate
                    break

            if fallback_q:
                options = json.loads(fallback_q.options_json)
                random.shuffle(options)
                correct_index = options.index(fallback_q.correct_answer)
                await session_svc.set_current_question(
                    str(session_id),
                    {
                        "id": str(fallback_q.id),
                        "correct_answer": fallback_q.correct_answer,
                        "shuffled_options": options,
                        "correct_index_shuffled": correct_index,
                        "question_sent_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                logger.warning(
                    "Concept-targeted selection empty; served DB fallback question",
                    user=str(current_user.id)[:8],
                    topic=body.topic,
                )
                return QuestionResponse(
                    id=fallback_q.id,
                    text=fallback_q.question_text,
                    options=options,
                    correctAnswer=None,
                    explanation=fallback_q.explanation,
                    session_id=session_id,
                )

            # Last resort placeholder when DB has no questions at all.
            logger.warning("No questions available", user=str(current_user.id)[:8], topic=body.topic)
            return QuestionResponse(
                id=uuid.uuid4(),
                text="Quiz coming soon! Questions are being prepared for this topic.",
                options=["OK", "Got it", "Ready to learn", "Let's go"],
                correctAnswer=None,
                explanation="Questions are being generated. Please try again in a moment.",
                session_id=session_id,
            )

        logger.info("Question generated", question_id=question["id"][:8], topic=body.topic)
        return QuestionResponse(
            id=uuid.UUID(question["id"]),
            text=question["text"],
            options=question["options"],
            correctAnswer=None,  # Hidden until /answers submission
            explanation=question.get("explanation"),
            session_id=session_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to generate question", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate question")


# ═══════════════════════════════════════════════════════════════════════════
# POST /hints — Generate study hint
# ═══════════════════════════════════════════════════════════════════════════


@classic_router.post("/hints", response_model=HintResponse)
@limiter.limit("20/minute")
# Generate a non-revealing hint for the active question.
async def generate_hint(
    request: Request,
    body: Annotated[HintRequest, Body()],
    current_user_tuple=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a study hint for the current question via LLM.

    The hint guides the user toward the answer without revealing it directly.
    Falls back to a generic hint if the LLM service is unavailable.
    """
    try:
        current_user, issued_at = current_user_tuple
        await enforce_user_quota(request, current_user.id, "classic_hint", limit=120, window_seconds=3600)
        logger.info("Hint requested", user=str(current_user.id)[:8])

        if not body.question_text:
            raise HTTPException(status_code=400, detail="Missing question_text")

        # Get LLM client from app state
        llm_client = getattr(request.app.state, "llm_client", None)
        if not llm_client:
            raise HTTPException(status_code=503, detail="LLM service unavailable")

        # Generate hint via LLM. If correct answer is unavailable, use a neutral placeholder.
        question_row = await db.get(QuestionBank, body.question_id)
        resolved_question_text = body.question_text
        resolved_correct_answer = "UNKNOWN"
        if question_row is not None:
            resolved_question_text = question_row.question_text or body.question_text
            resolved_correct_answer = question_row.correct_answer or "UNKNOWN"

        hint_text = await llm_client.generate_hint(
            question_text=resolved_question_text,
            correct_answer=resolved_correct_answer,
        )

        logger.debug(
            "classic.generate_hint - LLM inputs: question_text=%s correct_answer=%s",
            resolved_question_text[:300],
            resolved_correct_answer[:120],
        )

        if not hint_text:
            # Fallback hint if LLM fails
            hint_text = (
                "Think carefully about the key concepts related to this question. "
                "Consider what you know about the topic and narrow down the possibilities."
            )

        # ── Governance: check hint text against active block rules ──
        try:
            from services.governance_service import GovernanceService

            if GovernanceService.enabled() and hint_text:
                topic = getattr(question_row, "topic", "") or ""
                decision = await GovernanceService.evaluate_candidate(
                    db,
                    question_id=getattr(body, "question_id", None),
                    room="classic",
                    action="hint",
                    topic=topic,
                    question_text=hint_text,
                    correct_answer="",
                    explanation="",
                    options=[],
                )
                if not decision.approved:
                    logger.info("Hint blocked by governance for question %s", str(getattr(body, "question_id", ""))[:8])
                    hint_text = (
                        "Think carefully about the key concepts related to this question. "
                        "Consider what you know about the topic and narrow down the possibilities."
                    )
        except Exception as exc:
            logger.warning("Hint governance check failed (serving hint anyway): %s", exc)

        logger.info("Hint generated", question_id=str(getattr(body, "question_id", ""))[:8])
        return HintResponse(hint=hint_text)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to generate hint", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate hint")


# ═══════════════════════════════════════════════════════════════════════════
# POST /answers — Submit answer + get feedback
# ═══════════════════════════════════════════════════════════════════════════


@classic_router.post("/answers", response_model=SubmitAnswerResponse)
@limiter.limit("80/minute")
# Validate an answer, update mastery/session state, and return feedback.
async def submit_answer(
    request: Request,
    body: Annotated[SubmitAnswerRequest, Body()],
    current_user_tuple=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    session_svc: SessionService = Depends(get_session_svc),
):
    """Submit an answer and receive feedback, difficulty adjustment, and next question.

    Server-side verification flow:
      1. Retrieve stored correct answer from session
      2. Map selected_answer to index (or use selected_index directly)
      3. Run IRT theta update + repeat-queue logic via ClassicService
      4. Return is_correct, correct_answer, explanation, next_question, session_stats
    """
    try:
        current_user, issued_at = current_user_tuple
        await enforce_user_quota(request, current_user.id, "classic_submit", limit=480, window_seconds=3600)
        logger.info("Answer submitted", user=str(current_user.id)[:8], session=str(body.session_id)[:8])

        if not body.session_id or not body.question_id:
            raise HTTPException(status_code=400, detail="Missing session_id or question_id")

        session_state = await session_svc.get_session_state(str(body.session_id))
        if not session_state:
            raise HTTPException(status_code=404, detail="Session not found")
        if str(session_state.get("user_id", "")) != str(current_user.id):
            raise HTTPException(status_code=403, detail="You are not allowed to access this session")

        # Retrieve the stored question for server-side verification
        current_question = await session_svc.get_current_question(str(body.session_id))
        if not current_question:
            raise HTTPException(status_code=404, detail="Current question not found in session")

        # Resolve selected_index from selected_answer text (if provided)
        selected_index = body.selected_index if body.selected_index is not None else -1
        if body.selected_answer is not None:
            try:
                options = current_question.get("shuffled_options", [])
                selected_index = options.index(body.selected_answer)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid answer selected")

        # Process answer: IRT theta update, repeat queue, concept mastery
        try:
            result = await ClassicService.process_answer(
                db,
                current_user.id,
                str(body.session_id),
                str(body.question_id),
                selected_index,
                body.time_taken,
                session_svc,
                body.used_hint,
            )
        except ValueError as exc:
            logger.warning(
                "Invalid classic answer submission",
                user=str(current_user.id)[:8],
                session=str(body.session_id)[:8],
                error=str(exc),
            )
            raise HTTPException(status_code=400, detail=str(exc))

        # BUG FIX: Previously returned explanation as correct_answer.
        # Now returns the actual correct answer text from the stored question.
        actual_correct_answer = current_question.get("correct_answer", "")

        # Keep the per-user seen-set cache warm/correct (no-op unless enabled).
        _app_state = getattr(getattr(request, "app", None), "state", None)
        await ClassicService.mark_seen_in_cache(
            getattr(_app_state, "redis", None),
            current_user.id,
            current_question.get("topic", "mix") or "mix",
            body.question_id,
        )

        logger.info(
            "Answer processed",
            correct=result["correct"],
            user=str(current_user.id)[:8],
        )

        return SubmitAnswerResponse(
            success=True,
            is_correct=result["correct"],
            correct_answer=actual_correct_answer,
            explanation=result.get("explanation", ""),
            new_difficulty=result.get("new_difficulty", 1),
            theta_updated=_resolve_theta_change(result),
            next_question=result.get("next_question"),
            session_stats=result.get("session_stats"),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to submit answer", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to submit answer")
