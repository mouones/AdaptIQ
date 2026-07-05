"""
services/classic_service.py

Classic Room Service - Question selection, concept management, IRT updates.

Key Features:
  - Concept selection (weighted by mastery_gap, recency, repeat_due)
  - Question selection using IRT ZPD targeting
  - Repeat queue management (25% wrong - repeat queue, 1% correct)
  - Session state management with locking
"""

import json
import random
import uuid
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, and_, func, or_, nullsfirst, update as sqlalchemy_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from config import (
    CLASSIC_QUESTIONS_PER_SESSION,
    POINTS_BASE_AWARD,
    POINTS_TIME_BONUS_DIVISOR,
    POINTS_HINT_PENALTY,
    POINTS_WRONG_PENALTY,
    ENABLE_IRT_LOGIT_SCALE,
    ENABLE_CANDIDATE_POOL_SAMPLING,
    CANDIDATE_POOL_SIZE,
    ENABLE_SEEN_SET_CACHE,
    SEEN_SET_TTL_SECONDS,
    compute_level,
)
from services.question_sources import NON_CLASSIC_SOURCE_PREFIXES, NON_CLASSIC_SOURCE_VALUES

from database.models import QuestionBank, UserResponse, User
from database.challenge_models import ChallengeAnswer, ChallengeSession
from database.pvp_models import PvPMatch, PvPMatchAnswer
from database.concept_models import (
    Concept,
    ClassicSession,
    QuestionConcept,
    UserConceptTheta,
    UserConceptRepeatQueue,
)
from database.irt import target_beta_range, beta_to_difficulty, difficulty_to_beta_continuous
from services.concept_irt import ConceptIRT
from services.session import SessionService
from services.governance_service import GovernanceService


logger = logging.getLogger(__name__)


class ClassicService:
    """Classic Room quiz service."""

    MAX_QUESTIONS_PER_SESSION = CLASSIC_QUESTIONS_PER_SESSION
    COLD_START_THRESHOLD = 5  # Responses below this = learning mode
    NON_CLASSIC_SOURCES = NON_CLASSIC_SOURCE_VALUES

    # Spaced repetition
    WRONG_ANSWER_REPEAT_PROBABILITY = 0.25
    CORRECT_ANSWER_REPEAT_PROBABILITY = 0.01
    REPEAT_DUE_SESSIONS = 7  # Show repeat after 7 more sessions

    @staticmethod
    def _classic_source_filter():
        """Return a SQLAlchemy filter excluding room-specific generated sources."""
        source_expr = func.lower(func.coalesce(QuestionBank.source, ""))
        filters = [source_expr.notin_(list(ClassicService.NON_CLASSIC_SOURCES))]
        filters.extend(~source_expr.like(f"{prefix}_%") for prefix in NON_CLASSIC_SOURCE_PREFIXES)
        return and_(*filters)

    @staticmethod
    def _compute_points_delta(
        *,
        correct: bool,
        time_taken_seconds: int,
        used_hint: bool,
    ) -> int:
        """Compute per-answer points using the same rules as ClassicRoom UI."""
        if correct:
            remaining_seconds = max(0, 30 - int(time_taken_seconds or 0))
            delta = int(POINTS_BASE_AWARD) + int(remaining_seconds // int(POINTS_TIME_BONUS_DIVISOR))
        else:
            delta = -int(POINTS_WRONG_PENALTY)

        if used_hint:
            delta -= int(POINTS_HINT_PENALTY)

        return int(delta)

    @staticmethod
    async def get_user_seen_question_ids(
        db: AsyncSession,
        user_id: uuid.UUID,
        topic: str,
        asked_question_ids: list[str] | None = None,
        extra_question_ids: list[str] | None = None,
        history_limit: int = 5000,
        redis_client=None,
    ) -> set[uuid.UUID]:
        """Return question IDs already seen by this user for the topic/session context.

        The historical portion (UserResponse history + Challenge + PvP answers) is
        a 3-join fan-out. Under ENABLE_SEEN_SET_CACHE, when a Redis client is
        provided, that portion is cached in a per-user/topic Redis set with a TTL
        (populate-on-miss), so repeated selections skip the joins. Session-local
        asked/extra ids are always merged in fresh. See roadmap item 3.
        """
        seen_ids: set[uuid.UUID] = set()

        for raw in (asked_question_ids or []) + (extra_question_ids or []):
            try:
                seen_ids.add(uuid.UUID(str(raw)))
            except ValueError:
                continue

        parent_topic = topic
        if " - " in topic:
            parent_topic = topic.split(" - ", 1)[0].strip()
        normalized_topic = (parent_topic or "mix").strip().lower()

        use_cache = ENABLE_SEEN_SET_CACHE and redis_client is not None
        cache_key = f"seen:{user_id}:{normalized_topic}"

        # Cache hit: use the persisted historical set and skip the DB joins.
        if use_cache:
            try:
                cached = await redis_client.smembers(cache_key)
            except Exception:
                cached = None
            if cached:
                for raw in cached:
                    try:
                        seen_ids.add(uuid.UUID(str(raw)))
                    except ValueError:
                        continue
                return seen_ids

        # Cache miss (or cache disabled): compute the historical set from the DB.
        db_ids: set[uuid.UUID] = set()

        history_stmt = (
            select(UserResponse.question_id)
            .where(UserResponse.user_id == user_id)
            .order_by(UserResponse.created_at.desc())
            .limit(history_limit)
        )
        if normalized_topic != "mix":
            history_stmt = history_stmt.where(func.lower(UserResponse.topic) == normalized_topic)

        history_ids = (await db.execute(history_stmt)).scalars().all()
        for qid in history_ids:
            try:
                db_ids.add(uuid.UUID(str(qid)))
            except ValueError:
                continue

        cross_topic = normalized_topic == "mix"

        challenge_stmt = (
            select(ChallengeAnswer.question_id)
            .join(ChallengeSession, ChallengeSession.id == ChallengeAnswer.session_id)
            .where(ChallengeSession.user_id == user_id)
        )
        if not cross_topic:
            challenge_stmt = challenge_stmt.where(func.lower(ChallengeSession.topic) == normalized_topic)

        challenge_ids = (await db.execute(challenge_stmt)).scalars().all()
        for qid in challenge_ids:
            try:
                db_ids.add(uuid.UUID(str(qid)))
            except ValueError:
                continue

        pvp_stmt = (
            select(PvPMatchAnswer.question_id)
            .join(PvPMatch, PvPMatch.id == PvPMatchAnswer.match_id)
            .where(
                or_(
                    PvPMatch.user1_id == user_id,
                    PvPMatch.user2_id == user_id,
                )
            )
        )
        if not cross_topic:
            pvp_stmt = pvp_stmt.where(func.lower(PvPMatch.topic) == normalized_topic)

        pvp_ids = (await db.execute(pvp_stmt)).scalars().all()
        for qid in pvp_ids:
            try:
                db_ids.add(uuid.UUID(str(qid)))
            except ValueError:
                continue

        # Populate the cache for next time (best-effort; never block selection).
        if use_cache and db_ids:
            try:
                await redis_client.sadd(cache_key, *[str(qid) for qid in db_ids])
                await redis_client.expire(cache_key, SEEN_SET_TTL_SECONDS)
            except Exception as exc:
                logger.debug("seen-set cache populate failed: %s", exc)

        seen_ids |= db_ids
        return seen_ids

    @staticmethod
    async def start_session(
        db: AsyncSession,
        user_id: uuid.UUID,
        topic: str,
        session_service: SessionService,
    ) -> dict:
        """
        Start a Classic Room session.

        1. Select 5 concepts based on weighted scoring
        2. Get user's current theta for each concept
        3. Store session state in Redis
        4. Select first question

        Returns: {session_id, first_question, session_stats}
        """
        session_id = uuid.uuid4()

        # Select concepts for session (weighted scoring)
        concepts = await ClassicService.select_concepts_for_session(
            db, user_id, topic, n_concepts=5
        )
        concept_ids = [c.id for c in concepts]

        if not concept_ids:
            logger.warning(
                "Classic session started without concept matches; using question fallback path user=%s topic=%s",
                str(user_id)[:8],
                topic,
            )

        # Persist a classic session row so dashboard room progress reflects activity.
        db.add(
            ClassicSession(
                id=session_id,
                user_id=user_id,
                topic=topic,
                questions_answered=0,
                correct_count=0,
                concepts=[str(cid) for cid in concept_ids],
            )
        )
        await db.commit()

        # Get user's theta snapshot for these concepts (if any)
        theta_snapshot = {}
        if concept_ids:
            theta_snapshot = await ConceptIRT.get_user_concept_thetas(
                db, user_id, concept_ids
            )

        # Store in Redis session
        session_state = {
            "user_id": str(user_id),
            "topic": topic,
            "concept_ids": [str(cid) for cid in concept_ids],
            "theta_snapshot": theta_snapshot,
            "questions_asked": [],
            "current_question_id": None,
        }
        await session_service.store_session_state(str(session_id), session_state)

        # Select first question
        first_question = await ClassicService.select_next_question(
            db,
            user_id,
            topic,
            concept_ids,
            asked_question_ids=[],
            theta_snapshot=theta_snapshot,
            redis_client=getattr(session_service, "redis", None),
        )

        # Store shuffled options + correct answer in session
        if first_question:
            await session_service.set_current_question(
                str(session_id),
                {
                    "id": first_question["id"],
                    "correct_answer": first_question["correct_answer"],
                    "shuffled_options": first_question["options"],
                    "correct_index_shuffled": first_question["correct_index"],
                    "question_sent_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                },
            )

        return {
            "session_id": str(session_id),
            "first_question": first_question,
            "session_stats": {"questions_answered": 0, "correct_count": 0},
        }

    @staticmethod
    async def select_concepts_for_session(
        db: AsyncSession, user_id: uuid.UUID, topic: str, n_concepts: int = 5
    ) -> list[Concept]:
        """
        Select N concepts using weighted scoring:

        Score = 0.4*mastery_gap + 0.3*recency_bonus + 0.2*repeat_due + 0.1*zpd_fit

        Where:
          mastery_gap = (3.0 - theta) / 6.0    # Higher for lower theta
          recency_bonus = min(days_since / 14, 1.0)  # Higher for stale concepts
          repeat_due = 1.0 if in_repeat_queue else 0.0
          zpd_fit - 0.5
        """
        # Get all concepts for topic (case-insensitive); "mix" can draw from any topic.
        parent_topic = topic
        sub_topic = None
        if topic and " - " in topic:
            parts = topic.split(" - ", 1)
            parent_topic = parts[0].strip()
            sub_topic = parts[1].strip()

        normalized_topic = (parent_topic or "mix").strip().lower()
        stmt = select(Concept)
        if normalized_topic != "mix":
            stmt = stmt.where(func.lower(Concept.topic) == normalized_topic)
        if sub_topic:
            stmt = stmt.where(func.lower(Concept.scope) == sub_topic.lower())
        result = await db.execute(stmt)
        concepts = result.scalars().all()

        scored_concepts = []
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # Batch-load the per-concept theta records and repeat-queue membership in
        # two queries keyed by the candidate concept ids, instead of running two
        # queries per concept inside the loop (an N+1 over every topic concept on
        # each session start). Scoring below is unchanged.
        concept_ids = [concept.id for concept in concepts]
        theta_by_concept: dict = {}
        repeat_concept_ids: set = set()
        if concept_ids:
            theta_rows = await db.execute(
                select(UserConceptTheta).where(
                    UserConceptTheta.user_id == user_id,
                    UserConceptTheta.concept_id.in_(concept_ids),
                )
            )
            theta_by_concept = {
                record.concept_id: record for record in theta_rows.scalars().all()
            }
            repeat_rows = await db.execute(
                select(UserConceptRepeatQueue.concept_id).where(
                    UserConceptRepeatQueue.user_id == user_id,
                    UserConceptRepeatQueue.concept_id.in_(concept_ids),
                )
            )
            repeat_concept_ids = set(repeat_rows.scalars().all())

        for concept in concepts:
            theta_record = theta_by_concept.get(concept.id)

            if not theta_record:
                # New concept: start with theta=0.0
                theta = 0.0
                days_since = 30
                repeat_due = 0.0
            else:
                theta = theta_record.theta
                days_since = (
                    (now - theta_record.last_updated).days
                    if theta_record.last_updated
                    else 30
                )
                # Repeat-queue membership only counts for known concepts, matching
                # the previous behavior where this was checked inside the else.
                repeat_due = 1.0 if concept.id in repeat_concept_ids else 0.0

            # Calculate score
            mastery_gap = (3.0 - theta) / 6.0
            recency_bonus = min(days_since / 14.0, 1.0)
            zpd_fit = 0.5

            score = (
                0.4 * mastery_gap
                + 0.3 * recency_bonus
                + 0.2 * repeat_due
                + 0.1 * zpd_fit
            )
            scored_concepts.append((concept, score))

        # Sort and return top n
        scored_concepts.sort(key=lambda x: x[1], reverse=True)
        return [c for c, _ in scored_concepts[:n_concepts]]

    @staticmethod
    async def _topic_exposure_count(db: AsyncSession, user_id: uuid.UUID, topic: str) -> int:
        parent_topic = topic
        if topic and " - " in topic:
            parent_topic = topic.split(" - ", 1)[0].strip()
        normalized_topic = (parent_topic or "mix").strip().lower()
        stmt = select(func.count()).select_from(UserResponse).where(UserResponse.user_id == user_id)
        if normalized_topic != "mix":
            stmt = stmt.where(func.lower(UserResponse.topic) == normalized_topic)
        return int((await db.scalar(stmt)) or 0)

    @staticmethod
    async def _try_select_due_repeat_question(
        db: AsyncSession,
        user_id: uuid.UUID,
        topic: str,
        concept_ids: list[uuid.UUID],
        asked_question_ids: list[str],
    ) -> QuestionBank | None:
        """Return a due spaced-repetition question without breaking session uniqueness.

        Correct answers are almost never repeated because they are queued at only
        1%. Wrong answers may be queued at 25% and become due after the user's
        topic exposure passes the stored threshold.
        """
        session_seen: set[uuid.UUID] = set()
        for raw in asked_question_ids or []:
            try:
                session_seen.add(uuid.UUID(str(raw)))
            except Exception:
                continue

        topic_exposure = await ClassicService._topic_exposure_count(db, user_id, topic)
        stmt = (
            select(UserConceptRepeatQueue)
            .join(QuestionBank, QuestionBank.id == UserConceptRepeatQueue.question_id)
            .where(
                UserConceptRepeatQueue.user_id == user_id,
                UserConceptRepeatQueue.due_after_session <= topic_exposure,
            )
            .order_by(UserConceptRepeatQueue.repeat_probability.desc(), func.random())
            .limit(8)
        )
        if concept_ids:
            stmt = stmt.where(UserConceptRepeatQueue.concept_id.in_(concept_ids))

        parent_topic = topic
        if topic and " - " in topic:
            parent_topic = topic.split(" - ", 1)[0].strip()
        normalized_topic = (parent_topic or "mix").strip().lower()
        if normalized_topic != "mix":
            stmt = stmt.where(func.lower(QuestionBank.topic) == normalized_topic)

        rows = (await db.execute(stmt)).scalars().all()
        for repeat_row in rows:
            if repeat_row.question_id in session_seen:
                continue
            # The queued row already passed the insertion probability. Keep a small
            # serving probability so repeats remain occasional, not annoying.
            if random.random() > float(repeat_row.repeat_probability or 0.5):
                continue
            question = await db.get(QuestionBank, repeat_row.question_id)
            if question is None:
                await db.delete(repeat_row)
                await db.commit()
                continue
            try:
                raw_options = json.loads(question.options_json or "[]")
            except Exception:
                raw_options = []
            if len(raw_options) < 2 or not str(question.correct_answer or "").strip():
                await db.delete(repeat_row)
                await db.commit()
                continue
            await db.delete(repeat_row)
            await db.commit()
            logger.info(
                "Classic serving due repeat question user=%s question=%s",
                str(user_id)[:8],
                str(question.id)[:8],
            )
            return question
        return None

    @staticmethod
    async def mark_seen_in_cache(redis_client, user_id: uuid.UUID, topic: str, question_id) -> None:
        """Append a just-answered question id to the per-user seen-set cache.

        Only appends to sets that already exist (via EXISTS), so it never creates a
        partial set missing the historical ids — an expired key is simply left for
        the next read to repopulate from the DB. No-op unless ENABLE_SEEN_SET_CACHE
        and a Redis client are provided. See roadmap item 3.
        """
        if not (ENABLE_SEEN_SET_CACHE and redis_client is not None):
            return
        parent_topic = topic
        if topic and " - " in topic:
            parent_topic = topic.split(" - ", 1)[0].strip()
        normalized_topic = (parent_topic or "mix").strip().lower()
        keys = {f"seen:{user_id}:{normalized_topic}", f"seen:{user_id}:mix"}
        for key in keys:
            try:
                if await redis_client.exists(key):
                    await redis_client.sadd(key, str(question_id))
                    await redis_client.expire(key, SEEN_SET_TTL_SECONDS)
            except Exception as exc:
                logger.debug("seen-set cache append failed: %s", exc)

    @staticmethod
    async def _fetch_candidates(db: AsyncSession, stmt, candidate_limit: int) -> list:
        """Fetch question candidates, applying the ordering strategy.

        Default: ORDER BY random() over the filtered set. Under
        ENABLE_CANDIDATE_POOL_SAMPLING: pull a bounded freshness pool
        (least-recently-served first, never-served first) and shuffle it in
        Python — avoiding a random() sort over the whole set while keeping variety.
        See QUALITY_PERF_ROADMAP_2026-07-04.md item 5.
        """
        if ENABLE_CANDIDATE_POOL_SAMPLING:
            pool = max(candidate_limit, CANDIDATE_POOL_SIZE)
            stmt = stmt.order_by(nullsfirst(QuestionBank.last_served_at.asc())).limit(pool)
            rows = list((await db.execute(stmt)).scalars().all())
            random.shuffle(rows)
            return rows
        stmt = stmt.order_by(func.random()).limit(candidate_limit)
        return list((await db.execute(stmt)).scalars().all())

    @staticmethod
    async def select_next_question(
        db: AsyncSession,
        user_id: uuid.UUID,
        topic: str,
        concept_ids: list[uuid.UUID],
        asked_question_ids: list[str],
        theta_snapshot: dict[str, float],
        redis_client=None,
    ) -> dict:
        """
        Select next question using IRT Zone of Proximal Development (ZPD).

        Algorithm:
        1. Calculate average theta for selected concepts
        2. Check if user is in warm-up mode (< 5 responses)
        3. If warm-up: use wide difficulty range
           Otherwise: calculate target ZPD (P_correct 60-75%)
        4. Query questions in beta range, excluding already-asked
        5. Shuffle options and return with new correct_index

        Returns: {id, text, options, correct_index, correct_answer, topic, difficulty}
        """
        parent_topic = topic
        sub_topic = None
        if topic and " - " in topic:
            parts = topic.split(" - ", 1)
            parent_topic = parts[0].strip()
            sub_topic = parts[1].strip()

        normalized_topic = (parent_topic or "mix").strip().lower()

        seen_question_ids = await ClassicService.get_user_seen_question_ids(
            db=db,
            user_id=user_id,
            topic=topic,
            asked_question_ids=asked_question_ids,
            redis_client=redis_client,
        )

        # Spaced repetition is the only intentional repeat path. It bypasses the
        # global seen filter, but still avoids repeats inside the same session.
        due_repeat = await ClassicService._try_select_due_repeat_question(
            db=db,
            user_id=user_id,
            topic=topic,
            concept_ids=concept_ids,
            asked_question_ids=asked_question_ids,
        )
        if due_repeat is not None:
            question = due_repeat
            try:
                raw_options = json.loads(question.options_json or "[]")
                options = [str(option) for option in raw_options if str(option).strip()]
                correct_answer = str(question.correct_answer or "").strip()
                if correct_answer in options and len(options) >= 2:
                    random.shuffle(options)
                    await db.execute(
                        sqlalchemy_update(QuestionBank)
                        .where(QuestionBank.id == question.id)
                        .values(
                            times_seen=QuestionBank.times_seen + 1,
                            last_served_at=datetime.now(timezone.utc).replace(tzinfo=None),
                        )
                    )
                    await db.commit()
                    return {
                        "id": str(question.id),
                        "text": question.question_text,
                        "options": options,
                        "correct_index": options.index(correct_answer),
                        "correct_answer": correct_answer,
                        "topic": question.topic,
                        "difficulty": beta_to_difficulty(question.difficulty_irt),
                    }
            except Exception:
                logger.debug("Classic due-repeat formatting failed", exc_info=True)

        # Get average theta
        thetas = [theta_snapshot.get(str(cid), 0.0) for cid in concept_ids]
        avg_theta = sum(thetas) / len(thetas) if thetas else 0.0

        # Check if user is in warm-up mode (few responses)
        response_count_stmt = (
            select(func.count())
            .select_from(UserResponse)
            .where(UserResponse.user_id == user_id)
        )
        response_count = int((await db.scalar(response_count_stmt)) or 0)

        if response_count < ClassicService.COLD_START_THRESHOLD:
            # Warm-up mode: wide range
            beta_low, beta_high = -2.0, 2.0
        else:
            # Normal mode: ZPD targeting (P_correct 60-75%)
            beta_low, beta_high = target_beta_range(avg_theta)

        # difficulty_irt is stored on the 1-5 bucket scale. The ZPD band above is
        # in logits, so under ENABLE_IRT_LOGIT_SCALE convert the band into 1-5
        # bucket bounds before filtering the column (otherwise the logit band —
        # e.g. [-1.1, -0.4] for theta=0 — matches no 1-5 rows and selection always
        # falls through to the broad fallbacks). Default off keeps prior behavior.
        if ENABLE_IRT_LOGIT_SCALE:
            filter_low = float(beta_to_difficulty(beta_low))
            filter_high = float(beta_to_difficulty(beta_high))
        else:
            filter_low, filter_high = beta_low, beta_high

        # Query questions
        governance_enabled = GovernanceService.enabled()

        candidate_limit = 20 if governance_enabled else 1

        filters = [
            QuestionBank.difficulty_irt >= filter_low,
            QuestionBank.difficulty_irt <= filter_high,
            ClassicService._classic_source_filter(),
        ]

        if governance_enabled:
            filters.append(QuestionBank.gov_approved == True)  # noqa: E712
            filters.append(QuestionBank.gov_safe == True)  # noqa: E712

        if seen_question_ids:
            filters.append(QuestionBank.id.notin_(list(seen_question_ids)))

        stmt = select(QuestionBank).where(and_(*filters))

        if normalized_topic != "mix":
            stmt = stmt.where(func.lower(QuestionBank.topic) == normalized_topic)

        if sub_topic:
            stmt = stmt.where(func.lower(QuestionBank.sub_topic) == sub_topic.lower())

        # Use concept-targeted selection when concepts exist, otherwise fall back to topic-only.
        if concept_ids:
            stmt = (
                stmt.join(QuestionConcept, QuestionBank.id == QuestionConcept.question_id)
                .where(QuestionConcept.concept_id.in_(concept_ids))
            )

        candidates = await ClassicService._fetch_candidates(db, stmt, candidate_limit)

        question = None
        if candidates:
            for candidate in candidates:
                if governance_enabled:
                    try:
                        decision = await GovernanceService.evaluate_bank_row_for_serving(
                            db,
                            row=candidate,
                            room="classic",
                            topic=topic,
                        )
                        if not decision.approved:
                            continue
                    except Exception:
                        # Governance must not block core gameplay.
                        pass
                question = candidate
                break

        # Fallback: if no question in ZPD, expand search
        if not question:
            stmt = select(QuestionBank)
            stmt = stmt.where(ClassicService._classic_source_filter())
            if governance_enabled:
                stmt = stmt.where(QuestionBank.gov_approved == True)  # noqa: E712
                stmt = stmt.where(QuestionBank.gov_safe == True)  # noqa: E712
            if seen_question_ids:
                stmt = stmt.where(QuestionBank.id.notin_(list(seen_question_ids)))
            if normalized_topic != "mix":
                stmt = stmt.where(func.lower(QuestionBank.topic) == normalized_topic)

            candidates = await ClassicService._fetch_candidates(db, stmt, candidate_limit)

            if candidates:
                for candidate in candidates:
                    if governance_enabled:
                        try:
                            decision = await GovernanceService.evaluate_bank_row_for_serving(
                                db,
                                row=candidate,
                                room="classic",
                                topic=topic,
                            )
                            if not decision.approved:
                                continue
                        except Exception:
                            pass
                    question = candidate
                    break

        # Final DB fallback: when the requested topic is exhausted, keep the
        # room responsive by serving another classic-bank question before the
        # router escalates to slower live LLM generation.
        if not question and normalized_topic != "mix":
            stmt = select(QuestionBank)
            stmt = stmt.where(ClassicService._classic_source_filter())
            if governance_enabled:
                stmt = stmt.where(QuestionBank.gov_approved == True)  # noqa: E712
                stmt = stmt.where(QuestionBank.gov_safe == True)  # noqa: E712
            if seen_question_ids:
                stmt = stmt.where(QuestionBank.id.notin_(list(seen_question_ids)))

            candidates = await ClassicService._fetch_candidates(db, stmt, candidate_limit)

            if candidates:
                for candidate in candidates:
                    if governance_enabled:
                        try:
                            decision = await GovernanceService.evaluate_bank_row_for_serving(
                                db,
                                row=candidate,
                                room="classic",
                                topic=topic,
                            )
                            if not decision.approved:
                                continue
                        except Exception:
                            pass
                    question = candidate
                    break

        if not question:
            return None

        # Shuffle options
        try:
            raw_options = json.loads(question.options_json or "[]")
        except (TypeError, json.JSONDecodeError):
            logger.warning(
                "Invalid classic options_json; skipping question_id=%s",
                str(question.id)[:8],
            )
            return None

        options = [str(option) for option in (raw_options or []) if str(option).strip()]
        correct_answer = str(question.correct_answer or "").strip()

        if not options or not correct_answer:
            logger.warning(
                "Classic question missing options or correct_answer; skipping question_id=%s",
                str(question.id)[:8],
            )
            return None
        if correct_answer not in options:
            logger.warning(
                "Classic question correct_answer not found in options; skipping question_id=%s",
                str(question.id)[:8],
            )
            return None

        random.shuffle(options)
        correct_index = options.index(correct_answer)

        # Update times_seen
        await db.execute(
            sqlalchemy_update(QuestionBank)
            .where(QuestionBank.id == question.id)
            .values(
                times_seen=QuestionBank.times_seen + 1,
                last_served_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        await db.commit()

        return {
            "id": str(question.id),
            "text": question.question_text,
            "options": options,
            "correct_index": correct_index,
            "correct_answer": correct_answer,
            "topic": question.topic,
            "difficulty": beta_to_difficulty(question.difficulty_irt),
        }

    @staticmethod
    async def process_answer(
        db: AsyncSession,
        user_id: uuid.UUID,
        session_id: str,
        question_id: str,
        selected_index: int,
        time_taken_seconds: int,
        session_service: SessionService,
        used_hint: bool = False,
    ) -> dict:
        """
        Process answer submission.

        1. Acquire session lock (prevent race conditions)
        2. Verify question is current in session
        3. Get shuffled options from session
        4. Compare selected answer to correct answer
        5. Update concept theta via IRT
        6. Add to repeat queue if applicable (25% wrong, 1% correct)
        7. Select next question (or end session if 10 questions done)

        Returns: {correct, correct_index, explanation, theta_changes, next_question, session_stats}
        """
        async with session_service.session_lock(session_id):
            # Get question
            question = await db.get(QuestionBank, uuid.UUID(question_id))

            if not question:
                raise ValueError("Question not found")

            # Get shuffled options from session
            current_question = await session_service.get_current_question(session_id)
            if not current_question or current_question["id"] != question_id:
                raise ValueError("Question mismatch")

            session_state = await session_service.get_session_state(session_id)
            if not session_state:
                raise ValueError("Session state not found")
            if question_id in session_state.get("questions_asked", []):
                raise ValueError("Question already answered")

            # Check answer
            if selected_index == -1:  # Timeout
                selected_answer = None
                correct = False
            else:
                shuffled_options = list(current_question.get("shuffled_options", []))
                if selected_index < 0 or selected_index >= len(shuffled_options):
                    logger.warning(
                        "Invalid classic selected_index: user=%s session=%s question=%s index=%s options=%s",
                        str(user_id)[:8],
                        str(session_id)[:8],
                        str(question_id)[:8],
                        selected_index,
                        len(shuffled_options),
                    )
                    raise ValueError("Invalid selected index")
                selected_answer = shuffled_options[selected_index]
                correct = selected_answer == current_question["correct_answer"]

            # Get concepts for this question
            concept_stmt = select(QuestionConcept).where(
                QuestionConcept.question_id == uuid.UUID(question_id)
            )
            question_concepts = (await db.execute(concept_stmt)).scalars().all()

            # Update theta for each concept.
            # difficulty_irt is stored on the 1-5 scale; under ENABLE_IRT_LOGIT_SCALE
            # convert it to a logit beta so irt_probability(theta, beta) is on a
            # consistent scale. Default off passes the raw value (prior behavior).
            raw_difficulty = question.difficulty_irt or 0.0
            concept_beta = (
                difficulty_to_beta_continuous(raw_difficulty)
                if ENABLE_IRT_LOGIT_SCALE
                else raw_difficulty
            )
            theta_changes = []
            for qc in question_concepts:
                old_theta = await ConceptIRT.get_concept_theta(
                    db, user_id, qc.concept_id
                )
                new_theta = await ConceptIRT.update_concept_theta(
                    db, user_id, qc.concept_id, concept_beta, correct
                )
                theta_changes.append(
                    {
                        "concept_id": str(qc.concept_id),
                        "theta_before": old_theta,
                        "theta_after": new_theta,
                    }
                )

            # Record answer in database
            response = UserResponse(
                id=uuid.uuid4(),
                user_id=user_id,
                session_id=uuid.UUID(session_id),
                question_id=uuid.UUID(question_id),
                topic=question.topic,
                difficulty_sent=beta_to_difficulty(question.difficulty_irt),
                answered_correct=correct,
                time_taken=time_taken_seconds,
                used_hint=used_hint,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            db.add(response)

            points_delta = ClassicService._compute_points_delta(
                correct=bool(correct),
                time_taken_seconds=int(time_taken_seconds or 0),
                used_hint=bool(used_hint),
            )

            user_row = await db.get(User, user_id)
            if user_row is not None:
                new_points = max(0, int(user_row.points or 0) + int(points_delta))
                user_row.points = int(new_points)
                user_row.level = compute_level(int(new_points))

            asked_ids = list(session_state.get("questions_asked", []))
            question_count = len(asked_ids) + 1
            is_finished = question_count >= ClassicService.MAX_QUESTIONS_PER_SESSION

            session_row = await db.get(ClassicSession, uuid.UUID(session_id))
            if session_row is None:
                session_row = ClassicSession(
                    id=uuid.UUID(session_id),
                    user_id=user_id,
                    topic=session_state.get("topic", "mix"),
                    questions_answered=question_count,
                    correct_count=(1 if correct else 0),
                    concepts=session_state.get("concept_ids", []),
                    ended_at=datetime.now(timezone.utc).replace(tzinfo=None) if is_finished else None,
                )
                db.add(session_row)
            else:
                session_row.questions_answered = int(question_count)
                if correct:
                    session_row.correct_count = int(session_row.correct_count or 0) + 1
                if is_finished and session_row.ended_at is None:
                    session_row.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)

            # Add to spaced-repetition queue if applicable. Correct answers are
            # almost never repeated (1%). Wrong answers have a 25% chance to become
            # due after about 7 more exposures in the same topic.
            topic_exposure_now = await ClassicService._topic_exposure_count(
                db=db,
                user_id=user_id,
                topic=session_state.get("topic", question.topic),
            )
            for qc in question_concepts:
                repeat_probability = None
                due_delay = 0
                if correct and random.random() < ClassicService.CORRECT_ANSWER_REPEAT_PROBABILITY:
                    repeat_probability = 0.05
                    due_delay = 12
                elif not correct and random.random() < ClassicService.WRONG_ANSWER_REPEAT_PROBABILITY:
                    repeat_probability = 0.75
                    due_delay = ClassicService.REPEAT_DUE_SESSIONS

                if repeat_probability is None:
                    continue

                due_after = int(topic_exposure_now) + int(due_delay)
                existing_repeat = (
                    await db.execute(
                        select(UserConceptRepeatQueue).where(
                            UserConceptRepeatQueue.user_id == user_id,
                            UserConceptRepeatQueue.concept_id == qc.concept_id,
                            UserConceptRepeatQueue.question_id == uuid.UUID(question_id),
                        )
                    )
                ).scalar_one_or_none()
                if existing_repeat is None:
                    db.add(
                        UserConceptRepeatQueue(
                            id=uuid.uuid4(),
                            user_id=user_id,
                            concept_id=qc.concept_id,
                            question_id=uuid.UUID(question_id),
                            repeat_probability=repeat_probability,
                            due_after_session=due_after,
                            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                        )
                    )
                else:
                    existing_repeat.repeat_probability = max(float(existing_repeat.repeat_probability or 0.0), repeat_probability)
                    existing_repeat.due_after_session = max(int(existing_repeat.due_after_session or 0), due_after)

            await db.commit()

            asked_ids.append(question_id)
            session_state["questions_asked"] = asked_ids

            # Increment question count (for repeat queue)
            session_state["is_finished"] = is_finished

            # Persist session state on every answer, including the final one.
            await session_service.store_session_state(session_id, session_state)

            # Select next question or end session
            if is_finished:
                next_question = None
                next_question_public = None
            else:
                next_question = await ClassicService.select_next_question(
                    db,
                    user_id,
                    session_state["topic"],
                    [uuid.UUID(cid) for cid in session_state.get("concept_ids", [])],
                    asked_ids,
                    theta_snapshot=session_state.get("theta_snapshot", {}),
                    redis_client=getattr(session_service, "redis", None),
                )

                # Store next question shuffled options
                if next_question:
                    await session_service.set_current_question(
                        session_id,
                        {
                            "id": next_question["id"],
                            "correct_answer": next_question["correct_answer"],
                            "shuffled_options": next_question["options"],
                            "correct_index_shuffled": next_question["correct_index"],
                            "question_sent_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                        },
                    )

                    # Never expose the next question's answer metadata to clients.
                    next_question_public = {
                        "id": next_question["id"],
                        "text": next_question["text"],
                        "options": next_question["options"],
                        "topic": next_question.get("topic"),
                        "difficulty": next_question.get("difficulty"),
                    }
                else:
                    next_question_public = None

            correct_index_shuffled = current_question.get("correct_index_shuffled")
            if correct_index_shuffled is None:
                try:
                    correct_index_shuffled = current_question["shuffled_options"].index(
                        current_question["correct_answer"]
                    )
                except Exception:
                    correct_index_shuffled = -1

            return {
                "correct": correct,
                "correct_index": correct_index_shuffled,
                "explanation": question.explanation,
                "theta_changes": theta_changes,
                "next_question": next_question_public,
                "session_stats": {
                    "questions_answered": question_count,
                    "correct_count": 1 if correct else 0,  # This answer's correctness
                    "is_finished": is_finished,
                },
            }


