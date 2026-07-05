"""Background worker for pre-warming room question queues."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database.concept_models import Concept, QuestionConcept
from database.custom_models import Fact
from database.models import QuestionBank
from database.performance_models import QuestionGenerationEvent
from services.governance_service import GovernanceService
from services.llm import LLMClient
from services.concept_service import ConceptDiscoveryService
from services.question_generator_enhanced import generate_question_enhanced
from services.monitoring import get_monitoring
from services.question_queue import (
    RefillRequest,
    mark_provider_backoff,
    observe_queue_depth,
    pop_refill_request,
    prewarm_lease_key,
    provider_backoff_active,
    push_ready_question_id,
    request_refill,
)

logger = logging.getLogger(__name__)


def _topic_family(topic: str) -> str:
    label = str(topic or "").strip()
    if " - " in label:
        return label.split(" - ", 1)[0].strip().lower()
    return label.lower() or "mixed"


def _source_for_room(room: str) -> str:
    room_name = str(room or "").strip().lower()
    if room_name == "classic":
        return "classic_llm"
    if room_name == "challenge":
        return "challenge_llm"
    return "custom_llm_background"


def _is_low_quality_background_question(text: str) -> bool:
    lowered = str(text or "").lower()
    blocked = (
        "gdp", "gross domestic", "per capita", "income", "census", "population",
        "percentage", "percent", "how many", "how much", "approximate", "approximately",
        "usd", "dollars", "annual", "average", "growth rate", "unemployment",
    )
    return any(term in lowered for term in blocked)


def _build_context(room: str, refill: RefillRequest, fact_content: Optional[str]) -> str:
    topic = refill.topic
    if room == "classic":
        return (
            f"Generate a vetted classic-room question about {topic}. Keep it factual, concise, and educational. "
            "Avoid GDP, income, census, per-capita, population-count, percentage, and random numeric-stat trivia. "
            "Prefer causes, consequences, chronology, places, civilizations, maps, borders, rivers, mountains, and meaning. "
            "Use exactly 4 plausible options."
        )
    if room == "challenge":
        level = int(refill.difficulty_bucket or 1)
        if str(topic).lower() == "mixed":
            focus = "History or Geography only: civilizations, empires, capitals, landforms, rivers, maps, treaties, chronology"
        else:
            focus = str(topic)
        if level == 1:
            level_rule = "Level 1: very easy recall; it will be displayed with exactly 2 options."
        elif level == 5:
            level_rule = "Level 5: very hard; learner will type the answer, so use a precise answer key and no dependence on multiple-choice clues."
        else:
            level_rule = "Levels 2-4: use 4 plausible options, harder and more specific as the level increases."
        return (
            f"Generate a challenge-room question about {focus}. "
            f"Difficulty level {level}/5. {level_rule} "
            "Avoid GDP, population, income, census, percentage, and random numeric-stat trivia. "
            "Prefer historical/geographic meaning, causes, locations, capitals, rivers, mountains, borders, or chronology."
        )
    if fact_content:
        return f"Topic: {topic}\nFact: {fact_content}\nGenerate a precise study question from this fact."
    return f"Generate a custom-room question about {topic}. Use a concrete factual anchor."


@dataclass(slots=True)
class GeneratedQuestion:
    question_id: uuid.UUID
    question_text: str
    options: list[str]
    correct_answer: str
    explanation: str
    source: str


class QuestionGenerationWorker:
    """Continuously refills Redis ready queues with persisted question IDs."""

    def __init__(
        self,
        *,
        db_factory: async_sessionmaker[AsyncSession],
        redis_client,
        llm_client: Optional[LLMClient],
        http_client=None,
    ) -> None:
        self._db_factory = db_factory
        self._redis = redis_client
        self._llm = llm_client
        self._http_client = http_client

    async def run_forever(self) -> None:
        logger.info("Question generation worker started")
        while True:
            refill = await pop_refill_request(self._redis, timeout_seconds=5)
            if refill is None:
                await asyncio.sleep(0.25)
                continue
            try:
                await self.process_refill_request(refill)
            except Exception:
                logger.exception("Question refill processing failed for %s", refill.queue_key)

    async def process_refill_request(self, refill: RefillRequest) -> None:
        if self._redis is None:
            return
        lease_key = prewarm_lease_key(refill.queue_key)
        try:
            claimed = await self._redis.set(lease_key, "1", ex=900, nx=True)
        except Exception:
            claimed = False
        if not claimed:
            return

        try:
            current_depth = await observe_queue_depth(self._redis, refill.queue_key, room=refill.room)
            target_depth = max(1, int(refill.min_depth))
            if current_depth >= target_depth:
                return
            # Generate only enough to reach the target. Previously batch_size +
            # min_depth could overfill queues and spam the provider. With challenge
            # batch_size=20/min_depth=20 this creates 20, not 40.
            needed = min(max(1, int(refill.batch_size)), max(1, target_depth - current_depth))
            pushed_count = 0
            for _ in range(needed):
                generated = await self._generate_one(refill)
                if generated is None:
                    break
                pushed = await push_ready_question_id(self._redis, refill.queue_key, str(generated.question_id))
                if pushed:
                    pushed_count += 1
                    await observe_queue_depth(self._redis, refill.queue_key, room=refill.room)
                # Serial throttle: one worker, small pause. This lets Groq's token
                # bucket recover and avoids the prewarmer starving live gameplay.
                await asyncio.sleep(float(refill.metadata.get("sleep_seconds", 0.35)))

            final_depth = await observe_queue_depth(self._redis, refill.queue_key, room=refill.room)
            if pushed_count > 0 and final_depth < target_depth and refill.metadata.get("auto_continue", True):
                await request_refill(self._redis, refill, force=False)
        finally:
            try:
                await self._redis.delete(lease_key)
            except Exception:
                pass

    async def _generate_one(self, refill: RefillRequest) -> Optional[GeneratedQuestion]:
        provider = "groq"
        start = time.perf_counter()
        if self._llm is None:
            async with self._db_factory() as db:
                await self._record_event(
                    db,
                    refill,
                    provider=provider,
                    provider_status=503,
                    generation_ms=0.0,
                    accepted=False,
                    rejection_reason="llm_unavailable",
                )
            return None

        if await provider_backoff_active(self._redis, provider):
            async with self._db_factory() as db:
                await self._record_event(
                    db,
                    refill,
                    provider=provider,
                    provider_status=429,
                    generation_ms=0.0,
                    accepted=False,
                    rejection_reason="provider_backoff_active",
                )
            return None

        async with self._db_factory() as db:
            fact_content = await self._fact_content_for_refill(db, refill)
            payload = None
            # Classic and Challenge should use the same RAG/enhanced quality path
            # as live generation whenever possible. Direct LLM is only a fallback.
            if refill.room in {"classic", "challenge"} and self._http_client is not None:
                try:
                    payload = await generate_question_enhanced(
                        topic=refill.topic,
                        difficulty=max(1, min(5, int(refill.difficulty_bucket or 1))),
                        llm_client=self._llm,
                        http_client=self._http_client,
                        db_session=db,
                        user_accuracy=0.5,
                    )
                except Exception:
                    logger.exception("Enhanced background generation failed for %s", refill.queue_key)
                    payload = None

            if payload is None:
                payload = await self._llm.generate_mcq(
                    context=_build_context(refill.room, refill, fact_content),
                    topic=refill.topic,
                    difficulty=int(refill.difficulty_bucket),
                    strategy="topic_focus",
                    user_accuracy=0.5,
                )

            generation_ms = (time.perf_counter() - start) * 1000
            provider_status = int(getattr(self._llm, "last_status_code", 200) or 200)

            if provider_status == 429:
                get_monitoring().record_provider_status(provider, provider_status)
                await mark_provider_backoff(self._redis, provider)

            if not payload:
                await self._record_event(
                    db,
                    refill,
                    provider=provider,
                    provider_status=provider_status,
                    generation_ms=generation_ms,
                    accepted=False,
                    rejection_reason="empty_payload",
                )
                return None

            generated = await self._persist_generated_question(
                db,
                refill,
                payload=payload,
                provider=provider,
                provider_status=provider_status,
                generation_ms=generation_ms,
            )
            return generated

    async def _fact_content_for_refill(self, db: AsyncSession, refill: RefillRequest) -> Optional[str]:
        if refill.room != "custom":
            return None
        if refill.fact_id:
            try:
                fact = await db.get(Fact, uuid.UUID(str(refill.fact_id)))
            except Exception:
                fact = None
            if fact is not None:
                return str(fact.content or "").strip() or None

        stmt = (
            select(Fact)
            .where(Fact.topic == refill.topic)
            .order_by(Fact.total_questions_generated.asc(), Fact.id.asc())
            .limit(1)
        )
        fact = (await db.execute(stmt)).scalars().first()
        return str(fact.content or "").strip() if fact is not None and str(fact.content or "").strip() else None

    async def _persist_generated_question(
        self,
        db: AsyncSession,
        refill: RefillRequest,
        *,
        payload: dict[str, Any],
        provider: str,
        provider_status: int,
        generation_ms: float,
    ) -> Optional[GeneratedQuestion]:
        question_text = str(payload.get("text", "")).strip()
        explanation = str(payload.get("explanation", "")).strip() or "Review the question carefully before answering."
        options = [str(value).strip() for value in payload.get("options", []) if str(value).strip()]
        correct_answer = str(payload.get("correctAnswer", payload.get("correct", ""))).strip() or (options[0] if options else "")

        if refill.room == "challenge":
            level = max(1, min(5, int(refill.difficulty_bucket or 1)))
            if correct_answer and all(opt.lower() != correct_answer.lower() for opt in options):
                options.insert(0, correct_answer)
            unique_options: list[str] = []
            seen_options: set[str] = set()
            for opt in options:
                key = opt.lower()
                if key and key not in seen_options:
                    seen_options.add(key)
                    unique_options.append(opt)
            wrongs = [opt for opt in unique_options if correct_answer and opt.lower() != correct_answer.lower()]
            pads = ["None of the above", "Cannot be determined", "All of the above", "Unknown"]
            if level == 5:
                # Persist a minimal pair for DB compatibility; the API strips options
                # when serving level 5 typed-answer questions.
                options = [correct_answer, wrongs[0] if wrongs else "Cannot be determined"] if correct_answer else unique_options[:2]
            elif level == 1:
                while len(wrongs) < 1:
                    wrongs.append(pads.pop(0))
                options = [correct_answer, wrongs[0]] if correct_answer else unique_options[:2]
            else:
                while len(wrongs) < 3:
                    wrongs.append(pads.pop(0) if pads else f"Option {len(wrongs) + 2}")
                options = [correct_answer] + wrongs[:3] if correct_answer else unique_options[:4]

        if refill.room in {"classic", "challenge"} and _is_low_quality_background_question(question_text):
            await self._record_event(
                db,
                refill,
                provider=provider,
                provider_status=provider_status,
                generation_ms=generation_ms,
                accepted=False,
                rejection_reason="low_quality_numeric_stat_question",
            )
            return None

        if not question_text or len(options) < 2 or not correct_answer:
            await self._record_event(
                db,
                refill,
                provider=provider,
                provider_status=provider_status,
                generation_ms=generation_ms,
                accepted=False,
                rejection_reason="invalid_payload",
            )
            return None

        normalized_topic = refill.topic if refill.room != "custom" else (refill.topic_family or _topic_family(refill.topic))
        existing_stmt = (
            select(QuestionBank)
            .where(func.lower(QuestionBank.question_text) == question_text.lower())
            .where(func.lower(QuestionBank.topic) == normalized_topic.lower())
            .limit(1)
        )
        existing = (await db.execute(existing_stmt)).scalars().first()
        if existing is not None:
            await self._record_event(
                db,
                refill,
                provider=provider,
                provider_status=provider_status,
                generation_ms=generation_ms,
                accepted=True,
                rejection_reason="reused_existing_question",
            )
            return GeneratedQuestion(
                question_id=existing.id,
                question_text=existing.question_text,
                options=json.loads(existing.options_json or "[]"),
                correct_answer=existing.correct_answer,
                explanation=existing.explanation or "",
                source=str(existing.source or _source_for_room(refill.room)),
            )

        decision = await GovernanceService.evaluate_candidate(
            db,
            question_id=None,
            room=refill.room,
            action="persist",
            topic=refill.topic,
            question_text=question_text,
            correct_answer=correct_answer,
            explanation=explanation,
            options=options,
        )
        if decision is not None and not decision.approved:
            await self._record_event(
                db,
                refill,
                provider=provider,
                provider_status=provider_status,
                generation_ms=generation_ms,
                accepted=False,
                rejection_reason="governance_rejected",
            )
            return None

        question_id = uuid.uuid4()
        row = QuestionBank(
            id=question_id,
            question_text=question_text,
            correct_answer=correct_answer,
            options_json=json.dumps(options),
            explanation=explanation,
            difficulty_irt=float(refill.difficulty_bucket),
            topic=normalized_topic,
            source=_source_for_room(refill.room),
            usage_count=0,
            times_seen=0,
        )
        if decision is not None:
            await GovernanceService.apply_decision_to_persisted_row(db, row=row, decision=decision)
        db.add(row)

        if refill.room == "classic":
            try:
                concept_topic = "mixed" if str(normalized_topic).lower() in {"mix", "mixed"} else str(normalized_topic).lower()
                inferred = await ConceptDiscoveryService.ensure_question_has_concept(
                    db=db,
                    question_text=question_text,
                    correct_answer=correct_answer,
                    topic=concept_topic,
                    explanation=explanation,
                    topic_label=refill.topic,
                )
                row.primary_concept_id = inferred.id
                db.add(QuestionConcept(question_id=question_id, concept_id=inferred.id, is_primary=True))
            except Exception:
                logger.debug("Classic background concept discovery skipped", exc_info=True)

        if refill.room == "custom" and refill.concept_id:
            try:
                concept_uuid = uuid.UUID(str(refill.concept_id))
            except Exception:
                concept_uuid = None
            if concept_uuid is not None:
                concept_row = await db.get(Concept, concept_uuid)
                if concept_row is not None:
                    db.add(
                        QuestionConcept(
                            question_id=question_id,
                            concept_id=concept_row.id,
                            is_primary=True,
                        )
                    )
        if refill.room == "custom" and refill.fact_id:
            try:
                fact_uuid = uuid.UUID(str(refill.fact_id))
            except Exception:
                fact_uuid = None
            if fact_uuid is not None:
                fact_row = await db.get(Fact, fact_uuid)
                if fact_row is not None:
                    fact_row.total_questions_generated = int(fact_row.total_questions_generated or 0) + 1

        await self._record_event(
            db,
            refill,
            provider=provider,
            provider_status=provider_status,
            generation_ms=generation_ms,
            accepted=True,
            rejection_reason=None,
        )
        await db.commit()
        get_monitoring().record_generation_attempt(
            refill.room,
            provider=provider,
            provider_status=provider_status,
            generation_ms=generation_ms,
            accepted=True,
        )
        return GeneratedQuestion(
            question_id=question_id,
            question_text=question_text,
            options=options,
            correct_answer=correct_answer,
            explanation=explanation,
            source=_source_for_room(refill.room),
        )

    async def _record_event(
        self,
        db: AsyncSession,
        refill: RefillRequest,
        *,
        provider: str,
        provider_status: int,
        generation_ms: float,
        accepted: bool,
        rejection_reason: Optional[str],
    ) -> None:
        db.add(
            QuestionGenerationEvent(
                room=refill.room,
                queue_key=refill.queue_key,
                topic=refill.topic,
                concept_id=_safe_uuid(refill.concept_id),
                fact_id=_safe_uuid(refill.fact_id),
                provider=provider,
                provider_status=provider_status,
                generation_ms=float(generation_ms),
                accepted=bool(accepted),
                rejection_reason=rejection_reason,
            )
        )
        await db.commit()
        get_monitoring().record_generation_attempt(
            refill.room,
            provider=provider,
            provider_status=provider_status,
            generation_ms=generation_ms,
            accepted=accepted,
        )
        if provider_status == 429:
            get_monitoring().record_provider_status(provider, provider_status)


def _safe_uuid(value: Optional[str]) -> Optional[uuid.UUID]:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except Exception:
        return None
