"""
setup_test_users.py

Creates a deterministic set of test users directly in the database for local
development and monitoring.

The script is idempotent and seeds identity/profile state used by the
challenge, custom, onboarding, and PvP flows.

By default, it does NOT insert synthetic gameplay history rows.
Use scripts/generate_real_test_user_history.py to create real history through
live API question generation.

Optional legacy behavior:
    --with-sample-history
    Keeps previous synthetic sample session seeding for debugging/backfill.

Usage:
    python scripts/setup_test_users.py
"""

from __future__ import annotations

import asyncio
import argparse
import csv
import json
import random
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import bcrypt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import DATABASE_URL
from database.challenge_models import ChallengeAnswer, ChallengeRanking, ChallengeSession
from database.concept_models import Concept, UserConceptTheta
from database.custom_models import CustomSession, UserTopicMastery
from database.models import QuestionBank, User
from database.onboarding_models import UserOnboardingFlags, UserOnboardingTopic
from database.pvp_models import PvPMatch, PvPRating
from seeds.seed import seed_all
from services.challenge_service import CHALLENGE_POINTS_TABLE, compute_rank_from_points, get_available_levels
from services.custom_service import TOPIC_CATALOGUE

PASSWORD_DEFAULT = "TestPass123!"
PASSWORD_ADMIN = "AdminPass123!"
EXPORT_DIR = Path(__file__).resolve().parents[1] / "generated"
EXPORT_JSON = EXPORT_DIR / "test_users.json"
EXPORT_CSV = EXPORT_DIR / "test_users.csv"


def _utcnow() -> datetime:
    """Timezone-safe UTC now, stored as naive UTC for current DB schema."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

CONCEPT_GROUPS = {
    "history": [
        "Ancient Egypt",
        "Ancient Rome",
        "Medieval Europe",
        "World War I",
        "World War II",
        "Cold War",
    ],
    "geography": [
        "African Countries",
        "Asian Capitals",
        "European Geography",
    ],
}

CONCEPT_OFFSETS = {
    "Ancient Egypt": 0.10,
    "Ancient Rome": -0.15,
    "Medieval Europe": 0.05,
    "World War I": -0.20,
    "World War II": 0.15,
    "Cold War": -0.10,
    "African Countries": 0.20,
    "Asian Capitals": -0.05,
    "European Geography": 0.12,
}

PERSONAS: list[dict[str, Any]] = [
    {
        "key": "admin_master",
        "email": "admin.master@example.com",
        "username": "admin_master",
        "password": PASSWORD_ADMIN,
        "points": 18500,
        "level": "Expert",
        "elo_global": 1825.0,
        "is_admin": True,
        "is_active": True,
        "onboarding": {"first_login": False, "onboarding_completed": True, "tour_seen": True},
        "challenge_rank_points": 18500,
        "challenge_total_sessions": 28,
        "challenge_total_questions": 214,
        "challenge_highest_streak": 16,
        "challenge_topic": "Mixed",
        "history_theta": 2.15,
        "history_responses": 84,
        "geography_theta": 2.05,
        "geography_responses": 76,
        "custom_topics": {
            "History - World War II": 100.0,
            "Geography - France": 100.0,
        },
        "onboarding_confident": ["History - World War II", "Geography - France"],
        "onboarding_learn": [],
    },
    {
        "key": "challenge_e_newbie",
        "email": "challenge.e@example.com",
        "username": "challenge_e",
        "password": PASSWORD_DEFAULT,
        "points": 30,
        "level": "Novice",
        "elo_global": 980.0,
        "is_admin": False,
        "is_active": True,
        "onboarding": {"first_login": True, "onboarding_completed": False, "tour_seen": False},
        "challenge_rank_points": 120,
        "challenge_total_sessions": 3,
        "challenge_total_questions": 18,
        "challenge_highest_streak": 2,
        "challenge_topic": "Mixed",
        "history_theta": -1.35,
        "history_responses": 8,
        "geography_theta": -0.95,
        "geography_responses": 6,
        "custom_topics": {
            "History - World War II": 8.0,
            "Geography - France": 0.0,
        },
        "onboarding_confident": [],
        "onboarding_learn": ["History - World War II", "Geography - France"],
    },
    {
        "key": "challenge_d_climber",
        "email": "challenge.d@example.com",
        "username": "challenge_d",
        "password": PASSWORD_DEFAULT,
        "points": 640,
        "level": "Intermediate",
        "elo_global": 1105.0,
        "is_admin": False,
        "is_active": True,
        "onboarding": {"first_login": False, "onboarding_completed": True, "tour_seen": False},
        "challenge_rank_points": 1450,
        "challenge_total_sessions": 9,
        "challenge_total_questions": 61,
        "challenge_highest_streak": 5,
        "challenge_topic": "Mixed",
        "history_theta": -0.55,
        "history_responses": 20,
        "geography_theta": 0.25,
        "geography_responses": 18,
        "custom_topics": {
            "History - World War II": 42.0,
            "Geography - France": 18.0,
        },
        "onboarding_confident": ["Geography - France"],
        "onboarding_learn": ["History - Cold War"],
    },
    {
        "key": "challenge_c_balanced",
        "email": "challenge.c@example.com",
        "username": "challenge_c",
        "password": PASSWORD_DEFAULT,
        "points": 1260,
        "level": "Intermediate",
        "elo_global": 1220.0,
        "is_admin": False,
        "is_active": True,
        "onboarding": {"first_login": False, "onboarding_completed": True, "tour_seen": True},
        "challenge_rank_points": 4200,
        "challenge_total_sessions": 15,
        "challenge_total_questions": 104,
        "challenge_highest_streak": 7,
        "challenge_topic": "Mixed",
        "history_theta": 0.15,
        "history_responses": 28,
        "geography_theta": 0.35,
        "geography_responses": 30,
        "custom_topics": {
            "History - World War II": 50.0,
            "Geography - France": 34.0,
        },
        "onboarding_confident": ["History - Ancient Rome", "Geography - France"],
        "onboarding_learn": ["History - Cold War"],
    },
    {
        "key": "challenge_b_strategist",
        "email": "challenge.b@example.com",
        "username": "challenge_b",
        "password": PASSWORD_DEFAULT,
        "points": 2050,
        "level": "Advanced",
        "elo_global": 1385.0,
        "is_admin": False,
        "is_active": True,
        "onboarding": {"first_login": False, "onboarding_completed": True, "tour_seen": True},
        "challenge_rank_points": 8100,
        "challenge_total_sessions": 24,
        "challenge_total_questions": 166,
        "challenge_highest_streak": 11,
        "challenge_topic": "Mixed",
        "history_theta": 1.45,
        "history_responses": 44,
        "geography_theta": -0.35,
        "geography_responses": 16,
        "custom_topics": {
            "History - World War II": 72.0,
            "Geography - France": 24.0,
        },
        "onboarding_confident": ["History - World War II"],
        "onboarding_learn": ["Geography - Japan"],
    },
    {
        "key": "classic_novice",
        "email": "classic.novice@example.com",
        "username": "classic_novice",
        "password": PASSWORD_DEFAULT,
        "points": 90,
        "level": "Novice",
        "elo_global": 1000.0,
        "is_admin": False,
        "is_active": True,
        "onboarding": {"first_login": True, "onboarding_completed": False, "tour_seen": False},
        "challenge_rank_points": 0,
        "challenge_total_sessions": 1,
        "challenge_total_questions": 5,
        "challenge_highest_streak": 1,
        "challenge_topic": "Mixed",
        "history_theta": -1.70,
        "history_responses": 4,
        "geography_theta": -1.25,
        "geography_responses": 4,
        "custom_topics": {
            "History - World War II": 0.0,
            "Geography - France": 0.0,
        },
        "onboarding_confident": [],
        "onboarding_learn": ["History - Ancient Rome", "Geography - France"],
    },
    {
        "key": "classic_expert",
        "email": "classic.expert@example.com",
        "username": "classic_expert",
        "password": PASSWORD_DEFAULT,
        "points": 2320,
        "level": "Expert",
        "elo_global": 1660.0,
        "is_admin": False,
        "is_active": True,
        "onboarding": {"first_login": False, "onboarding_completed": True, "tour_seen": True},
        "challenge_rank_points": 15350,
        "challenge_total_sessions": 34,
        "challenge_total_questions": 240,
        "challenge_highest_streak": 19,
        "challenge_topic": "Mixed",
        "history_theta": 2.30,
        "history_responses": 90,
        "geography_theta": 2.10,
        "geography_responses": 82,
        "custom_topics": {
            "History - World War II": 100.0,
            "Geography - France": 100.0,
        },
        "onboarding_confident": ["History - Ancient Rome", "Geography - Japan"],
        "onboarding_learn": [],
    },
    {
        "key": "custom_fresh",
        "email": "custom.fresh@example.com",
        "username": "custom_fresh",
        "password": PASSWORD_DEFAULT,
        "points": 10,
        "level": "Novice",
        "elo_global": 1000.0,
        "is_admin": False,
        "is_active": True,
        "onboarding": {"first_login": True, "onboarding_completed": False, "tour_seen": False},
        "challenge_rank_points": 10,
        "challenge_total_sessions": 0,
        "challenge_total_questions": 0,
        "challenge_highest_streak": 0,
        "challenge_topic": "Mixed",
        "history_theta": -1.10,
        "history_responses": 2,
        "geography_theta": -0.90,
        "geography_responses": 2,
        "custom_topics": {
            "History - World War II": 0.0,
            "Geography - France": 0.0,
        },
        "onboarding_confident": [],
        "onboarding_learn": ["History - World War II", "Geography - France"],
    },
    {
        "key": "custom_complete",
        "email": "custom.complete@example.com",
        "username": "custom_complete",
        "password": PASSWORD_DEFAULT,
        "points": 780,
        "level": "Intermediate",
        "elo_global": 1195.0,
        "is_admin": False,
        "is_active": True,
        "onboarding": {"first_login": False, "onboarding_completed": True, "tour_seen": False},
        "challenge_rank_points": 3300,
        "challenge_total_sessions": 11,
        "challenge_total_questions": 74,
        "challenge_highest_streak": 6,
        "challenge_topic": "Mixed",
        "history_theta": 0.55,
        "history_responses": 24,
        "geography_theta": 0.75,
        "geography_responses": 24,
        "custom_topics": {
            "History - World War II": 100.0,
            "History - Cold War": 82.0,
            "Geography - France": 88.0,
        },
        "onboarding_confident": ["History - World War II", "Geography - France"],
        "onboarding_learn": ["History - Cold War"],
    },
    {
        "key": "pvp_grinder",
        "email": "pvp.grinder@example.com",
        "username": "pvp_grinder",
        "password": PASSWORD_DEFAULT,
        "points": 1450,
        "level": "Advanced",
        "elo_global": 1575.0,
        "is_admin": False,
        "is_active": True,
        "onboarding": {"first_login": False, "onboarding_completed": True, "tour_seen": True},
        "challenge_rank_points": 7450,
        "challenge_total_sessions": 20,
        "challenge_total_questions": 142,
        "challenge_highest_streak": 9,
        "challenge_topic": "Mixed",
        "history_theta": 0.95,
        "history_responses": 34,
        "geography_theta": 1.10,
        "geography_responses": 36,
        "custom_topics": {
            "History - World War II": 68.0,
            "Geography - France": 66.0,
        },
        "onboarding_confident": ["Geography - France"],
        "onboarding_learn": ["History - Ancient Rome"],
    },
    {
        "key": "challenge_all_levels",
        "email": "challenge.alllevels@example.com",
        "username": "challenge_all_levels",
        "password": PASSWORD_DEFAULT,
        "points": 2890,
        "level": "Master",
        "elo_global": 1740.0,
        "is_admin": False,
        "is_active": True,
        "onboarding": {"first_login": False, "onboarding_completed": True, "tour_seen": True},
        "challenge_rank_points": 22000,
        "challenge_total_sessions": 41,
        "challenge_total_questions": 318,
        "challenge_highest_streak": 17,
        "challenge_topic": "Mixed",
        "history_theta": 2.35,
        "history_responses": 102,
        "geography_theta": 2.25,
        "geography_responses": 97,
        "custom_topics": {
            "History - World War I": 100.0,
            "History - World War II": 100.0,
            "Geography - United States": 92.0,
            "Geography - France": 96.0,
        },
        "onboarding_confident": ["History - World War II", "Geography - United States"],
        "onboarding_learn": [],
    },
]


def _hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def _stable_uuid(value: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"adaptiq:test-user:{value}")


def _theta_to_mastery(theta: float) -> str:
    if theta < -1.0:
        return "BEGINNER"
    if theta < 0.0:
        return "NOVICE"
    if theta < 1.0:
        return "INTERMEDIATE"
    if theta < 2.0:
        return "ADVANCED"
    return "EXPERT"


def _topic_total(topic_label: str) -> int:
    topic_name = topic_label.split(" - ", 1)[-1]
    for item in TOPIC_CATALOGUE:
        if item["name"] == topic_name:
            return int(item["total_facts"])
    return 500 if topic_label.startswith("Geography") else 1000


async def _upsert_user(session: AsyncSession, persona: dict[str, Any]) -> User:
    result = await session.execute(select(User).where(User.email == persona["email"]))
    user = result.scalar_one_or_none()
    if user is None:
        username_result = await session.execute(select(User).where(User.username == persona["username"]))
        user = username_result.scalar_one_or_none()
    if user is None:
        user = User(
            id=_stable_uuid(persona["email"]),
            email=persona["email"],
            username=persona["username"],
            password_hash=_hash_password(persona["password"]),
            points=persona["points"],
            level=persona["level"],
            elo_global=persona["elo_global"],
            is_active=persona["is_active"],
            is_admin=persona["is_admin"],
            created_at=_utcnow(),
        )
        session.add(user)
        await session.flush()
    else:
        user.email = persona["email"]
        user.username = persona["username"]
        user.password_hash = _hash_password(persona["password"])
        user.points = persona["points"]
        user.level = persona["level"]
        user.elo_global = persona["elo_global"]
        user.is_active = persona["is_active"]
        user.is_admin = persona["is_admin"]
    return user


async def _upsert_onboarding(session: AsyncSession, user_id: uuid.UUID, persona: dict[str, Any]) -> None:
    flags_result = await session.execute(select(UserOnboardingFlags).where(UserOnboardingFlags.user_id == user_id))
    flags = flags_result.scalar_one_or_none()
    if flags is None:
        flags = UserOnboardingFlags(
            user_id=user_id,
            first_login=persona["onboarding"]["first_login"],
            onboarding_completed=persona["onboarding"]["onboarding_completed"],
            tour_seen=persona["onboarding"]["tour_seen"],
        )
        session.add(flags)
    else:
        flags.first_login = persona["onboarding"]["first_login"]
        flags.onboarding_completed = persona["onboarding"]["onboarding_completed"]
        flags.tour_seen = persona["onboarding"]["tour_seen"]

    desired_pairs: list[tuple[str, str]] = []
    for topic in persona.get("onboarding_confident", []):
        desired_pairs.append(("confident", topic))
    for topic in persona.get("onboarding_learn", []):
        desired_pairs.append(("want_to_learn", topic))

    existing_result = await session.execute(select(UserOnboardingTopic).where(UserOnboardingTopic.user_id == user_id))
    existing_rows = {
        (row.category, row.topic): row
        for row in existing_result.scalars().all()
    }

    for category, topic in desired_pairs:
        if (category, topic) not in existing_rows:
            session.add(UserOnboardingTopic(user_id=user_id, topic=topic, category=category))


async def _upsert_challenge_ranking(session: AsyncSession, user_id: uuid.UUID, persona: dict[str, Any]) -> None:
    rank_points = persona["challenge_rank_points"]
    rank = compute_rank_from_points(rank_points)
    result = await session.execute(select(ChallengeRanking).where(ChallengeRanking.user_id == user_id))
    row = result.scalar_one_or_none()
    if row is None:
        row = ChallengeRanking(
            user_id=user_id,
            current_rank=rank,
            rank_points=rank_points,
            total_sessions=persona["challenge_total_sessions"],
            total_questions=persona["challenge_total_questions"],
            highest_streak=persona["challenge_highest_streak"],
            updated_at=_utcnow(),
        )
        session.add(row)
    else:
        row.current_rank = rank
        row.rank_points = rank_points
        row.total_sessions = persona["challenge_total_sessions"]
        row.total_questions = persona["challenge_total_questions"]
        row.highest_streak = persona["challenge_highest_streak"]
        row.updated_at = _utcnow()


async def _upsert_pvp_rating(session: AsyncSession, user_id: uuid.UUID, persona: dict[str, Any]) -> None:
    result = await session.execute(select(PvPRating).where(PvPRating.user_id == user_id))
    row = result.scalar_one_or_none()
    if row is None:
        row = PvPRating(user_id=user_id, elo_rating=persona["elo_global"])
        session.add(row)
    else:
        row.elo_rating = persona["elo_global"]


async def _upsert_concept_mastery(session: AsyncSession, user_id: uuid.UUID, persona: dict[str, Any]) -> None:
    concept_result = await session.execute(select(Concept))
    concepts = {concept.name: concept for concept in concept_result.scalars().all()}

    existing_result = await session.execute(select(UserConceptTheta).where(UserConceptTheta.user_id == user_id))
    existing_rows = {row.concept_id: row for row in existing_result.scalars().all()}

    group_specs = {
        "history": {
            "theta": persona["history_theta"],
            "responses": persona["history_responses"],
            "concepts": CONCEPT_GROUPS["history"],
        },
        "geography": {
            "theta": persona["geography_theta"],
            "responses": persona["geography_responses"],
            "concepts": CONCEPT_GROUPS["geography"],
        },
    }

    now = _utcnow()
    for spec in group_specs.values():
        for concept_name in spec["concepts"]:
            concept = concepts.get(concept_name)
            if concept is None:
                continue
            theta = round(spec["theta"] + CONCEPT_OFFSETS[concept_name], 2)
            responses = int(spec["responses"])
            exposure = responses + 4
            mastery = _theta_to_mastery(theta)
            row = existing_rows.get(concept.id)
            if row is None:
                session.add(
                    UserConceptTheta(
                        user_id=user_id,
                        concept_id=concept.id,
                        theta=theta,
                        theta_variance=round(max(0.12, 1.0 / (responses + 1)), 3),
                        response_count=responses,
                        exposure_count=exposure,
                        mastery_level=mastery,
                        first_seen_at=now,
                        last_played_at=now,
                        last_updated=now,
                    )
                )
            else:
                row.theta = theta
                row.theta_variance = round(max(0.12, 1.0 / (responses + 1)), 3)
                row.response_count = responses
                row.exposure_count = exposure
                row.mastery_level = mastery
                row.last_played_at = now
                row.last_updated = now


async def _upsert_custom_mastery(session: AsyncSession, user_id: uuid.UUID, persona: dict[str, Any]) -> None:
    for topic_label, completion in persona["custom_topics"].items():
        total_facts = _topic_total(topic_label)
        mastered = int(round(total_facts * (completion / 100.0)))
        result = await session.execute(
            select(UserTopicMastery).where(
                UserTopicMastery.user_id == user_id,
                UserTopicMastery.topic == topic_label,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            session.add(
                UserTopicMastery(
                    user_id=user_id,
                    topic=topic_label,
                    mastered_facts_count=mastered,
                    total_facts_count=total_facts,
                    completion_percentage=float(completion),
                    last_session_at=_utcnow() if completion > 0 else None,
                )
            )
        else:
            row.mastered_facts_count = mastered
            row.total_facts_count = total_facts
            row.completion_percentage = float(completion)
            row.last_session_at = _utcnow() if completion > 0 else None


async def _seed_sample_sessions(session: AsyncSession, user_id: uuid.UUID, persona: dict[str, Any]) -> ChallengeSession | None:
    challenge_result = await session.execute(
        select(ChallengeSession)
        .where(ChallengeSession.user_id == user_id)
        .order_by(ChallengeSession.started_at.desc())
    )
    challenge_session = challenge_result.scalars().first()
    if challenge_session is None:
        rank = compute_rank_from_points(persona["challenge_rank_points"])
        levels = get_available_levels(rank)
        starting_level = levels[0]
        current_level = levels[min(1, len(levels) - 1)]
        challenge_session = ChallengeSession(
            user_id=user_id,
            topic=persona["challenge_topic"],
            starting_level=starting_level,
            current_level=current_level,
            rank_points=min(persona["challenge_rank_points"], 250),
            streak_correct=max(persona["challenge_highest_streak"] - 1, 0),
            streak_wrong=0,
            total_questions=min(persona["challenge_total_questions"], 12),
            correct_answers=max(min(persona["challenge_total_questions"], 12) - 3, 0),
            started_at=_utcnow(),
            ended_at=_utcnow(),
            is_completed=True,
        )
        session.add(challenge_session)
        await session.flush()

    custom_result = await session.execute(select(CustomSession).where(CustomSession.user_id == user_id))
    if custom_result.scalars().first() is None:
        topic_label = next(iter(persona["custom_topics"].keys()))
        completion = float(persona["custom_topics"][topic_label])
        total_questions = 8 if completion > 0 else 0
        session.add(
            CustomSession(
                user_id=user_id,
                topic=topic_label,
                started_at=_utcnow(),
                ended_at=_utcnow(),
                total_questions=total_questions,
                correct_count=int(round(total_questions * (completion / 100.0))) if total_questions else 0,
            )
        )

    return challenge_session


def _pick_incorrect_option(question: QuestionBank, rng: random.Random) -> str:
    options: list[str] = []
    try:
        parsed = json.loads(question.options_json or "[]")
        if isinstance(parsed, list):
            options = [str(opt).strip() for opt in parsed if str(opt).strip()]
    except Exception:
        options = []

    wrongs = [opt for opt in options if opt.lower() != str(question.correct_answer).strip().lower()]
    if wrongs:
        return rng.choice(wrongs)
    return "Unknown"


async def _seed_challenge_answer_history(
    session: AsyncSession,
    challenge_session: ChallengeSession,
    persona: dict[str, Any],
) -> None:
    existing_answers = (
        await session.execute(
            select(func.count()).select_from(ChallengeAnswer).where(ChallengeAnswer.session_id == challenge_session.id)
        )
    ).scalar() or 0
    if existing_answers > 0:
        return

    target_count = max(6, min(int(persona.get("challenge_total_questions", 24) // 8), 20))

    question_stmt = select(QuestionBank)
    topic = str(challenge_session.topic or "Mixed").strip().lower()
    if topic in {"history", "geography"}:
        question_stmt = question_stmt.where(func.lower(QuestionBank.topic) == topic)

    question_stmt = question_stmt.order_by(QuestionBank.created_at.asc(), QuestionBank.id.asc())
    question_rows = (await session.execute(question_stmt)).scalars().all()
    if not question_rows:
        return

    rng = random.Random(f"{challenge_session.user_id}:{challenge_session.id}")
    rng.shuffle(question_rows)
    selected_questions = question_rows[: min(target_count, len(question_rows))]
    if not selected_questions:
        return

    target_accuracy = 0.78 if int(persona.get("challenge_rank_points", 0)) >= 15000 else 0.62
    target_correct = max(1, min(len(selected_questions), int(round(len(selected_questions) * target_accuracy))))
    correct_indexes = set(rng.sample(range(len(selected_questions)), target_correct))

    total_points = 0
    correct_count = 0
    level = int(challenge_session.current_level or challenge_session.starting_level or 1)
    points_for_level = CHALLENGE_POINTS_TABLE.get(level, (3, -1))

    for idx, question in enumerate(selected_questions):
        is_correct = idx in correct_indexes
        chosen_answer = str(question.correct_answer).strip() if is_correct else _pick_incorrect_option(question, rng)
        points_change = points_for_level[0] if is_correct else points_for_level[1]

        total_points += points_change
        if is_correct:
            correct_count += 1

        session.add(
            ChallengeAnswer(
                session_id=challenge_session.id,
                question_id=question.id,
                chosen_answer=chosen_answer,
                is_correct=is_correct,
                points_change=points_change,
                level_at_answer=level,
                time_taken=round(rng.uniform(4.5, 18.0), 2),
                created_at=_utcnow(),
            )
        )

    challenge_session.total_questions = len(selected_questions)
    challenge_session.correct_answers = correct_count
    challenge_session.rank_points = total_points
    challenge_session.streak_correct = min(int(persona.get("challenge_highest_streak", 0)), max(0, correct_count // 2))
    challenge_session.streak_wrong = 0
    challenge_session.is_completed = True
    challenge_session.ended_at = challenge_session.ended_at or _utcnow()


async def _seed_sample_pvp_match(session: AsyncSession, users: dict[str, User]) -> None:
    count_result = await session.execute(select(func.count()).select_from(PvPMatch))
    if (count_result.scalar() or 0) > 0:
        return

    player1 = users["pvp_grinder"]
    player2 = users["challenge_c_balanced"]
    session.add(
        PvPMatch(
            user1_id=player1.id,
            user2_id=player2.id,
            topic="Mixed",
            status="completed",
            total_questions=5,
            questions_json=json.dumps([]),
            user1_score=4,
            user2_score=3,
            user1_finished=True,
            user2_finished=True,
            winner_id=player1.id,
            elo_change=18.0,
            started_at=_utcnow(),
            ended_at=_utcnow(),
            created_at=_utcnow(),
        )
    )


def _profile_export_row(persona: dict[str, Any]) -> dict[str, Any]:
    return {
        "email": persona["email"],
        "username": persona["username"],
        "password": persona["password"],
        "is_admin": persona["is_admin"],
        "points": persona["points"],
        "level": persona["level"],
        "elo_global": persona["elo_global"],
        "challenge_rank_points": persona["challenge_rank_points"],
        "custom_topics": persona["custom_topics"],
        "onboarding": persona["onboarding"],
    }


def _write_exports(personas: list[dict[str, Any]]) -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    rows = [_profile_export_row(persona) for persona in personas]

    with EXPORT_JSON.open("w", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=2)

    with EXPORT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "email",
                "username",
                "password",
                "is_admin",
                "points",
                "level",
                "elo_global",
                "challenge_rank_points",
                "custom_topics",
                "onboarding",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **row,
                    "custom_topics": json.dumps(row["custom_topics"]),
                    "onboarding": json.dumps(row["onboarding"]),
                }
            )


async def create_test_users(*, with_sample_history: bool = False) -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        await seed_all(async_session)

        users: dict[str, User] = {}
        async with async_session() as session:
            for persona in PERSONAS:
                user = await _upsert_user(session, persona)
                users[persona["key"]] = user
                await _upsert_onboarding(session, user.id, persona)
                await _upsert_challenge_ranking(session, user.id, persona)
                await _upsert_pvp_rating(session, user.id, persona)
                await _upsert_concept_mastery(session, user.id, persona)
                await _upsert_custom_mastery(session, user.id, persona)
                if with_sample_history:
                    challenge_session = await _seed_sample_sessions(session, user.id, persona)
                    if challenge_session is not None:
                        await _seed_challenge_answer_history(session, challenge_session, persona)

            if with_sample_history:
                await _seed_sample_pvp_match(session, users)
            await session.commit()

        _write_exports(PERSONAS)

        print("\nTest users created successfully!\n")
        for persona in PERSONAS:
            print(f"{persona['email']}  |  {persona['password']}  |  {persona['username']}")
        print(f"\nJSON export: {EXPORT_JSON}")
        print(f"CSV export:   {EXPORT_CSV}")
        print("\nLocal links:")
        print("  Frontend: http://localhost:5173")
        print("  Backend:  http://localhost:8000/docs")
        print("  Admin:    http://localhost:5173/admin")
        if with_sample_history:
            print("\nMode: with synthetic sample gameplay history (--with-sample-history)")
        else:
            print("\nMode: profile-only seeding (no synthetic gameplay history)")
            print("For real generated history, run: python scripts/generate_real_test_user_history.py")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed deterministic AdaptIQ test users")
    parser.add_argument(
        "--with-sample-history",
        action="store_true",
        help="Seed legacy synthetic sample sessions/matches in addition to profile state",
    )
    args = parser.parse_args()
    asyncio.run(create_test_users(with_sample_history=args.with_sample_history))
