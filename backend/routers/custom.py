"""
routers/custom.py
Custom Room endpoints with concept-aware progression.

Covers:
    - GET  /api/custom/topics
    - GET  /api/custom/topics/{topic}/concepts
    - GET  /api/custom/user/{user_id}/concept-mastery
    - POST /api/custom/start-session
    - POST /api/custom/generate-question
    - POST /api/custom/hint
    - POST /api/custom/submit-answer
    - POST /api/custom/end-session

Internal helper groups:
    - Topic parsing/scope controls and keyword filters
    - Redis/in-memory caches for recent questions, signatures, and rotation windows
    - Dynamic concept focus and difficulty targeting
    - RAG/LLM/offline fallback generation and payload normalization
"""



import json
import logging
import random
import uuid
from datetime import datetime, timezone
from typing import Optional, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_, select

from config import CUSTOM_ROOM_SIMPLE_MODE, QUIZ_QUESTIONS_PER_SESSION, ENABLE_UNIFIED_CONCEPT_THETA
from dependencies import limiter
from database.concept_models import Concept, QuestionConcept, UserConceptRepeatQueue, UserConceptTheta
from database.custom_models import CustomSession, Topic, UserTopicMastery
from database.irt import update_theta
from services.concept_irt import ConceptIRT
from database.models import QuestionBank, User, UserResponse
from schemas.custom import (
    ConceptMasteryItem,
    ConceptMasteryResponse,
    ConceptOut,
    ConceptsResponse,
    CustomQuestionResponse,
    EndSessionResponse,
    GenerateCustomHintRequest,
    GenerateQuestionRequest,
    HintOut,
    StartSessionRequest,
    StartSessionResponse,
    SubmitAnswerRequest,
    SubmitAnswerResponse,
    TopicOut,
    TopicsResponse,
)
from routers.auth import get_current_user
from services.concept_service import ConceptDiscoveryService
from services.custom_service import (
    TOPIC_CATALOGUE,
    _refresh_mastery_percentage,
    get_canonical_topic_label,
    get_or_create_mastery,
    get_session,
    pick_fact_for_user,
    total_facts_for_topic,
)
from services.rate_limits import enforce_user_quota

logger = logging.getLogger(__name__)

custom_router = APIRouter(prefix="/api/custom", tags=["Custom Room"])

RECENT_QUESTION_WINDOW = 80
RECENT_QUESTION_TTL_SECONDS = 7 * 24 * 60 * 60
WRONG_ANSWER_REPEAT_DELAY = 30
SESSION_QUESTION_TTL_SECONDS = 2 * 60 * 60
SESSION_SIGNATURE_TTL_SECONDS = SESSION_QUESTION_TTL_SECONDS
RECENT_SIGNATURE_WINDOW = 80
RECENT_STYLE_WINDOW = 4
RECENT_SIGNATURE_TTL_SECONDS = 7 * 24 * 60 * 60
RECENT_STYLE_TTL_SECONDS = 2 * 60 * 60
GEOGRAPHY_BROADER_SCOPE_PROBABILITY = 0.06
CONCEPT_ROTATION_WINDOW = 3
CONCEPT_ROTATION_TTL_SECONDS = 2 * 60 * 60
CUSTOM_REQUIRE_RAG_GENERATION = True
CUSTOM_ROOM_PROGRESS_FALLBACK_TOTAL = max(1, int(QUIZ_QUESTIONS_PER_SESSION or 10))

GEOGRAPHY_SCOPE_KEYWORDS: dict[str, dict[str, list[str]]] = {
    "united states": {
        "primary": ["united states", "united states of america", "u.s.", "usa", "american", "washington dc", "mississippi", "rocky mountains"],
        "broader": ["north america", "north american", "americas"],
    },
    "brazil": {
        "primary": ["brazil", "brazilian", "brasilia", "amazon", "rio de janeiro"],
        "broader": ["south america", "south american", "latin america"],
    },
    "france": {
        "primary": ["france", "french", "paris", "seine", "lyon"],
        "broader": ["europe", "european", "european union", "eu"],
    },
    "egypt": {
        "primary": ["egypt", "egyptian", "cairo", "nile", "sinai"],
        "broader": ["north africa", "middle east", "africa"],
    },
    "china": {
        "primary": ["china", "chinese", "beijing", "yangtze", "tibet"],
        "broader": ["east asia", "asia", "asian"],
    },
    "australia": {
        "primary": ["australia", "australian", "canberra", "outback", "great barrier reef"],
        "broader": ["oceania", "pacific", "australasia"],
    },
    "united kingdom": {
        "primary": ["united kingdom", "uk", "britain", "british", "england", "scotland", "wales", "london"],
        "broader": ["europe", "european", "north atlantic"],
    },
    "india": {
        "primary": ["india", "indian", "new delhi", "ganges", "deccan"],
        "broader": ["south asia", "asia", "asian"],
    },
    "japan": {
        "primary": ["japan", "japanese", "tokyo", "honshu", "kyushu"],
        "broader": ["east asia", "asia", "pacific"],
    },
    "south africa": {
        "primary": ["south africa", "south african", "pretoria", "cape town", "johannesburg"],
        "broader": ["southern africa", "africa", "sub-saharan"],
    },
}

SIMPLE_GEOGRAPHY_FALLBACK_ANCHORS: dict[str, str] = {
    "united states": "Mississippi River",
    "brazil": "Amazon River",
    "france": "Seine River",
    "egypt": "Nile River",
    "china": "Yangtze River",
    "australia": "Great Barrier Reef",
    "united kingdom": "London",
    "india": "Ganges River",
    "japan": "Tokyo",
    "south africa": "Cape Town",
}

QUESTION_STYLE_POOLS: dict[str, list[str]] = {
    "history": [
        "chronology and turning points",
        "causes and triggers",
        "consequences and legacy",
        "key figures and motives",
        "institutions and reforms",
        "source interpretation and evidence",
    ],
    "geography": [
        "physical geography and landforms",
        "human geography and population patterns",
        "economy, trade, and resources",
        "culture, language, and society",
        "infrastructure and regional planning",
        "geopolitical relationships",
    ],
    "mixed": [
        "compare and connect two ideas",
        "identify the exception",
        "cause-and-effect reasoning",
        "timeline or sequence reasoning",
    ],
}

OFFLINE_GEOGRAPHY_QUESTION_BANK: dict[str, list[dict[str, str | list[str]]]] = {
    "china": [
        {
            "text": "Which city is the capital of China?",
            "correctAnswer": "Beijing",
            "options": ["Beijing", "Shanghai", "Shenzhen", "Guangzhou"],
            "explanation": "Beijing has been China's political center for centuries and remains the national capital today.",
        },
        {
            "text": "What is the longest river flowing through China?",
            "correctAnswer": "Yangtze River",
            "options": ["Yangtze River", "Yellow River", "Mekong River", "Amur River"],
            "explanation": "The Yangtze is the longest river in China and the third longest in the world.",
        },
        {
            "text": "Which plateau in southwestern China is known as the Roof of the World?",
            "correctAnswer": "Tibetan Plateau",
            "options": ["Tibetan Plateau", "Loess Plateau", "Yunnan-Guizhou Plateau", "Mongolian Plateau"],
            "explanation": "The Tibetan Plateau's extreme average elevation gives it the nickname Roof of the World.",
        },
        {
            "text": "Which desert extends across northern China and southern Mongolia?",
            "correctAnswer": "Gobi Desert",
            "options": ["Gobi Desert", "Taklamakan Desert", "Karakum Desert", "Thar Desert"],
            "explanation": "The Gobi covers a vast area of northern China and Mongolia and is mostly cold desert terrain.",
        },
        {
            "text": "China's eastern coastline opens directly to which sea?",
            "correctAnswer": "East China Sea",
            "options": ["East China Sea", "Arabian Sea", "Coral Sea", "Bering Sea"],
            "explanation": "The East China Sea borders China's eastern coast and connects to major shipping routes.",
        },
        {
            "text": "What is the most widely spoken official language in China?",
            "correctAnswer": "Mandarin Chinese",
            "options": ["Mandarin Chinese", "Cantonese", "Korean", "Japanese"],
            "explanation": "Standard Mandarin is China's official language and the most commonly spoken variety.",
        },
        {
            "text": "Which Chinese city is a major global financial hub near the Yangtze estuary?",
            "correctAnswer": "Shanghai",
            "options": ["Shanghai", "Xi'an", "Harbin", "Kunming"],
            "explanation": "Shanghai grew into one of the world's major financial and trade centers.",
        },
        {
            "text": "Which mountain range forms much of China's southwestern natural boundary?",
            "correctAnswer": "Himalayas",
            "options": ["Himalayas", "Alps", "Andes", "Carpathians"],
            "explanation": "The Himalayas form a major high-altitude barrier along China's southwestern frontier.",
        },
        {
            "text": "What is the currency used in China?",
            "correctAnswer": "Renminbi (yuan)",
            "options": ["Renminbi (yuan)", "Won", "Baht", "Rupee"],
            "explanation": "China's official currency is the renminbi, commonly referred to as the yuan.",
        },
        {
            "text": "Which large engineering landmark stretches across northern China?",
            "correctAnswer": "Great Wall",
            "options": ["Great Wall", "Suez Canal", "Panama Canal", "Golden Gate Bridge"],
            "explanation": "The Great Wall spans multiple provinces in northern China and is one of the country's best-known landmarks.",
        },
        {
            "text": "Which major river basin supports dense agriculture in eastern and central China?",
            "correctAnswer": "Yangtze basin",
            "options": ["Yangtze basin", "Nile basin", "Danube basin", "Rhine basin"],
            "explanation": "The Yangtze basin includes fertile plains and supports large populations and farming zones.",
        },
        {
            "text": "Which Chinese region is known for high elevations and proximity to the Himalayas?",
            "correctAnswer": "Tibet",
            "options": ["Tibet", "Hainan", "Macau", "Fujian"],
            "explanation": "Tibet's high-altitude geography links closely to the Tibetan Plateau and Himalayan systems.",
        },
    ]
}

CHINA_PROVINCE_CAPITALS: list[tuple[str, str]] = [
    ("Guangdong", "Guangzhou"),
    ("Sichuan", "Chengdu"),
    ("Hubei", "Wuhan"),
    ("Zhejiang", "Hangzhou"),
    ("Shandong", "Jinan"),
    ("Henan", "Zhengzhou"),
    ("Shaanxi", "Xi'an"),
    ("Liaoning", "Shenyang"),
    ("Fujian", "Fuzhou"),
    ("Jiangsu", "Nanjing"),
    ("Anhui", "Hefei"),
    ("Jiangxi", "Nanchang"),
    ("Yunnan", "Kunming"),
    ("Guizhou", "Guiyang"),
    ("Inner Mongolia", "Hohhot"),
    ("Xinjiang", "Urumqi"),
    ("Qinghai", "Xining"),
    ("Gansu", "Lanzhou"),
]

CHINA_RIVER_REGION_PAIRS: list[tuple[str, str]] = [
    ("Yangtze River", "Shanghai"),
    ("Yellow River", "Lanzhou"),
    ("Pearl River", "Guangzhou"),
    ("Hai River", "Tianjin"),
    ("Songhua River", "Harbin"),
    ("Yarlung Tsangpo", "Tibet"),
    ("Tarim River", "Xinjiang"),
    ("Mekong River", "Yunnan"),
]


def _ensure_user_match(target_user_id: str, current_user_id: str) -> None:
    try:
        target_uuid = uuid.UUID(str(target_user_id))
    except ValueError:
        raise HTTPException(422, "user_id must be a valid UUID")
    if str(target_uuid) != current_user_id:
        raise HTTPException(403, "You are not allowed to access this user data")


def _topic_family(topic_label: str) -> str:
    t = (topic_label or "").lower().strip()
    if t.startswith("history"):
        return "history"
    if t.startswith("geography"):
        return "geography"
    return "mixed"


def _topic_detail(topic_label: str) -> str:
    return topic_label.split(" - ", 1)[-1].strip().lower()


def _topic_keywords(topic_label: str) -> list[str]:
    detail = _topic_detail(topic_label)
    family = _topic_family(topic_label)

    if family == "history":
        if "world war ii" in detail:
            return ["world war ii", "wwii", "second world war", "axis", "allied", "d-day"]
        if "world war i" in detail:
            return ["world war i", "wwi", "great war", "trench", "1914", "1918"]
        if "cold war" in detail:
            return ["cold war", "berlin wall", "nato", "soviet", "containment"]
        if "ancient rome" in detail:
            return ["rome", "roman", "caesar", "republic", "empire"]
        if "french revolution" in detail:
            return ["french revolution", "bastille", "robespierre", "jacobin", "louis xvi"]
        if "industrial revolution" in detail:
            return ["industrial revolution", "steam", "factory", "textile", "coal"]
    if family == "geography" and detail:
        scope = GEOGRAPHY_SCOPE_KEYWORDS.get(detail)
        if scope:
            return list(dict.fromkeys([detail, *scope["primary"]]))
        return [detail]
    return []


def _broader_geography_keywords(topic_label: str) -> list[str]:
    detail = _topic_detail(topic_label)
    if _topic_family(topic_label) != "geography":
        return []
    scope = GEOGRAPHY_SCOPE_KEYWORDS.get(detail)
    if not scope:
        return []
    return list(dict.fromkeys(scope["broader"]))


def _concept_matches_topic_label(concept_name: str, topic_label: str) -> bool:
    detail = _topic_detail(topic_label)
    concept = (concept_name or "").lower().strip()
    if not detail:
        return True
    return detail in concept or concept in detail


def _mastery_level(theta: float, responses: int) -> str:
    if theta < -0.75:
        return "BEGINNER"
    if theta < 0.75:
        return "LEARNING"
    if responses >= 12 and theta >= 1.25:
        return "ADVANCED"
    return "PROFICIENT"


def _difficulty_from_theta(theta: Optional[float]) -> int:
    if theta is None:
        return 3
    if theta <= -0.8:
        return 2
    if theta <= 0.4:
        return 3
    if theta <= 1.2:
        return 4
    return 5


def _custom_generation_rules(
    topic_label: str,
    concept_name: Optional[str],
    generation_style: str,
    recent_signatures: list[str],
    allow_broader_geography_scope: bool,
) -> str:
    family = _topic_family(topic_label)
    detail = topic_label.split(" - ", 1)[-1].strip()
    rules = [
        "Do not ask users to choose between subtopics. Ask a direct MCQ.",
        "If topic is specific, stay in that exact topic and avoid sibling topics.",
        "Avoid repetitive border-country style questions unless the topic is geography.",
        f"Use this question style: {generation_style}.",
    ]
    if family == "history":
        rules.append(
            "Rotate history question types across chronology, causes, consequences, key figures, institutions, reforms, and source interpretation."
        )
    if family == "geography" and " - " in topic_label:
        if allow_broader_geography_scope:
            rules.append(
                "Primarily keep the question anchored to the selected country. Broader region context is allowed only if still anchored to that country."
            )
        else:
            rules.append(
                "Keep the question strictly anchored to the selected country. Do not switch to another country or continent-wide trivia."
            )
    if "french revolution" in detail.lower():
        rules.append(
            "For French Revolution, focus on phases, actors, ideas, policies, and outcomes. Avoid generic geography framing."
        )
    if concept_name:
        rules.append(f"Question must directly test this concept: {concept_name}.")

    if recent_signatures:
        rules.append("Do not repeat these recent angles:")
        for sig in recent_signatures[:6]:
            rules.append(f"recent angle: {sig}")

    return "\n".join(f"- {rule}" for rule in rules)


async def _get_db(request: Request):
    factory = getattr(request.app.state, "db_session_factory", None)
    if factory is None:
        raise HTTPException(503, "Database not available")
    async with factory() as db:
        yield db


async def _get_llm(request: Request):
    llm = getattr(request.app.state, "llm_client", None)
    if llm is None:
        raise HTTPException(503, "LLM not available")
    return llm


async def _topic_theta_average(db, user_id: uuid.UUID, topic_family: str) -> Optional[float]:
    stmt = (
        select(func.avg(UserConceptTheta.theta))
        .join(Concept, Concept.id == UserConceptTheta.concept_id)
        .where(
            UserConceptTheta.user_id == user_id,
            Concept.topic == topic_family,
        )
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _topic_exposure_count(db, user_id: uuid.UUID, topic_family: str) -> int:
    stmt = (
        select(func.coalesce(func.sum(UserConceptTheta.exposure_count), 0))
        .join(Concept, Concept.id == UserConceptTheta.concept_id)
        .where(
            UserConceptTheta.user_id == user_id,
            Concept.topic == topic_family,
        )
    )
    return int((await db.execute(stmt)).scalar_one() or 0)


async def _target_difficulty_for_user(
    db,
    user_id: uuid.UUID,
    topic_family: str,
    selected_concept: Optional[Concept],
) -> int:
    theta = None
    if selected_concept is not None:
        concept_stmt = select(UserConceptTheta.theta).where(
            UserConceptTheta.user_id == user_id,
            UserConceptTheta.concept_id == selected_concept.id,
        )
        theta = (await db.execute(concept_stmt)).scalar_one_or_none()
    if theta is None:
        theta = await _topic_theta_average(db, user_id, topic_family)
    return _difficulty_from_theta(theta)


def _topic_cache_token(topic_label: str) -> str:
    token = _topic_detail(topic_label).replace(" ", "-").replace("/", "-")
    return token or "general"


def _concept_cache_token(concept_id: Optional[str]) -> str:
    return str(concept_id) if concept_id else "none"


def _recent_question_key(user_id: uuid.UUID, topic_label: str, concept_id: Optional[str] = None) -> str:
    family = _topic_family(topic_label)
    detail = _topic_cache_token(topic_label)
    concept = _concept_cache_token(concept_id)
    return f"custom_recent:{user_id}:{family}:{detail}:{concept}"


def _recent_signature_key(user_id: uuid.UUID, topic_label: str, concept_id: Optional[str] = None) -> str:
    family = _topic_family(topic_label)
    detail = _topic_cache_token(topic_label)
    concept = _concept_cache_token(concept_id)
    return f"custom_recent_signatures:{user_id}:{family}:{detail}:{concept}"


def _style_rotation_key(session_id: str) -> str:
    return f"custom_style_rotation:{session_id}"


def _focus_concept_rotation_key(session_id: str) -> str:
    return f"custom_focus_concepts:{session_id}"


async def _read_cached_list(request: Request, key: str, fallback_attr: str, window: int) -> list[str]:
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is not None:
        try:
            values = await redis_client.lrange(key, 0, window - 1)
            return [str(v) for v in values]
        except Exception as exc:
            logger.warning("cached-list redis read failed for %s: %s", key, exc)

    fallback = getattr(request.app.state, fallback_attr, None)
    if fallback is None:
        fallback = {}
        setattr(request.app.state, fallback_attr, fallback)
    return [str(v) for v in fallback.get(key, [])]


async def _write_cached_list(
    request: Request,
    key: str,
    value: str,
    fallback_attr: str,
    window: int,
    ttl_seconds: int,
) -> None:
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is not None:
        try:
            await redis_client.lrem(key, 0, value)
            await redis_client.lpush(key, value)
            await redis_client.ltrim(key, 0, window - 1)
            await redis_client.expire(key, ttl_seconds)
            return
        except Exception as exc:
            logger.warning("cached-list redis write failed for %s: %s", key, exc)

    fallback = getattr(request.app.state, fallback_attr, None)
    if fallback is None:
        fallback = {}
        setattr(request.app.state, fallback_attr, fallback)

    current = [str(v) for v in fallback.get(key, []) if str(v) != value]
    current.insert(0, value)
    fallback[key] = current[:window]


async def _get_recent_question_ids(
    request: Request,
    user_id: uuid.UUID,
    topic_label: str,
    concept_id: Optional[str] = None,
) -> set[uuid.UUID]:
    key = _recent_question_key(user_id, topic_label, concept_id)
    values = await _read_cached_list(request, key, "custom_recent_questions", RECENT_QUESTION_WINDOW)

    out: set[uuid.UUID] = set()
    for raw in values:
        try:
            out.add(uuid.UUID(str(raw)))
        except ValueError:
            continue
    return out


async def _get_recent_answered_question_ids(
    db,
    user_id: uuid.UUID,
    topic_label: str,
    window: Optional[int] = None,
) -> set[uuid.UUID]:
    topic_family = _topic_family(topic_label)
    stmt = (
        select(UserResponse.question_id)
        .where(
            UserResponse.user_id == user_id,
            func.lower(UserResponse.topic) == topic_family,
        )
        .order_by(UserResponse.created_at.desc())
    )
    if window and window > 0:
        stmt = stmt.limit(window)
    rows = (await db.execute(stmt)).scalars().all()

    out: set[uuid.UUID] = set()
    for raw in rows:
        try:
            out.add(uuid.UUID(str(raw)))
        except ValueError:
            continue
    return out


async def _remember_served_question(
    request: Request,
    user_id: uuid.UUID,
    topic_label: str,
    question_id: uuid.UUID,
    concept_id: Optional[str] = None,
) -> None:
    key = _recent_question_key(user_id, topic_label, concept_id)
    await _write_cached_list(
        request,
        key,
        str(question_id),
        "custom_recent_questions",
        RECENT_QUESTION_WINDOW,
        RECENT_QUESTION_TTL_SECONDS,
    )


def _question_signature(question_text: str, explanation: str) -> str:
    _ = explanation
    normalized = " ".join((question_text or "").strip().lower().split())
    return normalized or "question style already used"


async def _get_recent_signatures(
    request: Request,
    user_id: uuid.UUID,
    topic_label: str,
    concept_id: Optional[str] = None,
) -> list[str]:
    key = _recent_signature_key(user_id, topic_label, concept_id)
    return await _read_cached_list(request, key, "custom_recent_signatures", RECENT_SIGNATURE_WINDOW)


async def _get_recent_answered_signatures(
    db,
    user_id: uuid.UUID,
    topic_label: str,
    window: Optional[int] = None,
) -> list[str]:
    topic_family = _topic_family(topic_label)
    stmt = (
        select(QuestionBank.question_text, QuestionBank.explanation)
        .join(UserResponse, UserResponse.question_id == QuestionBank.id)
        .where(
            UserResponse.user_id == user_id,
            func.lower(UserResponse.topic) == topic_family,
        )
        .order_by(UserResponse.created_at.desc())
    )
    if window and window > 0:
        stmt = stmt.limit(window)
    rows = (await db.execute(stmt)).all()

    out: list[str] = []
    for question_text, explanation in rows:
        sig = _question_signature(str(question_text or ""), str(explanation or ""))
        if sig:
            out.append(sig)
    return out


async def _remember_question_signature(
    request: Request,
    user_id: uuid.UUID,
    topic_label: str,
    question_text: str,
    explanation: str,
    concept_id: Optional[str] = None,
) -> None:
    key = _recent_signature_key(user_id, topic_label, concept_id)
    await _write_cached_list(
        request,
        key,
        _question_signature(question_text, explanation),
        "custom_recent_signatures",
        RECENT_SIGNATURE_WINDOW,
        RECENT_SIGNATURE_TTL_SECONDS,
    )


async def _pick_generation_style(request: Request, session_id: str, topic_family: str) -> str:
    key = _style_rotation_key(session_id)
    recent_styles = await _read_cached_list(request, key, "custom_generation_styles", RECENT_STYLE_WINDOW)
    pool = QUESTION_STYLE_POOLS.get(topic_family, QUESTION_STYLE_POOLS["mixed"])

    candidates = [style for style in pool if style not in set(recent_styles[:2])]
    if not candidates:
        candidates = pool

    picked = random.choice(candidates)
    await _write_cached_list(
        request,
        key,
        picked,
        "custom_generation_styles",
        RECENT_STYLE_WINDOW,
        RECENT_STYLE_TTL_SECONDS,
    )
    return picked


async def _get_recent_focus_concepts(request: Request, session_id: str) -> list[str]:
    return await _read_cached_list(
        request,
        _focus_concept_rotation_key(session_id),
        "custom_focus_concepts",
        CONCEPT_ROTATION_WINDOW,
    )


async def _remember_focus_concept(request: Request, session_id: str, concept_id: uuid.UUID) -> None:
    await _write_cached_list(
        request,
        _focus_concept_rotation_key(session_id),
        str(concept_id),
        "custom_focus_concepts",
        CONCEPT_ROTATION_WINDOW,
        CONCEPT_ROTATION_TTL_SECONDS,
    )


def _concept_matches_scope(
    concept: Concept,
    topic_label: str,
    primary_keywords: list[str],
    broader_keywords: list[str],
    allow_broader_scope: bool,
) -> bool:
    if " - " not in topic_label:
        return True

    blob = " ".join(
        [
            str(concept.name or "").lower(),
            str(concept.description or "").lower(),
        ]
    )
    detail = _topic_detail(topic_label)
    if detail and detail in blob:
        return True
    if primary_keywords and any(kw in blob for kw in primary_keywords):
        return True
    if allow_broader_scope and broader_keywords and any(kw in blob for kw in broader_keywords):
        return True
    return False


async def _pick_dynamic_focus_concept(
    db,
    request: Request,
    user_id: uuid.UUID,
    session_id: str,
    topic_label: str,
    primary_keywords: list[str],
    broader_keywords: list[str],
    allow_broader_scope: bool,
) -> Optional[Concept]:
    family = _topic_family(topic_label)
    if family not in {"history", "geography", "mixed"}:
        return None

    candidates = (
        await db.execute(select(Concept).where(Concept.topic == family).order_by(Concept.created_at.desc()))
    ).scalars().all()

    if " - " in topic_label:
        candidates = [
            c
            for c in candidates
            if _concept_matches_scope(
                concept=c,
                topic_label=topic_label,
                primary_keywords=primary_keywords,
                broader_keywords=broader_keywords,
                allow_broader_scope=allow_broader_scope,
            )
        ]

    if not candidates and " - " in topic_label:
        candidates = await ConceptDiscoveryService.ensure_topic_seed_concepts(db, topic_label, max_new=4)

    if not candidates:
        return None

    concept_ids = [c.id for c in candidates]
    theta_rows = (
        await db.execute(
            select(UserConceptTheta).where(
                UserConceptTheta.user_id == user_id,
                UserConceptTheta.concept_id.in_(concept_ids),
            )
        )
    ).scalars().all()
    theta_map = {row.concept_id: row for row in theta_rows}

    recent_focus_ids = set(await _get_recent_focus_concepts(request, session_id))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    scored: list[tuple[float, Concept]] = []

    for concept in candidates:
        theta_row = theta_map.get(concept.id)
        exposure = int(theta_row.exposure_count) if theta_row else 0
        last_seen = theta_row.last_played_at if theta_row and theta_row.last_played_at else None
        staleness_days = (now - last_seen).days if last_seen else 365

        novelty_score = 1.0 / (1.0 + exposure)
        staleness_score = min(staleness_days / 30.0, 1.0)
        recent_penalty = 0.45 if str(concept.id) in recent_focus_ids else 0.0
        jitter = random.uniform(0.0, 0.05)
        score = (0.65 * novelty_score) + (0.30 * staleness_score) + jitter - recent_penalty
        scored.append((score, concept))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_candidates = [concept for _, concept in scored[:3]]
    chosen = random.choice(top_candidates if top_candidates else [scored[0][1]])
    await _remember_focus_concept(request, session_id, chosen.id)
    return chosen


def _build_keyword_filter(keywords: list[str]):
    normalized = []
    seen = set()
    for raw in keywords:
        kw = str(raw).strip().lower()
        if not kw or kw in seen:
            continue
        seen.add(kw)
        normalized.append(kw)

    if not normalized:
        return None

    return or_(
        *[
            or_(
                func.lower(QuestionBank.question_text).like(f"%{kw}%"),
                func.lower(QuestionBank.explanation).like(f"%{kw}%"),
                func.lower(QuestionBank.correct_answer).like(f"%{kw}%"),
            )
            for kw in normalized
        ]
    )


def _question_matches_keywords(question_row: QuestionBank, keywords: list[str]) -> bool:
    if not keywords:
        return True
    blob = " ".join(
        [
            str(question_row.question_text or "").lower(),
            str(question_row.explanation or "").lower(),
            str(question_row.correct_answer or "").lower(),
        ]
    )
    return any(str(kw).strip().lower() in blob for kw in keywords if str(kw).strip())


def _generated_payload_matches_keywords(
    question_text: str,
    explanation: str,
    options: list[str],
    keywords: list[str],
) -> bool:
    if not keywords:
        return True
    blob = " ".join(
        [
            str(question_text or "").lower(),
            str(explanation or "").lower(),
            " ".join(str(opt).lower() for opt in options),
        ]
    )
    return any(str(kw).strip().lower() in blob for kw in keywords if str(kw).strip())


def _normalize_rag_mcq_payload(payload: Any) -> Optional[dict[str, Any]]:
    if not isinstance(payload, dict):
        return None

    # LLM-shaped payload
    if isinstance(payload.get("text"), str) and isinstance(payload.get("options"), list):
        text = str(payload.get("text", "")).strip()
        options = [str(v).strip() for v in payload.get("options", []) if str(v).strip()]
        correct = str(payload.get("correctAnswer", "")).strip()
        explanation = str(payload.get("explanation", "")).strip()
        if text and len(options) >= 2:
            if not correct:
                correct = options[0]
            return {
                "text": text,
                "options": options,
                "correctAnswer": correct,
                "explanation": explanation,
            }

    # HF-shaped payload
    if isinstance(payload.get("question"), str) and isinstance(payload.get("options"), list):
        text = str(payload.get("question", "")).strip()
        options = [str(v).strip() for v in payload.get("options", []) if str(v).strip()]
        correct = str(payload.get("correct_answer", "")).strip()
        explanation = str(payload.get("explanation", "")).strip()
        if text and len(options) >= 2:
            if not correct:
                correct = options[0]
            return {
                "text": text,
                "options": options,
                "correctAnswer": correct,
                "explanation": explanation,
            }

    return None


async def _offline_geography_payload(
    topic_label: str,
    recent_signatures: list[str],
    request: Request,
    session_id: str,
) -> Optional[dict[str, str | list[str]]]:
    if _topic_family(topic_label) != "geography" or " - " not in topic_label:
        return None

    detail = _topic_detail(topic_label)
    bank = list(OFFLINE_GEOGRAPHY_QUESTION_BANK.get(detail) or [])

    if detail == "china":
        bank.extend(_offline_china_dynamic_candidates())

    if not bank:
        return None

    recent_set = {str(sig).strip().lower() for sig in recent_signatures if str(sig).strip()}
    random.shuffle(bank)

    chosen = None
    for item in bank:
        signature = _question_signature(str(item.get("text", "")), str(item.get("explanation", ""))).lower()
        if signature and signature in recent_set:
            continue
        if signature and await _session_has_signature(request, session_id, signature):
            continue
        chosen = item
        break

    if chosen is None:
        return None

    text = str(chosen.get("text", "")).strip()
    correct = str(chosen.get("correctAnswer", "")).strip()
    explanation = str(chosen.get("explanation", "")).strip()
    options = [str(opt).strip() for opt in (chosen.get("options") or []) if str(opt).strip()]
    random.shuffle(options)

    if correct and correct not in options:
        options.append(correct)

    return {
        "text": text,
        "correctAnswer": correct,
        "explanation": explanation,
        "options": options,
    }


def _offline_china_dynamic_candidates() -> list[dict[str, str | list[str]]]:
    candidates: list[dict[str, str | list[str]]] = []

    all_capitals = [capital for _, capital in CHINA_PROVINCE_CAPITALS]
    for province, capital in CHINA_PROVINCE_CAPITALS:
        distractors = [city for city in all_capitals if city != capital]
        if len(distractors) < 3:
            continue
        random.shuffle(distractors)
        options = [capital, distractors[0], distractors[1], distractors[2]]
        random.shuffle(options)
        candidates.append(
            {
                "text": f"What is the provincial capital of {province} in China?",
                "correctAnswer": capital,
                "options": options,
                "explanation": f"{capital} is the provincial capital of {province} in China.",
            }
        )

    river_names = [name for name, _ in CHINA_RIVER_REGION_PAIRS]
    for river, region in CHINA_RIVER_REGION_PAIRS:
        distractors = [name for name in river_names if name != river]
        if len(distractors) < 3:
            continue
        random.shuffle(distractors)
        options = [river, distractors[0], distractors[1], distractors[2]]
        random.shuffle(options)
        candidates.append(
            {
                "text": f"Which major river flows through or near {region} in China?",
                "correctAnswer": river,
                "options": options,
                "explanation": f"{river} is a major river system associated with {region} in China.",
            }
        )

    return candidates


def _session_question_key(session_id: str) -> str:
    return f"custom_session_questions:{session_id}"


async def _remember_session_question(request: Request, session_id: str, question_id: uuid.UUID) -> None:
    key = _session_question_key(session_id)
    qid = str(question_id)
    redis_client = getattr(request.app.state, "redis", None)

    if redis_client is not None:
        try:
            await redis_client.sadd(key, qid)
            await redis_client.expire(key, SESSION_QUESTION_TTL_SECONDS)
            return
        except Exception as exc:
            logger.warning("session-question redis write failed: %s", exc)

    fallback = getattr(request.app.state, "custom_session_questions", None)
    if fallback is None:
        fallback = {}
        request.app.state.custom_session_questions = fallback

    values = set(fallback.get(key, []))
    values.add(qid)
    fallback[key] = list(values)


async def _session_has_question(request: Request, session_id: str, question_id: uuid.UUID) -> bool:
    key = _session_question_key(session_id)
    qid = str(question_id)
    redis_client = getattr(request.app.state, "redis", None)

    if redis_client is not None:
        try:
            return bool(await redis_client.sismember(key, qid))
        except Exception as exc:
            logger.warning("session-question redis read failed: %s", exc)

    fallback = getattr(request.app.state, "custom_session_questions", None)
    if fallback is None:
        return False
    return qid in set(fallback.get(key, []))


def _session_signature_key(session_id: str) -> str:
    return f"custom_session_signatures:{session_id}"


async def _remember_session_signature(request: Request, session_id: str, signature: str) -> None:
    normalized = str(signature or "").strip().lower()
    if not normalized:
        return

    key = _session_signature_key(session_id)
    redis_client = getattr(request.app.state, "redis", None)

    if redis_client is not None:
        try:
            await redis_client.sadd(key, normalized)
            await redis_client.expire(key, SESSION_SIGNATURE_TTL_SECONDS)
            return
        except Exception as exc:
            logger.warning("session-signature redis write failed: %s", exc)

    fallback = getattr(request.app.state, "custom_session_signatures", None)
    if fallback is None:
        fallback = {}
        request.app.state.custom_session_signatures = fallback

    values = set(fallback.get(key, []))
    values.add(normalized)
    fallback[key] = list(values)


async def _session_has_signature(request: Request, session_id: str, signature: str) -> bool:
    normalized = str(signature or "").strip().lower()
    if not normalized:
        return False

    key = _session_signature_key(session_id)
    redis_client = getattr(request.app.state, "redis", None)

    if redis_client is not None:
        try:
            return bool(await redis_client.sismember(key, normalized))
        except Exception as exc:
            logger.warning("session-signature redis read failed: %s", exc)

    fallback = getattr(request.app.state, "custom_session_signatures", None)
    if fallback is None:
        return False
    return normalized in set(fallback.get(key, []))


def _parse_options(raw_options_json: str) -> list[str]:
    try:
        parsed = json.loads(raw_options_json)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Invalid options_json encountered in question_bank: %s", exc)
        return []

    if not isinstance(parsed, list):
        return []

    out = []
    seen = set()
    for val in parsed:
        text = str(val).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


# Deduplicate and pad generated options so every MCQ has four choices.
def _normalize_generated_options(options: list[str], correct_answer: str) -> list[str]:
    deduped = []
    seen = set()
    for val in options:
        text = str(val).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(text)

    if correct_answer and correct_answer.lower() not in seen:
        deduped.append(correct_answer)

    fillers = [
        "Insufficient context",
        "None of the listed options",
        "All options are incorrect",
        "Cannot be determined",
    ]
    while len(deduped) < 4:
        deduped.append(fillers[len(deduped) % len(fillers)])
    return deduped[:4]


def _custom_level(level: int | None) -> int:
    try:
        return max(1, min(5, int(level or 3)))
    except Exception:
        return 3


def _custom_options_for_level(options: list[str], correct_answer: str, level: int) -> list[str]:
    """Force Custom Room answer shape to match the chosen level.

    Level 1 is easy two-option MCQ. Levels 2-4 are 4-option MCQ.
    Level 5 is typed/free-text, so no options are returned.
    """
    level = _custom_level(level)
    if level == 5:
        return []
    normalized = _normalize_generated_options(options, correct_answer)
    if level == 1:
        correct = str(correct_answer or "").strip()
        out = []
        seen = set()
        if correct:
            out.append(correct)
            seen.add(correct.lower())
        for opt in normalized:
            text = str(opt).strip()
            if not text or text.lower() in seen:
                continue
            out.append(text)
            seen.add(text.lower())
            if len(out) == 2:
                break
        while len(out) < 2:
            filler = "None of the listed options" if len(out) == 1 else "Cannot be determined"
            if filler.lower() not in seen:
                out.append(filler)
                seen.add(filler.lower())
            else:
                out.append(f"Alternative {len(out) + 1}")
        random.shuffle(out)
        return out[:2]
    return normalized[:4]


# Build baseline LLM generation parameters for simple-mode questions.
def _custom_simple_generation_payload(topic_label: str, fact_content: str, level: int = 3) -> dict[str, Any]:
    return {
        "context": fact_content,
        "topic": topic_label,
        "difficulty": _custom_level(level),
        "strategy": "direct",
        "user_accuracy": 0.5,
    }


# Create a deterministic geography fallback question when generation is constrained.
def _simple_geography_scope_fallback_payload(topic_label: str) -> Optional[dict[str, Any]]:
    if _topic_family(topic_label) != "geography" or " - " not in topic_label:
        return None

    detail = _topic_detail(topic_label)
    country_name = topic_label.split(" - ", 1)[-1].strip()
    anchor = SIMPLE_GEOGRAPHY_FALLBACK_ANCHORS.get(detail)
    if not anchor:
        scope = GEOGRAPHY_SCOPE_KEYWORDS.get(detail, {})
        primary = [str(v).strip() for v in scope.get("primary", []) if str(v).strip()]
        anchor = next((kw for kw in primary if kw.lower() != detail), country_name)

    distractors = [
        value
        for key, value in SIMPLE_GEOGRAPHY_FALLBACK_ANCHORS.items()
        if key != detail
    ]
    random.shuffle(distractors)
    options = [anchor, *distractors[:3]]
    random.shuffle(options)

    return {
        "text": f"Which option is most directly associated with the geography of {country_name}?",
        "options": options,
        "correctAnswer": anchor,
        "explanation": (
            f"This question is intentionally anchored to {country_name}; "
            f"{anchor} is a core association used for country-specific geography practice."
        ),
    }


# Toggle concept-tracking pathways depending on custom-room mode.
def _custom_concept_tracking_enabled() -> bool:
    return not CUSTOM_ROOM_SIMPLE_MODE


def _custom_progress_total(total_in_db: int, catalogue_total: int = 0) -> int:
    if int(total_in_db or 0) > 0:
        return int(total_in_db)
    if int(catalogue_total or 0) > 0:
        return int(catalogue_total)
    return CUSTOM_ROOM_PROGRESS_FALLBACK_TOTAL


# Resolve the concept id tied to a served question for response payloads.
async def _resolve_concept_id_for_question(db, question_id: uuid.UUID, selected_concept: Optional[Concept]) -> Optional[str]:
    if selected_concept is not None:
        return str(selected_concept.id)
    linked = await db.execute(
        select(QuestionConcept)
        .where(QuestionConcept.question_id == question_id)
        .order_by(QuestionConcept.is_primary.desc())
        .limit(1)
    )
    link_row = linked.scalar_one_or_none()
    return str(link_row.concept_id) if link_row else None


@custom_router.get("/topics", response_model=TopicsResponse)
@limiter.limit("60/minute")
# Return the configured custom-room topic catalogue.
async def list_topics(request: Request, current=Depends(get_current_user)):
    _ = current
    by_slug: dict[str, TopicOut] = {
        t["slug"]: TopicOut(
            type=t["type"],
            slug=t["slug"],
            name=t["name"],
            description=t["description"],
            total_facts=t["total_facts"],
        )
        for t in TOPIC_CATALOGUE
    }
    async for db in _get_db(request):
        rows = await db.execute(select(Topic).order_by(Topic.type, Topic.name))
        for topic in rows.scalars().all():
            if not topic.is_active:
                by_slug.pop(str(topic.slug), None)
            else:
                by_slug[str(topic.slug)] = TopicOut(
                    type=topic.type,
                    slug=topic.slug,
                    name=topic.name,
                    description=topic.description or "",
                    total_facts=int(topic.total_facts_count or 0),
                )
        return TopicsResponse(topics=list(by_slug.values()))


@custom_router.get("/concepts/{topic}", response_model=ConceptsResponse)
@limiter.limit("60/minute")
# Return concepts available for a selected topic family.
async def list_concepts_for_topic(topic: str, request: Request, current=Depends(get_current_user)):
    _ = current
    async for db in _get_db(request):
        rows = await db.execute(
            select(Concept).where(Concept.topic == _topic_family(topic)).order_by(Concept.name)
        )
        concepts = rows.scalars().all()
        return ConceptsResponse(
            concepts=[
                ConceptOut(
                    id=str(c.id),
                    name=c.name,
                    topic=c.topic,
                    scope=c.scope,
                    description=c.description,
                )
                for c in concepts
            ]
        )


@custom_router.get("/user/{user_id}/concept-mastery", response_model=ConceptMasteryResponse)
@limiter.limit("60/minute")
# Return per-concept mastery metrics for the authenticated user.
async def get_user_concept_mastery(user_id: str, request: Request, current=Depends(get_current_user)):
    user, _ = current
    _ensure_user_match(user_id, str(user.id))

    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(422, "user_id must be a valid UUID")

    async for db in _get_db(request):
        rows = await db.execute(
            select(UserConceptTheta, Concept)
            .join(Concept, UserConceptTheta.concept_id == Concept.id)
            .where(UserConceptTheta.user_id == user_uuid)
            .order_by(Concept.topic, UserConceptTheta.theta.desc())
        )

        items = []
        for theta_row, concept in rows.all():
            items.append(
                ConceptMasteryItem(
                    concept_id=str(concept.id),
                    concept=concept.name,
                    topic=concept.topic,
                    scope=concept.scope,
                    theta=theta_row.theta,
                    response_count=theta_row.response_count,
                    mastery_level=theta_row.mastery_level,
                    exposure_count=theta_row.exposure_count,
                )
            )
        return ConceptMasteryResponse(user_id=user_id, concepts=items)


@custom_router.post("/start-session", response_model=StartSessionResponse)
@limiter.limit("20/minute")
# Start a new custom learning session and initialize progress state.
async def start_session(body: StartSessionRequest, request: Request, current=Depends(get_current_user)):
    user, _ = current
    await enforce_user_quota(request, user.id, "custom_start", limit=60, window_seconds=3600)
    _ensure_user_match(body.user_id, str(user.id))
    body.topic = get_canonical_topic_label(body.topic)

    try:
        user_uuid = uuid.UUID(str(body.user_id))
    except (ValueError, AttributeError):
        raise HTTPException(422, f"user_id must be a valid UUID, got: {body.user_id!r}")

    async for db in _get_db(request):
        selected_concept = None
        if body.concept_id:
            try:
                concept_uuid = uuid.UUID(body.concept_id)
            except ValueError:
                raise HTTPException(422, "concept_id must be a valid UUID")

            selected_concept = await db.get(Concept, concept_uuid)
            if selected_concept is None:
                raise HTTPException(404, "Concept not found")
            if not _concept_matches_topic_label(selected_concept.name, body.topic):
                selected_concept = None

        if " - " in body.topic:
            try:
                await ConceptDiscoveryService.ensure_topic_seed_concepts(db, body.topic, max_new=4)
            except Exception as exc:
                logger.warning("topic concept seeding failed for %s: %s", body.topic, exc)

        user_row = (await db.execute(select(User).where(User.id == user_uuid))).scalar_one_or_none()
        if user_row is None:
            raise HTTPException(404, "User not found")

        topic_name = body.topic.split(" - ", 1)[-1]
        total_from_catalogue = next(
            (t["total_facts"] for t in TOPIC_CATALOGUE if t["name"] == topic_name),
            0,
        )
        total_in_db = await total_facts_for_topic(db, body.topic)
        total = _custom_progress_total(total_in_db, total_from_catalogue)

        mastery = await get_or_create_mastery(db, user_uuid, body.topic, total)

        session = CustomSession(
            user_id=user_uuid,
            topic=body.topic,
            started_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)

        return StartSessionResponse(
            session_id=str(session.id),
            topic=body.topic,
            concept_id=str(selected_concept.id) if selected_concept else None,
            progress_percentage=mastery.completion_percentage,
            total_questions_estimate=total,
        )


@custom_router.post("/generate-question", response_model=CustomQuestionResponse)
@limiter.limit("40/minute")
# Select or generate the next custom-room question for the active session.
async def generate_question(body: GenerateQuestionRequest, request: Request, current=Depends(get_current_user)):
    user, _ = current
    await enforce_user_quota(request, user.id, "custom_generate", limit=160, window_seconds=3600)
    body.topic = get_canonical_topic_label(body.topic)
    async for db in _get_db(request):
        selected_concept = None
        if body.concept_id:
            try:
                concept_uuid = uuid.UUID(body.concept_id)
            except ValueError:
                raise HTTPException(422, "concept_id must be a valid UUID")
            selected_concept = await db.get(Concept, concept_uuid)
            if selected_concept is None:
                raise HTTPException(404, "Concept not found")
            if not _concept_matches_topic_label(selected_concept.name, body.topic):
                selected_concept = None

        session = await get_session(db, body.session_id)
        if session is None:
            raise HTTPException(404, "Session not found")
        if str(session.user_id) != str(user.id):
            raise HTTPException(403, "You are not allowed to use this session")
        if session.ended_at is not None:
            raise HTTPException(400, "Session already ended")

        custom_level = _custom_level(getattr(body, "level", 3))

        if CUSTOM_ROOM_SIMPLE_MODE:
            topic_family = _topic_family(body.topic)
            strict_topic = " - " in body.topic
            primary_keywords = _topic_keywords(body.topic)
            enforce_geography_scope = bool(
                strict_topic and topic_family == "geography" and primary_keywords
            )

            fact = await pick_fact_for_user(db, session.user_id, body.topic)
            if fact is None:
                fact_content = f"a key concept related to {body.topic}"
                fact_id_to_use = None
            else:
                fact_content = fact.content
                fact_id_to_use = str(fact.id)
                fact.total_questions_generated += 1

            llm = await _get_llm(request)
            data: Optional[dict[str, Any]] = None
            question_source = "custom_llm_simple"
            question_id: Optional[uuid.UUID] = None
            gov_decision = None
            for _ in range(4):
                try:
                    candidate = await llm.generate_mcq(
                        **_custom_simple_generation_payload(
                            topic_label=body.topic,
                            fact_content=fact_content,
            level=custom_level,
                        )
                    )
                except Exception as exc:
                    logger.warning("Custom simple-mode generate_mcq attempt failed: %s", exc)
                    continue

                if candidate and isinstance(candidate.get("options"), list) and str(candidate.get("text", "")).strip():
                    raw_opts = [str(v).strip() for v in candidate.get("options", []) if str(v).strip()]
                    raw_text = str(candidate.get("text", "")).strip()
                    raw_expl = str(candidate.get("explanation", "")).strip()
                    if enforce_geography_scope and not _generated_payload_matches_keywords(
                        question_text=raw_text,
                        explanation="",
                        options=raw_opts,
                        keywords=primary_keywords,
                    ):
                        continue

                    # Governance: reject blocked candidates before persisting.
                    candidate_id = uuid.uuid4()
                    candidate_correct = str(candidate.get("correctAnswer", "")).strip() or (
                        raw_opts[0] if raw_opts else ""
                    )
                    decision = None
                    try:
                        from services.governance_service import GovernanceService

                        decision = await GovernanceService.evaluate_candidate(
                            db,
                            question_id=candidate_id,
                            room="custom",
                            action="persist",
                            topic=body.topic,
                            question_text=raw_text,
                            correct_answer=candidate_correct,
                            explanation=raw_expl,
                            options=raw_opts,
                        )
                        if decision is not None and not decision.approved:
                            continue
                    except Exception as exc:
                        logger.warning("Custom governance evaluation failed: %s", exc)
                        decision = None

                    data = candidate
                    question_id = candidate_id
                    gov_decision = decision
                    break

            if not data and enforce_geography_scope:
                data = _simple_geography_scope_fallback_payload(body.topic)
                question_source = "custom_template_simple"

            if data is not None and question_id is None:
                # Template fallbacks or other flows without a candidate_id.
                question_id = uuid.uuid4()
                try:
                    from services.governance_service import GovernanceService

                    gov_decision = await GovernanceService.evaluate_candidate(
                        db,
                        question_id=question_id,
                        room="custom",
                        action="persist",
                        topic=body.topic,
                        question_text=str(data.get("text", "")),
                        correct_answer=str(data.get("correctAnswer", "")),
                        explanation=str(data.get("explanation", "")),
                        options=list(data.get("options") or []),
                    )
                    if gov_decision is not None and not gov_decision.approved:
                        data = None
                except Exception as exc:
                    logger.warning("Custom governance evaluation failed: %s", exc)
                    gov_decision = None

            if not data:
                raise HTTPException(502, "Unable to generate a question right now. Please retry.")

            raw_options = [str(v).strip() for v in data.get("options", []) if str(v).strip()]
            correct_answer = str(data.get("correctAnswer", "")).strip()
            if not correct_answer and raw_options:
                correct_answer = raw_options[0]
            if not correct_answer:
                raise HTTPException(502, "Generated question did not include a verifiable answer. Please retry.")

            question_text = str(data.get("text", "")).strip() or f"What is a key fact about {body.topic}?"
            explanation = str(data.get("explanation", "")).strip() or (
                "Review each option carefully and connect it to the topic context."
            )
            options = _custom_options_for_level(raw_options, correct_answer, custom_level)

            if question_id is None:
                question_id = uuid.uuid4()
            try:
                new_q = QuestionBank(
                    id=question_id,
                    question_text=question_text,
                    options_json=json.dumps(options),
                    correct_answer=correct_answer,
                    explanation=explanation,
                    difficulty_irt=float(custom_level),
                    topic=topic_family,
                    source=question_source,
                )

                if gov_decision is not None:
                    try:
                        from services.governance_service import GovernanceService

                        await GovernanceService.apply_decision_to_persisted_row(db, row=new_q, decision=gov_decision)
                    except Exception as exc:
                        logger.warning("Custom governance persistence hook failed: %s", exc)

                db.add(new_q)
                await db.flush()
                new_q.times_seen = (new_q.times_seen or 0) + 1
                new_q.usage_count = (new_q.usage_count or 0) + 1
                new_q.last_served_at = datetime.now(timezone.utc).replace(tzinfo=None)
                await db.commit()
            except Exception as exc:
                await db.rollback()
                logger.error("Custom simple-mode question persistence failed: %s", exc)
                raise HTTPException(500, "Failed to persist generated question")

            await _remember_session_question(request, str(session.id), question_id)
            return CustomQuestionResponse(
                id=str(question_id),
                text=question_text,
                options=options,
                explanation="",
                fact_id=fact_id_to_use,
                concept_id=None,
                level=custom_level,
                is_free_text=custom_level == 5,
            )

        topic_family = _topic_family(body.topic)
        strict_topic = " - " in body.topic
        primary_keywords = _topic_keywords(body.topic)
        broader_keywords = _broader_geography_keywords(body.topic)
        use_broader_geography_scope = bool(
            strict_topic
            and topic_family == "geography"
            and broader_keywords
            and random.random() < GEOGRAPHY_BROADER_SCOPE_PROBABILITY
        )
        active_keywords = broader_keywords if use_broader_geography_scope else primary_keywords
        questions_answered = int(session.total_questions or 0)
        prefer_generation = bool(
            strict_topic
            and topic_family == "geography"
            and questions_answered >= 12
        )

        if strict_topic:
            try:
                await ConceptDiscoveryService.ensure_topic_seed_concepts(db, body.topic, max_new=4)
            except Exception as exc:
                logger.warning("topic concept seeding failed for %s: %s", body.topic, exc)

        dynamic_focus_concept = None
        if selected_concept is None:
            dynamic_focus_concept = await _pick_dynamic_focus_concept(
                db=db,
                request=request,
                user_id=session.user_id,
                session_id=str(session.id),
                topic_label=body.topic,
                primary_keywords=primary_keywords,
                broader_keywords=broader_keywords,
                allow_broader_scope=use_broader_geography_scope,
            )

        effective_concept = selected_concept or dynamic_focus_concept
        concept_cache_id = str(effective_concept.id) if effective_concept else None

        target_difficulty = custom_level
        recent_question_ids = await _get_recent_question_ids(
            request=request,
            user_id=session.user_id,
            topic_label=body.topic,
            concept_id=concept_cache_id,
        )
        if concept_cache_id is not None:
            recent_question_ids |= await _get_recent_question_ids(
                request=request,
                user_id=session.user_id,
                topic_label=body.topic,
                concept_id=None,
            )
        recent_question_ids |= await _get_recent_answered_question_ids(
            db=db,
            user_id=session.user_id,
            topic_label=body.topic,
            window=None,
        )

        recent_signatures = await _get_recent_signatures(
            request=request,
            user_id=session.user_id,
            topic_label=body.topic,
            concept_id=concept_cache_id,
        )
        if concept_cache_id is not None:
            global_recent_signatures = await _get_recent_signatures(
                request=request,
                user_id=session.user_id,
                topic_label=body.topic,
                concept_id=None,
            )
            recent_signatures = list(dict.fromkeys([*recent_signatures, *global_recent_signatures]))
        db_recent_signatures = await _get_recent_answered_signatures(
            db=db,
            user_id=session.user_id,
            topic_label=body.topic,
            window=None,
        )
        recent_signatures = list(dict.fromkeys([*recent_signatures, *db_recent_signatures]))
        recent_signature_set = {str(sig).strip().lower() for sig in recent_signatures if str(sig).strip()}
        session_id_str = str(session.id)

        generation_style = await _pick_generation_style(request, str(session.id), topic_family)

        async def _remember_served_payload(question_id: uuid.UUID, question_text: str, question_explanation: str) -> None:
            await _remember_served_question(
                request,
                session.user_id,
                body.topic,
                question_id,
                concept_cache_id,
            )
            await _remember_question_signature(
                request,
                session.user_id,
                body.topic,
                question_text,
                question_explanation,
                concept_cache_id,
            )

            if concept_cache_id is not None:
                await _remember_served_question(
                    request,
                    session.user_id,
                    body.topic,
                    question_id,
                    None,
                )
                await _remember_question_signature(
                    request,
                    session.user_id,
                    body.topic,
                    question_text,
                    question_explanation,
                    None,
                )

            await _remember_session_signature(
                request,
                session_id_str,
                _question_signature(question_text, question_explanation),
            )

        rag_data: Optional[dict[str, Any]] = None
        rag_question_id: Optional[uuid.UUID] = None
        rag_question_source = "custom_rag"
        rag_gov_decision = None

        if CUSTOM_REQUIRE_RAG_GENERATION:
            llm = await _get_llm(request)
            rag_pipeline = getattr(request.app.state, "rag_pipeline", None)
            http_client = getattr(request.app.state, "http_client", None)

            if rag_pipeline is None or http_client is None:
                logger.warning("Custom RAG pipeline unavailable; falling back to bank/LLM generation")
            else:
                rag_topic = _topic_detail(body.topic).title() if strict_topic else ("Mixed" if topic_family == "mixed" else topic_family.title())
                llm_rate_limited = False

                for _ in range(3):
                    rag_result = None
                    try:
                        rag_result = await rag_pipeline.run(
                            topic=rag_topic,
                            difficulty=target_difficulty,
                            user_accuracy=0.5,
                            llm_client=llm,
                            http_client=http_client,
                        )
                    except Exception as exc:
                        logger.warning("Custom RAG generation attempt failed: %s", exc)
                        continue

                    if getattr(llm, "last_status_code", None) == 429:
                        llm_rate_limited = True
                        logger.warning("Custom RAG hit LLM rate limit; falling back to bank/LLM generation")
                        break

                    normalized = _normalize_rag_mcq_payload(rag_result)
                    if not normalized:
                        continue

                    raw_opts = [str(v).strip() for v in normalized.get("options", []) if str(v).strip()]
                    raw_text = str(normalized.get("text", "")).strip()
                    raw_expl = str(normalized.get("explanation", "")).strip()
                    if not raw_text or len(raw_opts) < 2:
                        continue

                    if strict_topic and active_keywords:
                        if not _generated_payload_matches_keywords(raw_text, raw_expl, raw_opts, active_keywords):
                            continue

                    generated_signature = _question_signature(raw_text, raw_expl).lower()
                    if generated_signature and (
                        generated_signature in recent_signature_set
                        or await _session_has_signature(request, session_id_str, generated_signature)
                    ):
                        continue

                    candidate_id = uuid.uuid4()
                    decision = None
                    try:
                        from services.governance_service import GovernanceService

                        decision = await GovernanceService.evaluate_candidate(
                            db,
                            question_id=candidate_id,
                            room="custom",
                            action="persist",
                            topic=body.topic,
                            question_text=raw_text,
                            correct_answer=str(normalized.get("correctAnswer", "")),
                            explanation=raw_expl,
                            options=raw_opts,
                        )
                        if decision is not None and not decision.approved:
                            continue
                    except Exception as exc:
                        logger.warning("Custom governance evaluation failed: %s", exc)
                        decision = None

                    rag_data = normalized
                    rag_question_id = candidate_id
                    rag_gov_decision = decision
                    source_token = str((rag_result or {}).get("source", "rag")).strip().lower().replace(" ", "_")
                    rag_question_source = f"custom_rag_{source_token}" if source_token else "custom_rag"
                    break

                if not rag_data and llm_rate_limited:
                    logger.warning("Custom RAG exhausted after rate limit; using adaptive fallback chain")

            if rag_data:
                raw_options = [str(v).strip() for v in rag_data.get("options", []) if str(v).strip()]
                correct_answer = str(rag_data.get("correctAnswer", "")).strip()
                if not correct_answer and raw_options:
                    correct_answer = raw_options[0]

                question_text = str(rag_data.get("text", "")).strip() or f"What is a key fact about {body.topic}?"
                explanation = str(rag_data.get("explanation", "")).strip() or (
                    "Review each option carefully and connect it to the concept context."
                )
                options = _custom_options_for_level(raw_options, correct_answer, custom_level)

                question_id = rag_question_id or uuid.uuid4()
                inferred_concept = effective_concept

                try:
                    new_q = QuestionBank(
                        id=question_id,
                        question_text=question_text,
                        options_json=json.dumps(options),
                        correct_answer=correct_answer,
                        explanation=explanation,
                        difficulty_irt=float(target_difficulty),
                        topic=topic_family,
                        source=rag_question_source,
                    )

                    if rag_gov_decision is not None:
                        try:
                            from services.governance_service import GovernanceService

                            await GovernanceService.apply_decision_to_persisted_row(db, row=new_q, decision=rag_gov_decision)
                        except Exception as exc:
                            logger.warning("Custom governance persistence hook failed: %s", exc)
                    db.add(new_q)
                    await db.flush()

                    inferred_concept = await ConceptDiscoveryService.ensure_question_has_concept(
                        db=db,
                        question_text=question_text,
                        correct_answer=correct_answer,
                        topic=topic_family,
                        explanation=explanation,
                        topic_label=body.topic,
                    )

                    if effective_concept is not None:
                        db.add(
                            QuestionConcept(
                                question_id=question_id,
                                concept_id=effective_concept.id,
                                is_primary=True,
                            )
                        )
                        if inferred_concept.id != effective_concept.id:
                            db.add(
                                QuestionConcept(
                                    question_id=question_id,
                                    concept_id=inferred_concept.id,
                                    is_primary=False,
                                )
                            )
                    else:
                        db.add(
                            QuestionConcept(
                                question_id=question_id,
                                concept_id=inferred_concept.id,
                                is_primary=True,
                            )
                        )

                    new_q.times_seen = (new_q.times_seen or 0) + 1
                    new_q.usage_count = (new_q.usage_count or 0) + 1
                    new_q.last_served_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    await db.commit()
                except Exception as exc:
                    await db.rollback()
                    logger.error("RAG question persistence failed: %s", exc)
                    raise HTTPException(500, "Failed to persist generated RAG question")

                await _remember_served_payload(
                    question_id=question_id,
                    question_text=question_text,
                    question_explanation=explanation,
                )
                await _remember_session_question(request, str(session.id), question_id)

                return CustomQuestionResponse(
                    id=str(question_id),
                    text=question_text,
                    options=options,
                    explanation="",
                    fact_id=None,
                    concept_id=str(effective_concept.id) if effective_concept else (str(inferred_concept.id) if inferred_concept else None),
                    level=custom_level,
                    is_free_text=custom_level == 5,
                )

            logger.info("Custom RAG did not produce a question; continuing with adaptive fallback chain")

        data: Optional[dict[str, Any]] = None
        question_source = "custom_llm"
        question_id: Optional[uuid.UUID] = None
        gov_decision = None

        async def _pick_from_bank(
            keywords: Optional[list[str]],
            require_concept: bool,
            ignore_recent: bool = False,
        ):
            governance_enabled = False
            try:
                from services.governance_service import GovernanceService

                governance_enabled = GovernanceService.enabled()
            except Exception:
                governance_enabled = False

            stmt = select(QuestionBank)

            if require_concept and effective_concept is not None:
                stmt = (
                    stmt.join(QuestionConcept, QuestionConcept.question_id == QuestionBank.id)
                    .where(QuestionConcept.concept_id == effective_concept.id)
                )

            stmt = stmt.where(
                or_(
                    func.lower(QuestionBank.topic) == topic_family,
                    func.lower(QuestionBank.topic) == body.topic.lower(),
                )
            )

            if governance_enabled:
                stmt = stmt.where(QuestionBank.gov_approved == True)  # noqa: E712
                stmt = stmt.where(QuestionBank.gov_safe == True)  # noqa: E712

            keyword_filter = _build_keyword_filter(keywords or [])
            if keyword_filter is not None:
                stmt = stmt.where(keyword_filter)
            if recent_question_ids and not ignore_recent:
                stmt = stmt.where(QuestionBank.id.notin_(recent_question_ids))

            diff_score = func.abs(func.coalesce(QuestionBank.difficulty_irt, 3.0) - float(target_difficulty))
            stmt = stmt.order_by(
                diff_score.asc(),
                func.coalesce(QuestionBank.times_seen, 0).asc(),
                func.random(),
            ).limit(40)

            candidates = (await db.execute(stmt)).scalars().all()
            for candidate in candidates:
                if await _session_has_question(request, session_id_str, candidate.id):
                    continue

                if governance_enabled:
                    try:
                        decision = await GovernanceService.evaluate_bank_row_for_serving(
                            db,
                            row=candidate,
                            room="custom",
                            topic=body.topic,
                        )
                        if not decision.approved:
                            continue
                    except Exception:
                        pass

                signature = _question_signature(candidate.question_text, candidate.explanation or "").lower()
                if signature and signature in recent_signature_set:
                    continue
                if signature and await _session_has_signature(request, session_id_str, signature):
                    continue
                return candidate

            if ignore_recent and candidates and not (strict_topic and topic_family == "geography"):
                return candidates[0]
            return None

        repeat_row = None
        if not prefer_generation:
            topic_exposure = await _topic_exposure_count(db, session.user_id, topic_family)
            repeat_stmt = (
                select(UserConceptRepeatQueue)
                .join(QuestionConcept, QuestionConcept.question_id == UserConceptRepeatQueue.question_id)
                .join(Concept, Concept.id == QuestionConcept.concept_id)
                .where(
                    UserConceptRepeatQueue.user_id == session.user_id,
                    Concept.topic == topic_family,
                    UserConceptRepeatQueue.due_after_session <= topic_exposure,
                )
                .order_by(UserConceptRepeatQueue.repeat_probability.desc(), func.random())
                .limit(1)
            )
            if effective_concept is not None:
                repeat_stmt = repeat_stmt.where(UserConceptRepeatQueue.concept_id == effective_concept.id)
            if recent_question_ids:
                repeat_stmt = repeat_stmt.where(UserConceptRepeatQueue.question_id.notin_(recent_question_ids))

            repeat_row = (await db.execute(repeat_stmt)).scalar_one_or_none()
        if repeat_row is not None:
            repeat_q = await db.get(QuestionBank, repeat_row.question_id)
            if repeat_q is not None:
                if await _session_has_question(request, session_id_str, repeat_q.id):
                    repeat_q = None
            if repeat_q is not None:
                if strict_topic and active_keywords and not _question_matches_keywords(repeat_q, active_keywords):
                    repeat_q = None
                else:
                    repeat_signature = _question_signature(repeat_q.question_text, repeat_q.explanation or "").lower()
                    if repeat_signature and (
                        repeat_signature in recent_signature_set
                        or await _session_has_signature(request, session_id_str, repeat_signature)
                    ):
                        repeat_q = None
            if repeat_q is not None:
                try:
                    from services.governance_service import GovernanceService

                    if GovernanceService.enabled():
                        decision = await GovernanceService.evaluate_bank_row_for_serving(
                            db,
                            row=repeat_q,
                            room="custom",
                            topic=body.topic,
                        )
                        if not decision.approved:
                            repeat_q = None
                except Exception:
                    pass

                repeat_opts = _custom_options_for_level(_parse_options(repeat_q.options_json), repeat_q.correct_answer, custom_level)
                if custom_level == 5 or len(repeat_opts) >= 2:
                    repeat_q.times_seen = (repeat_q.times_seen or 0) + 1
                    repeat_q.usage_count = (repeat_q.usage_count or 0) + 1
                    repeat_q.last_served_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    await db.delete(repeat_row)
                    await db.commit()
                    await _remember_served_payload(
                        question_id=repeat_q.id,
                        question_text=repeat_q.question_text,
                        question_explanation=repeat_q.explanation or "",
                    )
                    await _remember_session_question(request, str(session.id), repeat_q.id)

                    return CustomQuestionResponse(
                        id=str(repeat_q.id),
                        text=repeat_q.question_text,
                        options=repeat_opts,
                        explanation="",
                        fact_id=None,
                        concept_id=str(effective_concept.id) if effective_concept else str(repeat_row.concept_id),
                        level=custom_level,
                        is_free_text=custom_level == 5,
                    )

        db_question = None
        if not prefer_generation and effective_concept is not None:
            if active_keywords:
                db_question = await _pick_from_bank(keywords=active_keywords, require_concept=True)
            if db_question is None and strict_topic and topic_family != "geography":
                db_question = await _pick_from_bank(keywords=None, require_concept=True)
            if db_question is None and not strict_topic:
                db_question = await _pick_from_bank(keywords=None, require_concept=True)

        if not prefer_generation and db_question is None:
            if strict_topic:
                if active_keywords:
                    db_question = await _pick_from_bank(keywords=active_keywords, require_concept=False)
                if (
                    db_question is None
                    and topic_family == "geography"
                    and not use_broader_geography_scope
                    and broader_keywords
                    and random.random() < GEOGRAPHY_BROADER_SCOPE_PROBABILITY
                ):
                    db_question = await _pick_from_bank(keywords=broader_keywords, require_concept=False)
                    if db_question is not None:
                        use_broader_geography_scope = True
            else:
                db_question = await _pick_from_bank(
                    keywords=primary_keywords if primary_keywords else None,
                    require_concept=False,
                )
                if db_question is None:
                    db_question = await _pick_from_bank(keywords=None, require_concept=False)

        if db_question is not None and await _session_has_question(request, session_id_str, db_question.id):
            db_question = None

        if db_question is not None:
            try:
                from services.governance_service import GovernanceService

                if GovernanceService.enabled():
                    decision = await GovernanceService.evaluate_bank_row_for_serving(
                        db,
                        row=db_question,
                        room="custom",
                        topic=body.topic,
                    )
                    if not decision.approved:
                        db_question = None
            except Exception:
                pass

        if db_question is not None:
            options = _custom_options_for_level(_parse_options(db_question.options_json), db_question.correct_answer, custom_level)
            if custom_level == 5 or len(options) >= 2:
                db_question.times_seen = (db_question.times_seen or 0) + 1
                db_question.usage_count = (db_question.usage_count or 0) + 1
                db_question.last_served_at = datetime.now(timezone.utc).replace(tzinfo=None)
                await db.commit()
                await _remember_served_payload(
                    question_id=db_question.id,
                    question_text=db_question.question_text,
                    question_explanation=db_question.explanation or "",
                )
                await _remember_session_question(request, str(session.id), db_question.id)

                concept_id = await _resolve_concept_id_for_question(db, db_question.id, effective_concept)
                return CustomQuestionResponse(
                    id=str(db_question.id),
                    text=db_question.question_text,
                    options=options,
                    explanation="",
                    fact_id=None,
                    concept_id=concept_id,
                    level=custom_level,
                    is_free_text=custom_level == 5,
                )

        fact = await pick_fact_for_user(db, session.user_id, body.topic)
        if fact is None:
            fact_content = f"a key concept related to {body.topic}"
            fact_id_to_use = None
        else:
            fact_content = fact.content
            fact_id_to_use = str(fact.id)
            fact.total_questions_generated += 1

        if strict_topic:
            fact_content = f"Exact topic scope: {body.topic}. {fact_content}"
        if effective_concept is not None:
            fact_content = f"Concept focus: {effective_concept.name}. {fact_content}"
        if recent_signatures:
            fact_content = (
                f"{fact_content} Avoid repeating these recent angles: "
                + " | ".join(recent_signatures[:5])
            )

        llm = await _get_llm(request)
        generation_rules = _custom_generation_rules(
            topic_label=body.topic,
            concept_name=effective_concept.name if effective_concept else None,
            generation_style=generation_style,
            recent_signatures=recent_signatures,
            allow_broader_geography_scope=use_broader_geography_scope,
        )

        data = None
        question_source = "custom_llm"
        question_id: Optional[uuid.UUID] = None
        gov_decision = None
        for _ in range(3):
            try:
                data = await llm.generate_mcq(
                    context=fact_content,
                    topic=body.topic,
                    difficulty=target_difficulty,
                    strategy="concept_focus" if effective_concept else "topic_focus",
                    user_accuracy=0.5,
                    extra_instructions=generation_rules,
                )
            except Exception as exc:
                logger.warning("LLM generate_mcq attempt failed: %s", exc)
                data = None

            if data and isinstance(data.get("options"), list) and str(data.get("text", "")).strip():
                raw_opts = [str(v).strip() for v in data.get("options", []) if str(v).strip()]
                raw_text = str(data.get("text", "")).strip()
                raw_expl = str(data.get("explanation", "")).strip()
                if strict_topic and active_keywords:
                    if not _generated_payload_matches_keywords(raw_text, raw_expl, raw_opts, active_keywords):
                        data = None
                        continue
                generated_signature = _question_signature(raw_text, raw_expl).lower()
                if generated_signature and (
                    generated_signature in recent_signature_set
                    or await _session_has_signature(request, session_id_str, generated_signature)
                ):
                    data = None
                    continue

                # Governance: reject blocked candidates before serving/persisting.
                candidate_id = uuid.uuid4()
                candidate_correct = str(data.get("correctAnswer", "")).strip() or (
                    raw_opts[0] if raw_opts else ""
                )
                decision = None
                try:
                    from services.governance_service import GovernanceService

                    decision = await GovernanceService.evaluate_candidate(
                        db,
                        question_id=candidate_id,
                        room="custom",
                        action="persist",
                        topic=body.topic,
                        question_text=raw_text,
                        correct_answer=candidate_correct,
                        explanation=raw_expl,
                        options=raw_opts,
                    )
                    if decision is not None and not decision.approved:
                        data = None
                        continue
                except Exception as exc:
                    logger.warning("Custom governance evaluation failed: %s", exc)
                    decision = None

                question_id = candidate_id
                gov_decision = decision
                break
            data = None

        if not data:
            offline_data = await _offline_geography_payload(
                topic_label=body.topic,
                recent_signatures=recent_signatures,
                request=request,
                session_id=session_id_str,
            )
            if offline_data is not None:
                offline_opts = [str(v).strip() for v in offline_data.get("options", []) if str(v).strip()]
                offline_text = str(offline_data.get("text", "")).strip()
                offline_expl = str(offline_data.get("explanation", "")).strip()
                offline_signature = _question_signature(offline_text, offline_expl).lower()
                if offline_signature and (
                    offline_signature in recent_signature_set
                    or await _session_has_signature(request, session_id_str, offline_signature)
                ):
                    offline_data = None
                if offline_data is not None:
                    if not strict_topic or not active_keywords or _generated_payload_matches_keywords(
                        offline_text,
                        offline_expl,
                        offline_opts,
                        active_keywords,
                    ):
                        candidate_id = uuid.uuid4()
                        decision = None
                        try:
                            from services.governance_service import GovernanceService

                            offline_correct = str(offline_data.get("correctAnswer", "")).strip() or (
                                offline_opts[0] if offline_opts else ""
                            )
                            decision = await GovernanceService.evaluate_candidate(
                                db,
                                question_id=candidate_id,
                                room="custom",
                                action="persist",
                                topic=body.topic,
                                question_text=offline_text,
                                correct_answer=offline_correct,
                                explanation=offline_expl,
                                options=offline_opts,
                            )
                            if decision is not None and not decision.approved:
                                offline_data = None
                            else:
                                data = offline_data
                                question_source = "custom_template"
                                question_id = candidate_id
                                gov_decision = decision
                        except Exception as exc:
                            logger.warning("Custom governance evaluation failed: %s", exc)
                            data = offline_data
                            question_source = "custom_template"
                            question_id = candidate_id
                            gov_decision = None

        if not data and strict_topic and topic_family == "geography":
            # Relax recency constraints once to avoid collapsing into emergency DB repeats.
            fallback_offline_data = await _offline_geography_payload(
                topic_label=body.topic,
                recent_signatures=[],
                request=request,
                session_id=session_id_str,
            )
            if fallback_offline_data is not None:
                fallback_opts = [
                    str(v).strip()
                    for v in fallback_offline_data.get("options", [])
                    if str(v).strip()
                ]
                fallback_text = str(fallback_offline_data.get("text", "")).strip()
                fallback_expl = str(fallback_offline_data.get("explanation", "")).strip()

                candidate_id = uuid.uuid4()
                decision = None
                try:
                    from services.governance_service import GovernanceService

                    fallback_correct = str(fallback_offline_data.get("correctAnswer", "")).strip() or (
                        fallback_opts[0] if fallback_opts else ""
                    )
                    decision = await GovernanceService.evaluate_candidate(
                        db,
                        question_id=candidate_id,
                        room="custom",
                        action="persist",
                        topic=body.topic,
                        question_text=fallback_text,
                        correct_answer=fallback_correct,
                        explanation=fallback_expl,
                        options=fallback_opts,
                    )
                    if decision is not None and not decision.approved:
                        fallback_offline_data = None
                    else:
                        data = fallback_offline_data
                        question_source = "custom_template"
                        question_id = candidate_id
                        gov_decision = decision
                except Exception as exc:
                    logger.warning("Custom governance evaluation failed: %s", exc)
                    data = fallback_offline_data
                    question_source = "custom_template"
                    question_id = candidate_id
                    gov_decision = None

        if not data:
            emergency_keywords = active_keywords if strict_topic else None
            emergency_q = await _pick_from_bank(
                keywords=emergency_keywords,
                require_concept=False,
                ignore_recent=True,
            )

            if (
                emergency_q is None
                and strict_topic
                and topic_family == "geography"
                and broader_keywords
                and use_broader_geography_scope
            ):
                emergency_q = await _pick_from_bank(
                    keywords=broader_keywords,
                    require_concept=False,
                    ignore_recent=True,
                )

            if emergency_q is None and not strict_topic:
                emergency_q = await _pick_from_bank(
                    keywords=None,
                    require_concept=False,
                    ignore_recent=True,
                )

            if emergency_q is not None and await _session_has_question(request, session_id_str, emergency_q.id):
                emergency_q = None

            if emergency_q is not None:
                try:
                    from services.governance_service import GovernanceService

                    if GovernanceService.enabled():
                        decision = await GovernanceService.evaluate_bank_row_for_serving(
                            db,
                            row=emergency_q,
                            room="custom",
                            topic=body.topic,
                        )
                        if not decision.approved:
                            emergency_q = None
                except Exception:
                    pass

            if emergency_q is not None:
                emergency_opts = _custom_options_for_level(_parse_options(emergency_q.options_json), emergency_q.correct_answer, custom_level)
                if custom_level == 5 or len(emergency_opts) >= 2:
                    emergency_q.times_seen = (emergency_q.times_seen or 0) + 1
                    emergency_q.usage_count = (emergency_q.usage_count or 0) + 1
                    emergency_q.last_served_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    await db.commit()
                    await _remember_served_payload(
                        question_id=emergency_q.id,
                        question_text=emergency_q.question_text,
                        question_explanation=emergency_q.explanation or "",
                    )
                    await _remember_session_question(request, str(session.id), emergency_q.id)

                    concept_id = await _resolve_concept_id_for_question(db, emergency_q.id, effective_concept)
                    return CustomQuestionResponse(
                        id=str(emergency_q.id),
                        text=emergency_q.question_text,
                        options=emergency_opts,
                        explanation="",
                        fact_id=None,
                        concept_id=concept_id,
                        level=custom_level,
                        is_free_text=custom_level == 5,
                    )

            raise HTTPException(502, "Unable to generate a question right now. Please retry.")

        raw_options = [str(v).strip() for v in data.get("options", []) if str(v).strip()]
        correct_answer = str(data.get("correctAnswer", "")).strip()
        if not correct_answer and raw_options:
            correct_answer = raw_options[0]

        question_text = str(data.get("text", "")).strip() or f"What is a key fact about {body.topic}?"
        explanation = str(data.get("explanation", "")).strip() or (
            "Review each option carefully and connect it to the concept context."
        )
        options = _custom_options_for_level(raw_options, correct_answer, custom_level)

        if question_id is None:
            question_id = uuid.uuid4()
            # Safety: governance may not have run in earlier fallbacks.
            try:
                from services.governance_service import GovernanceService

                gov_decision = await GovernanceService.evaluate_candidate(
                    db,
                    question_id=question_id,
                    room="custom",
                    action="persist",
                    topic=body.topic,
                    question_text=question_text,
                    correct_answer=correct_answer,
                    explanation=explanation,
                    options=options,
                )
                if gov_decision is not None and not gov_decision.approved:
                    raise HTTPException(502, "Unable to generate a question right now. Please retry.")
            except HTTPException:
                raise
            except Exception as exc:
                logger.warning("Custom governance evaluation failed: %s", exc)
                gov_decision = None

        inferred_concept = effective_concept
        try:
            new_q = QuestionBank(
                id=question_id,
                question_text=question_text,
                options_json=json.dumps(options),
                correct_answer=correct_answer,
                explanation=explanation,
                difficulty_irt=float(target_difficulty),
                topic=topic_family,
                source=question_source,
            )

            if gov_decision is not None:
                try:
                    from services.governance_service import GovernanceService

                    await GovernanceService.apply_decision_to_persisted_row(db, row=new_q, decision=gov_decision)
                except Exception as exc:
                    logger.warning("Custom governance persistence hook failed: %s", exc)
            db.add(new_q)
            await db.flush()

            inferred_concept = await ConceptDiscoveryService.ensure_question_has_concept(
                db=db,
                question_text=question_text,
                correct_answer=correct_answer,
                topic=topic_family,
                explanation=explanation,
                topic_label=body.topic,
            )

            if effective_concept is not None:
                db.add(
                    QuestionConcept(
                        question_id=question_id,
                        concept_id=effective_concept.id,
                        is_primary=True,
                    )
                )
                if inferred_concept.id != effective_concept.id:
                    db.add(
                        QuestionConcept(
                            question_id=question_id,
                            concept_id=inferred_concept.id,
                            is_primary=False,
                        )
                    )
            else:
                db.add(
                    QuestionConcept(
                        question_id=question_id,
                        concept_id=inferred_concept.id,
                        is_primary=True,
                    )
                )

            new_q.times_seen = (new_q.times_seen or 0) + 1
            new_q.usage_count = (new_q.usage_count or 0) + 1
            new_q.last_served_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.warning("Could not save custom question to question_bank: %s", exc)

        await _remember_served_payload(
            question_id=question_id,
            question_text=question_text,
            question_explanation=explanation,
        )
        await _remember_session_question(request, str(session.id), question_id)
        return CustomQuestionResponse(
            id=str(question_id),
            text=question_text,
            options=options,
            explanation="",
            fact_id=fact_id_to_use,
            concept_id=str(effective_concept.id) if effective_concept else (str(inferred_concept.id) if inferred_concept else None),
            level=custom_level,
            is_free_text=custom_level == 5,
        )


@custom_router.post("/generate-hint", response_model=HintOut)
@limiter.limit("20/minute")
# Generate a contextual hint for a custom-room question.
async def generate_hint(
    body: GenerateCustomHintRequest,
    request: Request,
    current=Depends(get_current_user),
):
    user, _ = current
    await enforce_user_quota(request, user.id, "custom_hint", limit=120, window_seconds=3600)
    llm = await _get_llm(request)
    try:
        question_uuid = uuid.UUID(body.question_id)
    except ValueError:
        raise HTTPException(422, "question_id must be a valid UUID")

    async for db in _get_db(request):
        question_row = await db.get(QuestionBank, question_uuid)
        if question_row is None:
            raise HTTPException(404, "Question not found")

        resolved_question_text = (body.question_text or question_row.question_text)
        resolved_correct_answer = (question_row.correct_answer or "")
        hint = await llm.generate_hint(
            question_text=resolved_question_text,
            correct_answer=resolved_correct_answer,
        )
        logger.debug(
            "custom.generate_hint - LLM inputs: question=%s question_len=%d body_provided=%s",
            str(question_uuid)[:8],
            len(resolved_question_text or ""),
            bool(body.question_text),
        )
        if not hint:
            hint = "Think about the broader historical and geographical context of this topic."

        # ── Governance: check hint text against active block rules ──
        try:
            from services.governance_service import GovernanceService

            if GovernanceService.enabled() and hint:
                topic = getattr(question_row, "topic", "") or ""
                decision = await GovernanceService.evaluate_candidate(
                    db,
                    question_id=question_uuid,
                    room="custom",
                    action="hint",
                    topic=topic,
                    question_text=hint,
                    correct_answer="",
                    explanation="",
                    options=[],
                )
                logger.debug(
                    "custom.generate_hint - governance decision for question %s: approved=%s reasons=%s",
                    str(question_uuid)[:8],
                    decision.approved,
                    decision.reasons,
                )
                if not decision.approved:
                    logger.info(
                        "custom.generate_hint - hint blocked by governance, replacing with fallback for question %s",
                        str(question_uuid)[:8],
                    )
                    hint = "Think about the broader historical and geographical context of this topic."
        except Exception:
            pass  # Governance must never crash the hint flow.

        return HintOut(hint=hint)


@custom_router.post("/submit-answer", response_model=SubmitAnswerResponse)
@limiter.limit("80/minute")
# Validate answer correctness and update session/mastery progression.
async def submit_answer(body: SubmitAnswerRequest, request: Request, current=Depends(get_current_user)):
    user, _ = current
    await enforce_user_quota(request, user.id, "custom_submit", limit=320, window_seconds=3600)
    async for db in _get_db(request):
        session = await get_session(db, body.session_id)
        if session is None:
            raise HTTPException(404, "Session not found")
        if str(session.user_id) != str(user.id):
            raise HTTPException(403, "You are not allowed to submit for this session")
        if session.ended_at is not None:
            raise HTTPException(400, "Session already ended")

        try:
            qid = uuid.UUID(body.question_id)
        except ValueError:
            raise HTTPException(404, "Question not found")

        if qid == uuid.UUID("00000000-0000-0000-0000-000000000000"):
            raise HTTPException(404, "Question not found")

        question_row = await db.get(QuestionBank, qid)
        if question_row is None:
            raise HTTPException(404, "Question not found")

        if not await _session_has_question(request, str(session.id), qid):
            raise HTTPException(409, "Question was not issued for this session")

        already_answered = (
            await db.execute(
                select(UserResponse.id).where(
                    UserResponse.user_id == session.user_id,
                    UserResponse.session_id == session.id,
                    UserResponse.question_id == qid,
                )
            )
        ).scalar_one_or_none()
        if already_answered is not None:
            raise HTTPException(409, "This question has already been answered in this session")

        submitted = (body.answer or "").strip().lower()
        if not submitted:
            raise HTTPException(400, "Answer cannot be empty. Please provide a valid answer.")
        expected = (question_row.correct_answer or "").strip().lower()
        is_correct = submitted == expected

        session.total_questions += 1
        if is_correct:
            session.correct_count += 1

        total_in_db = await total_facts_for_topic(db, session.topic)
        total = _custom_progress_total(total_in_db)
        mastery = await get_or_create_mastery(db, session.user_id, session.topic, total)

        # Custom progress bar is game-like: +5% for a correct answer, -2% for a wrong answer.
        # Keep the persisted mastered_facts_count in sync with the visible percentage.
        current_progress = float(mastery.completion_percentage or 0.0)
        next_progress = current_progress + (5.0 if is_correct else -2.0)
        mastery.completion_percentage = max(0.0, min(100.0, next_progress))
        mastery.total_facts_count = max(1, mastery.total_facts_count or total or 1)
        mastery.mastered_facts_count = int(round((mastery.completion_percentage / 100.0) * mastery.total_facts_count))

        if _custom_concept_tracking_enabled():
            concept_link_row = await db.execute(
                select(QuestionConcept)
                .where(QuestionConcept.question_id == qid)
                .order_by(QuestionConcept.is_primary.desc())
                .limit(1)
            )
            concept_link = concept_link_row.scalar_one_or_none()

            if concept_link is not None:
                theta_row_result = await db.execute(
                    select(UserConceptTheta).where(
                        UserConceptTheta.user_id == session.user_id,
                        UserConceptTheta.concept_id == concept_link.concept_id,
                    )
                )
                concept_theta = theta_row_result.scalar_one_or_none()
                if concept_theta is None:
                    concept_theta = UserConceptTheta(
                        user_id=session.user_id,
                        concept_id=concept_link.concept_id,
                        theta=0.0,
                        theta_variance=1.0,
                        response_count=0,
                        exposure_count=0,
                        mastery_level="BEGINNER",
                        first_seen_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    )
                    db.add(concept_theta)
                    await db.flush()

                if ENABLE_UNIFIED_CONCEPT_THETA:
                    # Use the shared ConceptIRT math so custom and classic apply the
                    # same variance decay + mastery mapping (roadmap item 8).
                    new_theta, new_variance, mastery = ConceptIRT.compute_update(
                        concept_theta.theta, concept_theta.theta_variance, 0.0, is_correct
                    )
                    concept_theta.theta = new_theta
                    concept_theta.theta_variance = new_variance
                    concept_theta.mastery_level = mastery
                else:
                    new_theta = update_theta(concept_theta.theta, 0.0, is_correct)
                    concept_theta.theta = new_theta
                    concept_theta.mastery_level = _mastery_level(new_theta, concept_theta.response_count)
                concept_theta.response_count += 1
                concept_theta.exposure_count += 1
                concept_theta.last_played_at = datetime.now(timezone.utc).replace(tzinfo=None)
                concept_theta.last_updated = datetime.now(timezone.utc).replace(tzinfo=None)

                if not is_correct:
                    topic_exposure = await _topic_exposure_count(
                        db=db,
                        user_id=session.user_id,
                        topic_family=_topic_family(session.topic),
                    )
                    due_after = topic_exposure + WRONG_ANSWER_REPEAT_DELAY

                    existing_repeat = (
                        await db.execute(
                            select(UserConceptRepeatQueue).where(
                                UserConceptRepeatQueue.user_id == session.user_id,
                                UserConceptRepeatQueue.concept_id == concept_link.concept_id,
                                UserConceptRepeatQueue.question_id == qid,
                            )
                        )
                    ).scalar_one_or_none()

                    if existing_repeat is None:
                        db.add(
                            UserConceptRepeatQueue(
                                user_id=session.user_id,
                                concept_id=concept_link.concept_id,
                                question_id=qid,
                                repeat_probability=0.75,
                                due_after_session=due_after,
                            )
                        )
                    else:
                        existing_repeat.repeat_probability = max(existing_repeat.repeat_probability, 0.75)
                        existing_repeat.due_after_session = max(existing_repeat.due_after_session, due_after)

        difficulty_sent = int(round(float(question_row.difficulty_irt or 3.0)))
        difficulty_sent = max(1, min(5, difficulty_sent))
        db.add(
            UserResponse(
                user_id=session.user_id,
                session_id=session.id,
                question_id=qid,
                topic=_topic_family(session.topic),
                difficulty_sent=difficulty_sent,
                answered_correct=is_correct,
                time_taken=int(body.time_taken or 0),
                used_hint=bool(body.used_hint),
            )
        )

        await db.commit()

        return SubmitAnswerResponse(
            is_correct=is_correct,
            correct_answer=question_row.correct_answer,
            explanation=question_row.explanation or "",
            new_progress_percentage=mastery.completion_percentage,
            total_questions_this_session=session.total_questions,
        )


@custom_router.post("/session/{session_id}/end", response_model=EndSessionResponse)
@limiter.limit("20/minute")
# Close a custom session and return completion stats.
async def end_session(session_id: str, request: Request, current=Depends(get_current_user)):
    user, _ = current
    async for db in _get_db(request):
        session = await get_session(db, session_id)
        if session is None:
            raise HTTPException(404, "Session not found")
        if str(session.user_id) != str(user.id):
            raise HTTPException(403, "You are not allowed to end this session")

        if session.ended_at is None:
            session.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await db.commit()

        mastery = (
            await db.execute(
                select(UserTopicMastery).where(
                    UserTopicMastery.user_id == session.user_id,
                    UserTopicMastery.topic == session.topic,
                )
            )
        ).scalar_one_or_none()
        completion = mastery.completion_percentage if mastery else 0.0

        return EndSessionResponse(
            session_id=str(session.id),
            topic=session.topic,
            questions_answered=session.total_questions,
            correct_count=session.correct_count,
            completion_percentage_after=completion,
        )
