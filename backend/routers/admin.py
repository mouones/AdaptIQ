"""
routers/admin.py - Admin dashboard API endpoints.

All endpoints require admin privileges (user.is_admin = True).

Covers:
  - GET  /api/admin/overview       - System-wide statistics
  - GET  /api/admin/top-concepts   - Most-tracked concepts
  - GET  /api/admin/users          - Paginated user list
  - GET  /api/admin/users/{id}     - User detail with sessions & mastery
  - PATCH /api/admin/users/{id}    - Toggle user active/admin status
  - GET  /api/admin/questions      - Paginated question list
  - GET  /api/admin/sessions       - All session types combined
  - GET  /api/admin/monitoring     - Request stats and error log

Internal helpers:
    - _as_iso: safe datetime serializer
    - _require_admin: strict admin guard
    - get_admin_read_access: strict dependency for authenticated admin reads
"""

import logging
import json
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import Integer, MetaData, Table, cast, delete, func, inspect as sa_inspect, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from database.challenge_models import ChallengeSession
from database.concept_models import ClassicSession, Concept, UserConceptTheta, QuestionConcept, UserConceptRepeatQueue
from database.custom_models import CustomSession, Fact, Topic
from database.models import QuestionBank, User, UserResponse
from routers.auth import get_current_user, get_db
from services.custom_service import TOPIC_CATALOGUE
from services.security_utils import is_sensitive_column, redact_db_value
from services.monitoring import get_monitoring
from services.question_sources import summarize_source_counts
from config import ADMIN_DB_INSPECTOR_DEFAULT_LIMIT, ADMIN_DB_INSPECTOR_MAX_LIMIT

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/api/admin", tags=["Admin"])

# Serialize datetimes consistently for JSON payloads.
def _as_iso(dt: Optional[datetime]) -> Optional[str]:
    """Convert a datetime to ISO string, returning None if input is None."""
    return dt.isoformat() if dt else None


# Convert DB values to JSON-safe primitives for admin inspector payloads.
def _to_jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def _display_name_for_user(user: User) -> str:
    """Prefer readable display names for admin UX."""
    raw_username = (getattr(user, "username", "") or "").strip()
    if raw_username:
        if any(ch.isalpha() for ch in raw_username):
            return raw_username.replace("_", " ").replace(".", " ").strip().title()
        return raw_username

    local_part = (getattr(user, "email", "") or "").split("@", 1)[0].strip()
    if not local_part:
        return f"user-{str(user.id)[:8]}"
    return local_part.replace("_", " ").replace(".", " ").strip().title()


# Enforce admin-only access for protected admin operations.
def _require_admin(current) -> User:
    """Extract user from current tuple and verify admin privileges.

    Args:
        current: (User, issued_at) tuple from get_current_user

    Returns:
        User object if admin

    Raises:
        HTTPException 403 if not admin
    """
    user, _issued_at = current
    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def get_admin_read_access(current=Depends(get_current_user)):
    """Authorize read access for authenticated admins only."""
    _require_admin(current)
    return current


class AdminConceptCreateIn(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    topic: str = Field(min_length=2, max_length=50)
    scope: Optional[str] = Field(default="general", min_length=2, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)


class AdminConceptUpdateIn(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=255)
    topic: Optional[str] = Field(default=None, min_length=2, max_length=50)
    scope: Optional[str] = Field(default=None, min_length=2, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)


class AdminQuestionCreateIn(BaseModel):
    question_text: str = Field(min_length=5, max_length=4000)
    correct_answer: str = Field(min_length=1, max_length=2000)
    options: list[str] = Field(min_length=2, max_length=12)
    explanation: str = Field(min_length=1, max_length=8000)
    topic: str = Field(min_length=2, max_length=50)
    difficulty_irt: float = Field(default=2.5, ge=0.0, le=10.0)
    source: str = Field(default="admin", min_length=2, max_length=30)
    primary_concept_id: Optional[str] = None


class AdminQuestionUpdateIn(BaseModel):
    question_text: Optional[str] = Field(default=None, min_length=5, max_length=4000)
    correct_answer: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    options: Optional[list[str]] = Field(default=None, min_length=2, max_length=12)
    explanation: Optional[str] = Field(default=None, min_length=1, max_length=8000)
    topic: Optional[str] = Field(default=None, min_length=2, max_length=50)
    difficulty_irt: Optional[float] = Field(default=None, ge=0.0, le=10.0)
    source: Optional[str] = Field(default=None, min_length=2, max_length=30)
    primary_concept_id: Optional[str] = None


class AdminUserUpdateIn(BaseModel):
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None
    ban_minutes: Optional[int] = Field(default=None, ge=1, le=525600)
    ban_reason: Optional[str] = Field(default=None, min_length=1, max_length=500)
    clear_ban: Optional[bool] = None
    username: Optional[str] = Field(default=None, min_length=3, max_length=100)
    email: Optional[str] = Field(default=None, min_length=5, max_length=255)
    level: Optional[str] = Field(default=None, min_length=1, max_length=30)
    points: Optional[int] = Field(default=None, ge=0)


class AdminCustomTopicApproveIn(BaseModel):
    type: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=2, max_length=200)
    slug: Optional[str] = Field(default=None, min_length=2, max_length=100)
    description: Optional[str] = Field(default=None, max_length=2000)
    source_topic: Optional[str] = Field(default=None, min_length=2, max_length=50)
    max_facts: int = Field(default=100, ge=1, le=500)
    context: Optional[str] = Field(default=None, max_length=5000)


class AdminCustomTopicToggleActiveIn(BaseModel):
    slug: str
    is_active: bool


def _normalize_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_email(value: Optional[str]) -> Optional[str]:
    normalized = _normalize_text(value)
    return normalized.lower() if normalized else None


def _slugify_topic(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug[:100] or "custom-topic"


def _topic_label(topic_type: str, name: str) -> str:
    clean_type = (topic_type or "").strip().title()
    clean_name = (name or "").strip()
    if not clean_name:
        return clean_type
    if clean_name.lower().startswith(clean_type.lower()):
        return clean_name
    return f"{clean_type} - {clean_name}"


def _topic_family(topic_label: str) -> str:
    text_value = (topic_label or "").strip().lower()
    if text_value.startswith("history"):
        return "history"
    if text_value.startswith("geography"):
        return "geography"
    if text_value.startswith("mixed") or text_value == "mix":
        return "mixed"
    return text_value


def _catalogue_topic_state(slug: str, topic: dict, approved_info: dict[str, dict]) -> dict:
    info = approved_info.get(str(slug))
    return {
        "approved": True,
        "is_active": bool(info["is_active"]) if info else True,
        "total_facts_count": int(info["total_facts_count"]) if info else int(topic.get("total_facts", 0) or 0),
    }


def _fact_content_from_question(row: QuestionBank) -> str:
    question = (row.question_text or "").strip()
    answer = (row.correct_answer or "").strip()
    explanation = (row.explanation or "").strip()
    if not explanation:
        explanation = f"The correct answer is {answer}."
    return f"Question: {question}\nCorrect answer: {answer}\nExplanation: {explanation}"


def _difficulty_hint_from_question(row: QuestionBank) -> str:
    value = float(row.difficulty_irt or 0.0)
    if value <= 1.5:
        return "easy"
    if value >= 3.5:
        return "hard"
    return "medium"


def _is_valid_email(value: str) -> bool:
    if value.count("@") != 1:
        return False
    local_part, domain_part = value.split("@", 1)
    if not local_part or not domain_part:
        return False
    if domain_part.startswith(".") or domain_part.endswith("."):
        return False
    return "." in domain_part


async def _find_other_user_by_username(db: AsyncSession, username: str, exclude_user_id: uuid.UUID):
    return await db.scalar(select(User).where(User.username == username, User.id != exclude_user_id))


async def _find_other_user_by_email(db: AsyncSession, email: str, exclude_user_id: uuid.UUID):
    return await db.scalar(select(User).where(User.email == email, User.id != exclude_user_id))


def _normalize_options(options: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in options:
        opt = (raw or "").strip()
        if not opt:
            continue
        key = opt.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(opt)
    if len(cleaned) < 2:
        raise HTTPException(status_code=400, detail="At least two unique non-empty options are required")
    return cleaned


def _parse_uuid_or_422(value: Optional[str], field_name: str) -> Optional[uuid.UUID]:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid {field_name}")


def _question_admin_payload(row: QuestionBank) -> dict:
    options: list[str] = []
    try:
        parsed = json.loads(row.options_json or "[]")
        if isinstance(parsed, list):
            options = [str(item) for item in parsed]
    except Exception:
        options = []

    return {
        "id": str(row.id),
        "question_text": row.question_text,
        "correct_answer": row.correct_answer,
        "options": options,
        "options_json": row.options_json,
        "explanation": row.explanation,
        "topic": row.topic,
        "difficulty_irt": round(float(row.difficulty_irt or 0.0), 2),
        "source": row.source,
        "usage_count": int(row.usage_count or 0),
        "times_seen": int(row.times_seen or 0),
        "last_served_at": _as_iso(row.last_served_at),
        "created_at": _as_iso(row.created_at),
        "primary_concept_id": str(row.primary_concept_id) if row.primary_concept_id else None,
        "gov_approved": bool(row.gov_approved) if row.gov_approved is not None else None,
        "gov_safe": bool(row.gov_safe) if row.gov_safe is not None else None,
        "gov_flags_json": row.gov_flags_json,
    }


# -
# OVERVIEW
# -


@admin_router.get("/overview")
# Aggregate top-level admin dashboard counters and health stats.
async def admin_overview(
    current=Depends(get_admin_read_access),
    db: AsyncSession = Depends(get_db),
):
    """System-wide dashboard statistics.

    Returns counts of users, questions, sessions, concepts, and responses.
    """
    if current is not None:
        _require_admin(current)

    total_users = await db.scalar(select(func.count()).select_from(User)) or 0
    active_users = await db.scalar(
        select(func.count()).select_from(User).where(User.is_active == True)
    ) or 0
    admin_users = await db.scalar(
        select(func.count()).select_from(User).where(User.is_admin == True)
    ) or 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    banned_users = await db.scalar(
        select(func.count()).select_from(User).where(User.ban_until.is_not(None), User.ban_until > now)
    ) or 0

    total_questions = await db.scalar(select(func.count()).select_from(QuestionBank)) or 0
    source_rows = await db.execute(
        select(QuestionBank.source, func.count(QuestionBank.id)).group_by(QuestionBank.source)
    )
    source_summary = summarize_source_counts(dict(source_rows.all()))
    cached_questions = await db.scalar(
        select(func.count()).select_from(QuestionBank).where(QuestionBank.times_seen > 0)
    ) or 0

    total_responses = await db.scalar(select(func.count()).select_from(UserResponse)) or 0
    classic_sessions = await db.scalar(select(func.count()).select_from(ClassicSession)) or 0
    challenge_sessions = await db.scalar(select(func.count()).select_from(ChallengeSession)) or 0
    custom_sessions = await db.scalar(select(func.count()).select_from(CustomSession)) or 0
    total_concepts = await db.scalar(select(func.count()).select_from(Concept)) or 0
    concept_mastery_rows = await db.scalar(select(func.count()).select_from(UserConceptTheta)) or 0

    latest_user_created = await db.scalar(select(func.max(User.created_at)))
    latest_question_created = await db.scalar(select(func.max(QuestionBank.created_at)))

    # PvP stats (optional - table may not exist yet)
    pvp_matches = 0
    pvp_players = 0
    try:
        from database.pvp_models import PvPMatch, PvPRating
        pvp_matches = await db.scalar(select(func.count()).select_from(PvPMatch)) or 0
        pvp_players = await db.scalar(select(func.count()).select_from(PvPRating)) or 0
    except Exception as exc:
        logger.warning("PvP stats unavailable for admin overview: %s", exc)

    return {
        "users": {
            "total": int(total_users),
            "active": int(active_users),
            "admin": int(admin_users),
            "banned": int(banned_users),
            "latest_created_at": _as_iso(latest_user_created),
        },
        "questions": {
            "total": int(total_questions),
            "llm_generated": int(source_summary["generated"]),
            "generated": int(source_summary["generated"]),
            "seeded": int(source_summary["seeded"]),
            "admin": int(source_summary["admin"]),
            "unknown": int(source_summary["unknown"]),
            "by_category": source_summary["by_category"],
            "by_source": source_summary["by_source"],
            "cached": int(cached_questions),
            "latest_created_at": _as_iso(latest_question_created),
        },
        "sessions": {
            "classic": int(classic_sessions),
            "challenge": int(challenge_sessions),
            "custom": int(custom_sessions),
            "pvp": int(pvp_matches),
        },
        "concepts": {
            "total": int(total_concepts),
            "mastery_rows": int(concept_mastery_rows),
        },
        "responses": {
            "total": int(total_responses),
        },
        "pvp": {
            "total_matches": int(pvp_matches),
            "rated_players": int(pvp_players),
        },
    }


@admin_router.get("/custom-topics/candidates")
async def admin_custom_topic_candidates(
    limit: int = Query(default=50, ge=1, le=200),
    current=Depends(get_admin_read_access),
    db: AsyncSession = Depends(get_db),
):
    """Return topic candidates that admins can approve as Custom Room topics."""
    _require_admin(current)

    approved_rows = await db.execute(select(Topic.slug, Topic.is_active, Topic.total_facts_count))
    approved_info = {
        str(row[0]): {"is_active": bool(row[1]), "total_facts_count": int(row[2] or 0)} 
        for row in approved_rows.fetchall()
    }
    approved_slugs = set(approved_info.keys())

    family_counts = {
        str(row[0] or "").lower(): int(row[1] or 0)
        for row in (
            await db.execute(
                select(func.lower(QuestionBank.topic), func.count())
                .group_by(func.lower(QuestionBank.topic))
            )
        ).fetchall()
    }

    seen: set[str] = set()
    items: list[dict] = []

    for topic in TOPIC_CATALOGUE:
        slug = str(topic["slug"])
        if slug in seen:
            continue
        seen.add(slug)
        family = _topic_family(str(topic["type"]))
        catalogue_state = _catalogue_topic_state(slug, topic, approved_info)
        items.append(
            {
                "type": topic["type"],
                "name": topic["name"],
                "slug": slug,
                "description": topic["description"],
                "source_topic": topic["type"],
                "available_question_count": family_counts.get(family, 0),
                **catalogue_state,
                "candidate_source": "catalogue",
            }
        )

    topic_counts = await db.execute(
        select(QuestionBank.topic, func.count())
        .group_by(QuestionBank.topic)
        .order_by(func.count().desc())
        .limit(limit)
    )
    for raw_topic, count in topic_counts.fetchall():
        topic_type = (raw_topic or "Mixed").strip().title()
        name = topic_type
        slug = _slugify_topic(f"{topic_type}-{name}")
        if slug in seen:
            continue
        seen.add(slug)
        items.append(
            {
                "type": topic_type,
                "name": name,
                "slug": slug,
                "description": f"Approved custom topic generated from existing {topic_type} question-bank coverage.",
                "source_topic": raw_topic,
                "available_question_count": int(count or 0),
                "approved": slug in approved_slugs,
                "is_active": approved_info[slug]["is_active"] if slug in approved_slugs else True,
                "total_facts_count": approved_info[slug]["total_facts_count"] if slug in approved_slugs else 0,
                "candidate_source": "question_bank",
            }
        )

    items.sort(key=lambda item: (-int(item["available_question_count"]), item["type"], item["name"]))
    return {"items": items[:limit], "total": min(len(items), limit)}


def _get_topic_keywords(topic_label: str) -> list[str]:
    parts = topic_label.split(" - ", 1)
    detail = parts[-1].strip().lower() if len(parts) > 1 else topic_label.strip().lower()
    
    keywords = [detail]
    if "world war ii" in detail or "world war 2" in detail:
        keywords.extend(["world war ii", "wwii", "second world war", "axis", "allied", "d-day", "1939", "1945"])
    elif "world war i" in detail or "world war 1" in detail:
        keywords.extend(["world war i", "wwi", "great war", "trench", "1914", "1918", "somme", "verdun"])
    elif "cold war" in detail:
        keywords.extend(["cold war", "berlin wall", "nato", "soviet", "containment", "khrushchev", "kennedy"])
    elif "ancient rome" in detail or "roman" in detail:
        keywords.extend(["rome", "roman", "caesar", "republic", "empire", "pompey", "augustus"])
    elif "french revolution" in detail:
        keywords.extend(["french revolution", "bastille", "robespierre", "jacobin", "louis xvi", "guillotine"])
    elif "industrial revolution" in detail:
        keywords.extend(["industrial revolution", "steam", "factory", "textile", "coal", "watt"])
    return list(dict.fromkeys(keywords))


@admin_router.post("/custom-topics/toggle-active")
async def admin_toggle_custom_topic_active(
    body: AdminCustomTopicToggleActiveIn,
    current=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Toggle the active/disabled status of a Custom Room topic."""
    _require_admin(current)

    slug = body.slug.strip()
    topic_row = await db.scalar(select(Topic).where(Topic.slug == slug))

    if topic_row is None:
        matching_catalogue = None
        for topic in TOPIC_CATALOGUE:
            if str(topic["slug"]) == slug:
                matching_catalogue = topic
                break

        if matching_catalogue is None:
            raise HTTPException(status_code=404, detail="Topic not found in catalogue or database")

        topic_row = Topic(
            type=matching_catalogue["type"],
            slug=slug,
            name=matching_catalogue["name"],
            description=matching_catalogue["description"],
            total_facts_count=int(matching_catalogue.get("total_facts", 0) or 0),
            is_active=body.is_active,
        )
        db.add(topic_row)
    else:
        topic_row.is_active = body.is_active

    await db.commit()
    return {"slug": slug, "is_active": topic_row.is_active}


@admin_router.post("/custom-topics/approve")
async def admin_approve_custom_topic(
    body: AdminCustomTopicApproveIn,
    current=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve a Custom Room topic and harvest facts from existing questions."""
    _require_admin(current)

    topic_type = (body.type or "").strip().title()
    name = (body.name or "").strip()
    if not topic_type or not name:
        raise HTTPException(status_code=400, detail="type and name are required")

    slug = _slugify_topic(body.slug or f"{topic_type}-{name}")
    topic_label = _topic_label(topic_type, name)
    source_topic = (body.source_topic or topic_type).strip()
    source_family = _topic_family(source_topic)
    source_exact = source_topic.lower()

    topic_row = await db.scalar(select(Topic).where(Topic.slug == slug))
    created_topic = False
    if topic_row is None:
        topic_row = Topic(
            type=topic_type,
            slug=slug,
            name=name,
            description=body.description or f"Admin-approved custom room for {topic_label}.",
            total_facts_count=0,
        )
        db.add(topic_row)
        created_topic = True
    else:
        topic_row.type = topic_type
        topic_row.name = name
        if body.description is not None:
            topic_row.description = body.description

    facts_created = 0

    # 1. Custom Manual Context Parsing
    if body.context and len(body.context.strip()) > 5:
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+|\n+', body.context) if len(s.strip()) > 10]
        for sentence in sentences:
            duplicate_content = await db.scalar(
                select(Fact).where(Fact.topic == topic_label, Fact.content == sentence)
            )
            if duplicate_content is None:
                db.add(
                    Fact(
                        topic=topic_label,
                        content=sentence,
                        difficulty_hint="medium",
                    )
                )
                facts_created += 1

    # 2. Automated Fact Harvesting
    if facts_created < body.max_facts:
        remaining_facts_needed = body.max_facts - facts_created
        # Only exclude questions harvested FOR THIS SPECIFIC TOPIC to allow fact re-use
        harvested_q_ids_query = select(Fact.source_question_id).where(
            Fact.source_question_id.is_not(None),
            Fact.topic == topic_label
        )

        base_qb_query = select(QuestionBank).where(
            QuestionBank.question_text.is_not(None),
            QuestionBank.correct_answer.is_not(None),
            QuestionBank.options_json.is_not(None),
            QuestionBank.gov_approved == True,  # noqa: E712
            QuestionBank.gov_safe == True,  # noqa: E712
            QuestionBank.id.notin_(harvested_q_ids_query)
        )

        # Priority 1: Match specifically by sub_topic (e.g. "World War I", "Ancient Rome")
        subtopic_qb_query = base_qb_query.where(
            or_(
                func.lower(QuestionBank.sub_topic) == name.lower(),
                func.lower(QuestionBank.sub_topic) == slug.lower()
            )
        )
        subtopic_rows = await db.execute(
            subtopic_qb_query
            .order_by(QuestionBank.times_seen.desc(), QuestionBank.created_at.desc())
            .limit(remaining_facts_needed)
        )
        questions = list(subtopic_rows.scalars().all())

        # Priority 2: Match by keywords if we need more facts
        if len(questions) < remaining_facts_needed:
            already_selected_ids = [q.id for q in questions]
            
            keywords = _get_topic_keywords(topic_label)
            keyword_filters = []
            for kw in keywords:
                pattern = f"%{kw}%"
                keyword_filters.append(QuestionBank.question_text.ilike(pattern))
                keyword_filters.append(QuestionBank.explanation.ilike(pattern))
                keyword_filters.append(QuestionBank.correct_answer.ilike(pattern))

            keyword_qb_query = base_qb_query.where(
                or_(
                    func.lower(QuestionBank.topic) == source_family,
                    func.lower(QuestionBank.topic) == source_exact,
                )
            ).where(or_(*keyword_filters))
            
            if already_selected_ids:
                keyword_qb_query = keyword_qb_query.where(QuestionBank.id.notin_(already_selected_ids))

            keyword_rows = await db.execute(
                keyword_qb_query
                .order_by(QuestionBank.times_seen.desc(), QuestionBank.created_at.desc())
                .limit(remaining_facts_needed - len(questions))
            )
            questions.extend(keyword_rows.scalars().all())

        # Priority 3: Fallback to any questions in the general topic family/exact
        if len(questions) < remaining_facts_needed:
            already_selected_ids = [q.id for q in questions]
            
            fallback_qb_query = base_qb_query.where(
                or_(
                    func.lower(QuestionBank.topic) == source_family,
                    func.lower(QuestionBank.topic) == source_exact,
                )
            )
            if already_selected_ids:
                fallback_qb_query = fallback_qb_query.where(QuestionBank.id.notin_(already_selected_ids))

            fallback_question_rows = await db.execute(
                fallback_qb_query
                .order_by(QuestionBank.times_seen.desc(), QuestionBank.created_at.desc())
                .limit(remaining_facts_needed - len(questions))
            )
            questions.extend(fallback_question_rows.scalars().all())

        for question in questions:
            content = _fact_content_from_question(question)
            duplicate_content = await db.scalar(
                select(Fact).where(Fact.topic == topic_label, Fact.content == content)
            )
            if duplicate_content is not None:
                continue
            db.add(
                Fact(
                    topic=topic_label,
                    content=content,
                    difficulty_hint=_difficulty_hint_from_question(question),
                    source_question_id=question.id,
                )
            )
            facts_created += 1

    await db.flush()
    total_facts = await db.scalar(
        select(func.count()).select_from(Fact).where(Fact.topic == topic_label)
    ) or 0
    topic_row.total_facts_count = int(total_facts)
    await db.commit()
    await db.refresh(topic_row)

    return {
        "success": True,
        "created_topic": created_topic,
        "slug": topic_row.slug,
        "type": topic_row.type,
        "name": topic_row.name,
        "topic": topic_label,
        "facts_created": facts_created,
        "total_facts": int(topic_row.total_facts_count or 0),
    }


@admin_router.get("/analytics/daily")
# Return graph-ready daily admin analytics.
async def admin_daily_analytics(
    days: int = Query(default=14, ge=1, le=90),
    current=Depends(get_admin_read_access),
    db: AsyncSession = Depends(get_db),
):
    """Daily metrics for dashboard charts and trend analysis."""
    if current is not None:
        _require_admin(current)

    safe_days = max(1, min(int(days), 90))
    now = datetime.utcnow().replace(microsecond=0)
    start_dt = (now - timedelta(days=safe_days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

    def _coerce_day(value) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        return value

    def _rows_to_count_map(rows) -> dict[date, int]:
        output: dict[date, int] = {}
        for day_value, count in rows:
            key = _coerce_day(day_value)
            if key is None:
                continue
            output[key] = int(count or 0)
        return output

    users_rows = await db.execute(
        select(func.date(User.created_at), func.count(User.id))
        .where(User.created_at >= start_dt)
        .group_by(func.date(User.created_at))
    )
    users_map = _rows_to_count_map(users_rows.all())

    response_rows = await db.execute(
        select(
            func.date(UserResponse.created_at),
            func.count(UserResponse.id),
            func.coalesce(func.sum(cast(UserResponse.answered_correct, Integer)), 0),
        )
        .where(UserResponse.created_at >= start_dt)
        .group_by(func.date(UserResponse.created_at))
    )
    responses_map: dict[date, int] = {}
    correct_map: dict[date, int] = {}
    for day_value, total_count, correct_count in response_rows.all():
        key = _coerce_day(day_value)
        if key is None:
            continue
        responses_map[key] = int(total_count or 0)
        correct_map[key] = int(correct_count or 0)

    classic_rows = await db.execute(
        select(func.date(ClassicSession.created_at), func.count(ClassicSession.id))
        .where(ClassicSession.created_at >= start_dt)
        .group_by(func.date(ClassicSession.created_at))
    )
    classic_map = _rows_to_count_map(classic_rows.all())

    challenge_rows = await db.execute(
        select(func.date(ChallengeSession.started_at), func.count(ChallengeSession.id))
        .where(ChallengeSession.started_at >= start_dt)
        .group_by(func.date(ChallengeSession.started_at))
    )
    challenge_map = _rows_to_count_map(challenge_rows.all())

    custom_rows = await db.execute(
        select(func.date(CustomSession.started_at), func.count(CustomSession.id))
        .where(CustomSession.started_at >= start_dt)
        .group_by(func.date(CustomSession.started_at))
    )
    custom_map = _rows_to_count_map(custom_rows.all())

    pvp_map: dict[date, int] = {}
    try:
        from database.pvp_models import PvPMatch

        pvp_rows = await db.execute(
            select(func.date(PvPMatch.started_at), func.count(PvPMatch.id))
            .where(PvPMatch.started_at >= start_dt)
            .group_by(func.date(PvPMatch.started_at))
        )
        pvp_map = _rows_to_count_map(pvp_rows.all())
    except Exception as exc:
        logger.warning("PvP daily analytics unavailable: %s", exc)

    top_rows = await db.execute(
        select(
            User.id,
            User.username,
            User.email,
            User.points,
            func.count(UserResponse.id).label("responses"),
            func.coalesce(func.sum(cast(UserResponse.answered_correct, Integer)), 0).label("correct"),
        )
        .outerjoin(UserResponse, UserResponse.user_id == User.id)
        .group_by(User.id, User.username, User.email, User.points)
        .order_by(func.count(UserResponse.id).desc(), User.points.desc())
        .limit(8)
    )
    top_users = []
    for user_id, username, email, points, responses, correct in top_rows.all():
        total_responses = int(responses or 0)
        total_correct = int(correct or 0)
        accuracy = round((total_correct / max(total_responses, 1)) * 100, 1) if total_responses else 0.0
        display_name = (
            (username or "").replace("_", " ").replace(".", " ").strip().title()
            if (username or "").strip()
            else (email or "").split("@", 1)[0].replace("_", " ").replace(".", " ").strip().title()
        ) or f"user-{str(user_id)[:8]}"
        top_users.append(
            {
                "user_id": str(user_id),
                "display_name": display_name,
                "username": username,
                "email": email,
                "points": int(points or 0),
                "responses": total_responses,
                "correct": total_correct,
                "accuracy": accuracy,
            }
        )

    items = []
    totals = {
        "new_users": 0,
        "responses": 0,
        "correct": 0,
        "classic_sessions": 0,
        "challenge_sessions": 0,
        "custom_sessions": 0,
        "pvp_matches": 0,
    }

    for idx in range(safe_days):
        day = (start_dt + timedelta(days=idx)).date()
        row = {
            "date": day.isoformat(),
            "new_users": int(users_map.get(day, 0)),
            "responses": int(responses_map.get(day, 0)),
            "correct": int(correct_map.get(day, 0)),
            "classic_sessions": int(classic_map.get(day, 0)),
            "challenge_sessions": int(challenge_map.get(day, 0)),
            "custom_sessions": int(custom_map.get(day, 0)),
            "pvp_matches": int(pvp_map.get(day, 0)),
        }
        totals["new_users"] += row["new_users"]
        totals["responses"] += row["responses"]
        totals["correct"] += row["correct"]
        totals["classic_sessions"] += row["classic_sessions"]
        totals["challenge_sessions"] += row["challenge_sessions"]
        totals["custom_sessions"] += row["custom_sessions"]
        totals["pvp_matches"] += row["pvp_matches"]
        items.append(row)

    return {
        "days": safe_days,
        "start_date": items[0]["date"] if items else None,
        "end_date": items[-1]["date"] if items else None,
        "items": items,
        "totals": totals,
        "top_users": top_users,
    }


# -
# TOP CONCEPTS
# -


@admin_router.get("/top-concepts")
# Return concepts ordered by tracking volume and average mastery.
async def admin_top_concepts(
    limit: int = Query(default=10, ge=1, le=50),
    current=Depends(get_admin_read_access),
    db: AsyncSession = Depends(get_db),
):
    """Most-tracked concepts by user engagement count.

    Returns concepts ranked by number of users who have interacted with them.
    """
    if current is not None:
        _require_admin(current)

    rows = await db.execute(
        select(
            Concept.id,
            Concept.name,
            Concept.topic,
            Concept.scope,
            func.count(UserConceptTheta.id).label("tracked_users"),
            func.avg(UserConceptTheta.theta).label("avg_theta"),
        )
        .outerjoin(UserConceptTheta, UserConceptTheta.concept_id == Concept.id)
        .group_by(Concept.id, Concept.name, Concept.topic, Concept.scope)
        .order_by(func.count(UserConceptTheta.id).desc(), Concept.name.asc())
        .limit(limit)
    )

    items = []
    for concept_id, name, topic, scope, tracked_users, avg_theta in rows.all():
        items.append({
            "concept_id": str(concept_id),
            "name": name,
            "topic": topic,
            "scope": scope,
            "tracked_users": int(tracked_users or 0),
            "avg_theta": round(float(avg_theta or 0.0), 3),
        })

    return {"items": items}

# -
# CONCEPTS (FULL TABLE)
# -


@admin_router.get("/concepts")
# Return paginated concept catalog with aggregate usage metadata.
async def admin_list_concepts(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    topic: Optional[str] = Query(default=None),
    sort_by: str = Query(default="tracked_users"),
    current=Depends(get_admin_read_access),
    db: AsyncSession = Depends(get_db),
):
    """Paginated list of all concepts with comprehensive stats."""
    if current is not None:
        _require_admin(current)

    base_query = select(Concept)
    count_query = select(func.count()).select_from(Concept)

    if topic:
        topic_filter = Concept.topic.ilike(f"%{topic}%")
        base_query = base_query.where(topic_filter)
        count_query = count_query.where(topic_filter)

    result = await db.execute(count_query)
    total = result.scalar() or 0

    result = await db.execute(
        base_query.order_by(Concept.name.asc()).offset((page - 1) * per_page).limit(per_page)
    )
    concepts = result.scalars().all()

    concept_ids = [c.id for c in concepts]
    tracked_map: dict[str, tuple[int, float]] = {}
    tagged_map: dict[str, int] = {}
    mastery_map: dict[str, dict[str, int]] = {}

    if concept_ids:
        tracked_rows = await db.execute(
            select(
                UserConceptTheta.concept_id,
                func.count(UserConceptTheta.id).label("tracked_users"),
                func.avg(UserConceptTheta.theta).label("avg_theta"),
            )
            .where(UserConceptTheta.concept_id.in_(concept_ids))
            .group_by(UserConceptTheta.concept_id)
        )
        tracked_map = {
            str(row[0]): (int(row[1] or 0), float(row[2] or 0.0))
            for row in tracked_rows.all()
        }

        tagged_rows = await db.execute(
            select(
                QuestionConcept.concept_id,
                func.count(QuestionConcept.id).label("questions_tagged"),
            )
            .where(QuestionConcept.concept_id.in_(concept_ids))
            .group_by(QuestionConcept.concept_id)
        )
        tagged_map = {
            str(row[0]): int(row[1] or 0)
            for row in tagged_rows.all()
        }

        mastery_rows = await db.execute(
            select(
                UserConceptTheta.concept_id,
                UserConceptTheta.mastery_level,
                func.count(UserConceptTheta.id).label("count"),
            )
            .where(UserConceptTheta.concept_id.in_(concept_ids))
            .group_by(UserConceptTheta.concept_id, UserConceptTheta.mastery_level)
        )
        for concept_id, mastery_level, count in mastery_rows.all():
            cid = str(concept_id)
            bucket = mastery_map.setdefault(cid, {})
            bucket[str(mastery_level)] = int(count or 0)

    items = []
    for concept in concepts:
        cid = str(concept.id)
        tracked_users, avg_theta = tracked_map.get(cid, (0, 0.0))
        items.append({
            "concept_id": cid,
            "name": concept.name,
            "topic": concept.topic,
            "scope": concept.scope,
            "description": concept.description or "",
            "tracked_users": tracked_users,
            "avg_theta": round(float(avg_theta), 3),
            "questions_tagged": tagged_map.get(cid, 0),
            "mastery_distribution": mastery_map.get(cid, {}),
            "created_at": _as_iso(concept.created_at),
        })

    sort_key = (sort_by or "tracked_users").strip().lower()
    if sort_key == "name":
        items.sort(key=lambda item: str(item.get("name") or "").lower())
    elif sort_key == "topic":
        items.sort(key=lambda item: str(item.get("topic") or "").lower())
    elif sort_key == "avg_theta":
        items.sort(key=lambda item: float(item.get("avg_theta") or 0.0), reverse=True)
    elif sort_key == "questions_tagged":
        items.sort(key=lambda item: int(item.get("questions_tagged") or 0), reverse=True)
    else:
        items.sort(key=lambda item: int(item.get("tracked_users") or 0), reverse=True)

    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@admin_router.get("/concepts/{concept_id}")
# Return one concept with mastery, question, and repeat-queue details.
async def admin_concept_detail(
    concept_id: str,
    current=Depends(get_admin_read_access),
    db: AsyncSession = Depends(get_db),
):
    """Detailed view of a single concept with all tracking data.

    Returns concept info, user mastery breakdown, tagged questions, repeat queue stats.
    """
    if current is not None:
        _require_admin(current)

    import uuid
    try:
        cid = uuid.UUID(concept_id)
    except ValueError:
        raise HTTPException(422, "Invalid concept ID")

    concept = await db.get(Concept, cid)
    if not concept:
        raise HTTPException(404, "Concept not found")

    # User mastery breakdown
    mastery_rows = await db.execute(
        select(
            UserConceptTheta.mastery_level,
            func.count(UserConceptTheta.id).label("count"),
            func.avg(UserConceptTheta.theta).label("avg_theta"),
            func.avg(UserConceptTheta.response_count).label("avg_responses"),
        )
        .where(UserConceptTheta.concept_id == cid)
        .group_by(UserConceptTheta.mastery_level)
        .order_by(UserConceptTheta.mastery_level.desc())
    )
    mastery_breakdown = [
        {
            "mastery_level": row[0],
            "user_count": int(row[1]),
            "avg_theta": round(float(row[2] or 0.0), 3),
            "avg_responses": int(row[3] or 0),
        }
        for row in mastery_rows.all()
    ]

    # Tagged questions sample
    question_rows = await db.execute(
        select(QuestionBank, QuestionConcept.is_primary)
        .join(QuestionConcept, QuestionBank.id == QuestionConcept.question_id)
        .where(QuestionConcept.concept_id == cid)
        .order_by(QuestionBank.created_at.desc())
        .limit(10)
    )
    questions = [
        {
            "question_id": str(q[0].id),
            "text": q[0].question_text[:100],
            "difficulty": round(float(q[0].difficulty_irt or 0.0), 2),
            "is_primary": bool(q[1]),
            "times_seen": q[0].times_seen or 0,
        }
        for q in question_rows.all()
    ]

    # Repeat queue stats
    repeat_count = await db.scalar(
        select(func.count()).select_from(UserConceptRepeatQueue)
        .where(UserConceptRepeatQueue.concept_id == cid)
    ) or 0

    repeat_users = await db.scalar(
        select(func.count(UserConceptRepeatQueue.user_id.distinct()))
        .select_from(UserConceptRepeatQueue)
        .where(UserConceptRepeatQueue.concept_id == cid)
    ) or 0

    # Stats
    tracked_users = await db.scalar(
        select(func.count()).select_from(UserConceptTheta)
        .where(UserConceptTheta.concept_id == cid)
    ) or 0

    questions_tagged = await db.scalar(
        select(func.count()).select_from(QuestionConcept)
        .where(QuestionConcept.concept_id == cid)
    ) or 0

    return {
        "concept": {
            "id": str(concept.id),
            "name": concept.name,
            "topic": concept.topic,
            "scope": concept.scope,
            "description": concept.description or "",
            "created_at": _as_iso(concept.created_at),
        },
        "stats": {
            "tracked_users": int(tracked_users),
            "questions_tagged": int(questions_tagged),
            "repeat_queue_count": int(repeat_count),
            "users_with_repeats": int(repeat_users),
        },
        "mastery_breakdown": mastery_breakdown,
        "sample_questions": questions,
    }


# -
# USERS
# -


@admin_router.post("/concepts")
# Create a concept entry for admin-managed concept catalog updates.
async def admin_create_concept(
    body: AdminConceptCreateIn,
    current=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current)

    name = _normalize_text(body.name)
    topic = _normalize_text(body.topic)
    scope = _normalize_text(body.scope) or "general"
    description = _normalize_text(body.description)
    if not name or not topic:
        raise HTTPException(status_code=400, detail="Concept name and topic are required")

    existing = await db.scalar(
        select(Concept).where(
            func.lower(Concept.name) == name.lower(),
            func.lower(Concept.topic) == topic.lower(),
            func.lower(Concept.scope) == scope.lower(),
        )
    )
    if existing:
        raise HTTPException(status_code=400, detail="Concept already exists for this topic and scope")

    concept = Concept(name=name, topic=topic, scope=scope, description=description)
    db.add(concept)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Concept could not be created")
    await db.refresh(concept)

    return {
        "concept_id": str(concept.id),
        "name": concept.name,
        "topic": concept.topic,
        "scope": concept.scope,
        "description": concept.description or "",
        "created_at": _as_iso(concept.created_at),
    }


@admin_router.patch("/concepts/{concept_id}")
# Edit one concept's metadata.
async def admin_update_concept(
    concept_id: str,
    body: AdminConceptUpdateIn,
    current=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current)
    cid = _parse_uuid_or_422(concept_id, "concept_id")
    assert cid is not None

    concept = await db.get(Concept, cid)
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")

    changed = False
    next_name = _normalize_text(body.name)
    next_topic = _normalize_text(body.topic)
    next_scope = _normalize_text(body.scope)
    candidate_name = next_name if next_name is not None else concept.name
    candidate_topic = next_topic if next_topic is not None else concept.topic
    candidate_scope = next_scope if next_scope is not None else concept.scope
    identity_changed = (
        candidate_name != concept.name
        or candidate_topic != concept.topic
        or candidate_scope != concept.scope
    )
    if identity_changed:
        existing = await db.scalar(
            select(Concept).where(
                func.lower(Concept.name) == candidate_name.lower(),
                func.lower(Concept.topic) == candidate_topic.lower(),
                func.lower(Concept.scope) == candidate_scope.lower(),
                Concept.id != concept.id,
            )
        )
        if existing:
            raise HTTPException(status_code=400, detail="Concept already exists for this topic and scope")

    if next_name is not None and next_name != concept.name:
        concept.name = next_name
        changed = True

    if next_topic is not None and next_topic != concept.topic:
        concept.topic = next_topic
        changed = True

    if next_scope is not None and next_scope != concept.scope:
        concept.scope = next_scope
        changed = True

    if body.description is not None:
        next_description = _normalize_text(body.description)
        if (concept.description or None) != next_description:
            concept.description = next_description
            changed = True

    if changed:
        await db.commit()
        await db.refresh(concept)

    return {
        "concept_id": str(concept.id),
        "name": concept.name,
        "topic": concept.topic,
        "scope": concept.scope,
        "description": concept.description or "",
        "created_at": _as_iso(concept.created_at),
        "changed": changed,
    }


@admin_router.delete("/concepts/{concept_id}")
# Delete concept with optional cascade cleanup of dependent rows.
async def admin_delete_concept(
    concept_id: str,
    force: bool = Query(default=False),
    current=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current)
    cid = _parse_uuid_or_422(concept_id, "concept_id")
    assert cid is not None

    concept = await db.get(Concept, cid)
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")

    tagged_count = await db.scalar(
        select(func.count()).select_from(QuestionConcept).where(QuestionConcept.concept_id == cid)
    ) or 0
    mastery_count = await db.scalar(
        select(func.count()).select_from(UserConceptTheta).where(UserConceptTheta.concept_id == cid)
    ) or 0
    queue_count = await db.scalar(
        select(func.count()).select_from(UserConceptRepeatQueue).where(UserConceptRepeatQueue.concept_id == cid)
    ) or 0

    blockers = int(tagged_count) + int(mastery_count) + int(queue_count)
    if blockers > 0 and not force:
        raise HTTPException(
            status_code=409,
            detail=(
                "Concept has dependent rows. Re-run with force=true to delete. "
                f"(question_links={tagged_count}, mastery_rows={mastery_count}, repeat_rows={queue_count})"
            ),
        )

    if force:
        await db.execute(
            update(QuestionBank)
            .where(QuestionBank.primary_concept_id == cid)
            .values(primary_concept_id=None)
        )
        await db.execute(delete(QuestionConcept).where(QuestionConcept.concept_id == cid))
        await db.execute(delete(UserConceptRepeatQueue).where(UserConceptRepeatQueue.concept_id == cid))
        await db.execute(delete(UserConceptTheta).where(UserConceptTheta.concept_id == cid))

    await db.delete(concept)
    await db.commit()
    return {"success": True, "concept_id": concept_id, "force": bool(force)}


@admin_router.get("/users")
# Return paginated users for admin browsing.
async def admin_list_users(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    search: Optional[str] = Query(default=None),
    current=Depends(get_admin_read_access),
    db: AsyncSession = Depends(get_db),
):
    """Paginated list of all users with search by email/username.

    Returns:
        {items: [...], total: int, page: int, per_page: int}
    """
    if current is not None:
        _require_admin(current)

    query = select(User)
    count_query = select(func.count()).select_from(User)

    if search:
        search_filter = (User.email.ilike(f"%{search}%")) | (User.username.ilike(f"%{search}%"))
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    total = await db.scalar(count_query) or 0
    offset = (page - 1) * per_page

    result = await db.execute(
        query.order_by(User.created_at.desc()).offset(offset).limit(per_page)
    )
    users = result.scalars().all()

    return {
        "items": [
            {
                "id": str(u.id),
                "email": u.email,
                "username": u.username,
                "display_name": _display_name_for_user(u),
                "points": u.points or 0,
                "level": u.level or "Novice",
                "is_active": bool(u.is_active),
                "is_admin": bool(getattr(u, "is_admin", False)),
                "ban_until": _as_iso(getattr(u, "ban_until", None)),
                "ban_reason": (getattr(u, "ban_reason", None) or "").strip() or None,
                "is_banned_now": bool(getattr(u, "ban_until", None) and u.ban_until > datetime.utcnow()),
                "created_at": _as_iso(u.created_at),
                "last_login": _as_iso(getattr(u, "last_login", None)),
            }
            for u in users
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@admin_router.get("/users/{user_id}")
# Return one user profile with related learning/session detail.
async def admin_user_detail(
    user_id: str,
    current=Depends(get_admin_read_access),
    db: AsyncSession = Depends(get_db),
):
    """Detailed user profile with sessions and concept mastery.

    Returns user info, their recent sessions, and concept tracking data.
    """
    if current is not None:
        _require_admin(current)

    import uuid
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(422, "Invalid user ID")

    user = await db.get(User, uid)
    if not user:
        raise HTTPException(404, "User not found")

    # Get concept mastery
    mastery_rows = await db.execute(
        select(UserConceptTheta, Concept)
        .join(Concept, UserConceptTheta.concept_id == Concept.id)
        .where(UserConceptTheta.user_id == uid)
        .order_by(UserConceptTheta.theta.desc())
        .limit(20)
    )
    concepts = [
        {
            "concept": c.name,
            "topic": c.topic,
            "theta": round(float(t.theta), 3),
            "responses": t.response_count,
            "mastery": t.mastery_level,
        }
        for t, c in mastery_rows.all()
    ]

    # Get recent responses
    response_count = await db.scalar(
        select(func.count()).select_from(UserResponse).where(UserResponse.user_id == uid)
    ) or 0

    correct_count = await db.scalar(
        select(func.count()).select_from(UserResponse).where(
            UserResponse.user_id == uid,
            UserResponse.answered_correct == True,
        )
    ) or 0

    # Get challenge sessions
    challenge_count = await db.scalar(
        select(func.count()).select_from(ChallengeSession).where(ChallengeSession.user_id == uid)
    ) or 0

    # Get custom sessions
    custom_count = await db.scalar(
        select(func.count()).select_from(CustomSession).where(CustomSession.user_id == uid)
    ) or 0

    # Get classic sessions
    classic_count = await db.scalar(
        select(func.count()).select_from(ClassicSession).where(ClassicSession.user_id == uid)
    ) or 0

    # Usage by question source (llm/challenge_llm/custom_llm/etc.)
    source_usage_rows = await db.execute(
        select(
            QuestionBank.source,
            func.count(UserResponse.id).label("count"),
        )
        .select_from(UserResponse)
        .outerjoin(QuestionBank, QuestionBank.id == UserResponse.question_id)
        .where(UserResponse.user_id == uid)
        .group_by(QuestionBank.source)
        .order_by(func.count(UserResponse.id).desc())
        .limit(20)
    )
    source_usage = [
        {
            "source": source or "unknown",
            "count": int(count or 0),
        }
        for source, count in source_usage_rows.all()
    ]

    # Usage by topic from submitted responses.
    topic_usage_rows = await db.execute(
        select(
            UserResponse.topic,
            func.count(UserResponse.id).label("count"),
        )
        .where(UserResponse.user_id == uid)
        .group_by(UserResponse.topic)
        .order_by(func.count(UserResponse.id).desc())
        .limit(20)
    )
    topic_usage = [
        {
            "topic": topic or "unknown",
            "count": int(count or 0),
        }
        for topic, count in topic_usage_rows.all()
    ]

    # Recent question usage feed with question metadata when available.
    recent_usage_rows = await db.execute(
        select(
            UserResponse.id,
            UserResponse.question_id,
            UserResponse.topic,
            UserResponse.difficulty_sent,
            UserResponse.answered_correct,
            UserResponse.used_hint,
            UserResponse.time_taken,
            UserResponse.created_at,
            QuestionBank.question_text,
            QuestionBank.source,
        )
        .select_from(UserResponse)
        .outerjoin(QuestionBank, QuestionBank.id == UserResponse.question_id)
        .where(UserResponse.user_id == uid)
        .order_by(UserResponse.created_at.desc())
        .limit(50)
    )
    recent_usage = [
        {
            "response_id": str(response_id),
            "question_id": str(question_id),
            "topic": topic,
            "difficulty_sent": int(difficulty_sent or 0),
            "answered_correct": bool(answered_correct),
            "used_hint": bool(used_hint),
            "time_taken": int(time_taken or 0),
            "answered_at": _as_iso(created_at),
            "question_text": question_text or "",
            "source": source or "unknown",
        }
        for (
            response_id,
            question_id,
            topic,
            difficulty_sent,
            answered_correct,
            used_hint,
            time_taken,
            created_at,
            question_text,
            source,
        ) in recent_usage_rows.all()
    ]

    # Recent sessions across room types.
    recent_sessions: list[dict] = []

    classic_rows = await db.execute(
        select(ClassicSession)
        .where(ClassicSession.user_id == uid)
        .order_by(ClassicSession.created_at.desc())
        .limit(10)
    )
    for row in classic_rows.scalars().all():
        started_at = row.created_at
        recent_sessions.append(
            {
                "type": "classic",
                "session_id": str(row.id),
                "topic": row.topic,
                "questions": int(row.questions_answered or 0),
                "correct": int(row.correct_count or 0),
                "started_at": _as_iso(started_at),
                "is_completed": row.ended_at is not None,
                "_sort": started_at,
            }
        )

    challenge_rows = await db.execute(
        select(ChallengeSession)
        .where(ChallengeSession.user_id == uid)
        .order_by(ChallengeSession.started_at.desc())
        .limit(10)
    )
    for row in challenge_rows.scalars().all():
        started_at = row.started_at
        recent_sessions.append(
            {
                "type": "challenge",
                "session_id": str(row.id),
                "topic": row.topic,
                "questions": int(row.total_questions or 0),
                "correct": int(row.correct_answers or 0),
                "started_at": _as_iso(started_at),
                "is_completed": bool(row.is_completed),
                "_sort": started_at,
            }
        )

    custom_rows = await db.execute(
        select(CustomSession)
        .where(CustomSession.user_id == uid)
        .order_by(CustomSession.started_at.desc())
        .limit(10)
    )
    for row in custom_rows.scalars().all():
        started_at = row.started_at
        recent_sessions.append(
            {
                "type": "custom",
                "session_id": str(row.id),
                "topic": row.topic,
                "questions": int(row.total_questions or 0),
                "correct": int(row.correct_count or 0),
                "started_at": _as_iso(started_at),
                "is_completed": row.ended_at is not None,
                "_sort": started_at,
            }
        )

    pvp_stats = {
        "enabled": False,
        "matches": 0,
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "elo_rating": None,
    }

    try:
        from database.pvp_models import PvPMatch, PvPRating

        pvp_stats["enabled"] = True

        pvp_rating = (
            await db.execute(select(PvPRating).where(PvPRating.user_id == uid))
        ).scalar_one_or_none()
        if pvp_rating is not None:
            pvp_stats["elo_rating"] = float(pvp_rating.elo_rating or 0.0)

        pvp_matches = (
            await db.execute(
                select(PvPMatch)
                .where(or_(PvPMatch.user1_id == uid, PvPMatch.user2_id == uid))
                .order_by(PvPMatch.started_at.desc())
                .limit(10)
            )
        ).scalars().all()

        wins = 0
        losses = 0
        draws = 0

        for row in pvp_matches:
            started_at = row.started_at
            my_score = int(row.user1_score or 0) if row.user1_id == uid else int(row.user2_score or 0)
            opp_score = int(row.user2_score or 0) if row.user1_id == uid else int(row.user1_score or 0)

            if row.winner_id is None:
                draws += 1
            elif row.winner_id == uid:
                wins += 1
            else:
                losses += 1

            recent_sessions.append(
                {
                    "type": "pvp",
                    "session_id": str(row.id),
                    "topic": row.topic,
                    "questions": int(row.total_questions or 0),
                    "correct": my_score,
                    "opponent_score": opp_score,
                    "started_at": _as_iso(started_at),
                    "is_completed": row.status == "completed",
                    "status": row.status,
                    "_sort": started_at,
                }
            )

        pvp_stats["matches"] = int(len(pvp_matches))
        pvp_stats["wins"] = int(wins)
        pvp_stats["losses"] = int(losses)
        pvp_stats["draws"] = int(draws)
    except Exception as exc:
        logger.warning("PvP user detail unavailable: %s", exc)

    recent_sessions.sort(
        key=lambda item: item.get("_sort") or datetime.min,
        reverse=True,
    )
    recent_sessions = [
        {k: v for k, v in item.items() if k != "_sort"}
        for item in recent_sessions[:20]
    ]

    return {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "username": user.username,
            "display_name": _display_name_for_user(user),
            "points": user.points or 0,
            "level": user.level or "Novice",
            "is_active": bool(user.is_active),
            "is_admin": bool(getattr(user, "is_admin", False)),
            "ban_until": _as_iso(getattr(user, "ban_until", None)),
            "ban_reason": (getattr(user, "ban_reason", None) or "").strip() or None,
            "is_banned_now": bool(getattr(user, "ban_until", None) and user.ban_until > now),
            "created_at": _as_iso(user.created_at),
            "last_login": _as_iso(getattr(user, "last_login", None)),
        },
        "stats": {
            "total_responses": int(response_count),
            "correct_responses": int(correct_count),
            "accuracy": round(correct_count / max(response_count, 1) * 100, 1),
            "classic_sessions": int(classic_count),
            "challenge_sessions": int(challenge_count),
            "custom_sessions": int(custom_count),
        },
        "concept_mastery": concepts,
        "activity": {
            "source_usage": source_usage,
            "topic_usage": topic_usage,
            "recent_usage": recent_usage,
        },
        "sessions": {
            "recent": recent_sessions,
        },
        "pvp": pvp_stats,
    }


@admin_router.patch("/users/{user_id}")
# Update mutable admin-controlled user fields.
async def admin_update_user(
    user_id: str,
    is_active: Optional[bool] = Query(default=None),
    is_admin: Optional[bool] = Query(default=None),
    ban_minutes: Optional[int] = Query(default=None, ge=1, le=525600),
    ban_reason: Optional[str] = Query(default=None, min_length=1, max_length=500),
    clear_ban: bool = Query(default=False),
    body: Optional[AdminUserUpdateIn] = None,
    current=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update admin-managed user controls and profile fields.

    Query params:
        is_active: Set user active status (true/false)
        is_admin: Set user admin status (true/false)
        ban_minutes: Temporarily ban user for N minutes
        ban_reason: Optional reason attached to active temporary ban
        clear_ban: Clear any existing temporary ban

    Optional JSON body fields:
        username, email, level, points
    """
    admin_user = _require_admin(current)

    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(422, "Invalid user ID")

    user = await db.get(User, uid)
    if not user:
        raise HTTPException(404, "User not found")

    effective_is_active = body.is_active if body and body.is_active is not None else is_active
    effective_is_admin = body.is_admin if body and body.is_admin is not None else is_admin
    effective_ban_minutes = body.ban_minutes if body and body.ban_minutes is not None else ban_minutes
    effective_ban_reason = body.ban_reason if body and body.ban_reason is not None else ban_reason
    effective_clear_ban = body.clear_ban if body and body.clear_ban is not None else clear_ban

    changed = False
    changed_fields: list[str] = []

    if body and "username" in body.model_fields_set:
        next_username = _normalize_text(body.username)
        if not next_username or len(next_username) < 3:
            raise HTTPException(status_code=400, detail="Username must be at least 3 characters")
        if next_username != user.username:
            existing_username = await _find_other_user_by_username(db, next_username, user.id)
            if existing_username:
                raise HTTPException(status_code=400, detail="Username already taken")
            user.username = next_username
            changed = True
            changed_fields.append("username")

    if body and "email" in body.model_fields_set:
        next_email = _normalize_email(body.email)
        if not next_email or not _is_valid_email(next_email):
            raise HTTPException(status_code=400, detail="Invalid email")
        if next_email != (user.email or "").lower():
            existing_email = await _find_other_user_by_email(db, next_email, user.id)
            if existing_email:
                raise HTTPException(status_code=400, detail="Email already registered")
            user.email = next_email
            changed = True
            changed_fields.append("email")

    if body and "level" in body.model_fields_set:
        next_level = _normalize_text(body.level)
        if not next_level:
            raise HTTPException(status_code=400, detail="Level cannot be empty")
        if next_level != user.level:
            user.level = next_level
            changed = True
            changed_fields.append("level")

    if body and "points" in body.model_fields_set:
        next_points = int(body.points) if body.points is not None else None
        if next_points is None or next_points < 0:
            raise HTTPException(status_code=400, detail="Points must be zero or greater")
        if int(user.points or 0) != next_points:
            user.points = next_points
            changed = True
            changed_fields.append("points")

    if effective_is_active is not None:
        user.is_active = effective_is_active
        changed = True
        changed_fields.append("is_active")
        logger.info("Admin toggled user %s active=%s", user_id[:8], effective_is_active)

    if effective_is_admin is not None:
        if user.id == admin_user.id and not effective_is_admin:
            raise HTTPException(400, "You cannot remove your own admin access")
        user.is_admin = effective_is_admin
        changed = True
        changed_fields.append("is_admin")
        logger.info("Admin toggled user %s admin=%s", user_id[:8], effective_is_admin)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if effective_clear_ban:
        user.ban_until = None
        user.ban_reason = None
        changed = True
        changed_fields.append("clear_ban")
        logger.info("Admin cleared ban for user %s", user_id[:8])

    if effective_ban_minutes is not None:
        reason = (effective_ban_reason or "Banned by admin").strip()
        user.ban_until = now + timedelta(minutes=int(effective_ban_minutes))
        user.ban_reason = reason
        changed = True
        changed_fields.append("ban_minutes")
        logger.info(
            "Admin banned user %s for %s minutes (reason=%s)",
            user_id[:8],
            effective_ban_minutes,
            reason,
        )
    elif effective_ban_reason is not None and user.ban_until and user.ban_until > now:
        user.ban_reason = effective_ban_reason.strip()
        changed = True
        changed_fields.append("ban_reason")
        logger.info("Admin updated active ban reason for user %s", user_id[:8])

    if changed:
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status_code=400, detail="Username or email already exists")
        await db.refresh(user)
        logger.info(
            "Admin updated user profile target=%s actor=%s fields=%s",
            str(user.id)[:8],
            str(admin_user.id)[:8],
            sorted(set(changed_fields)),
        )

    return {
        "success": True,
        "user_id": user_id,
        "changed": changed,
        "user": {
            "id": str(user.id),
            "email": user.email,
            "username": user.username,
            "display_name": _display_name_for_user(user),
            "points": int(user.points or 0),
            "level": user.level or "Novice",
            "is_active": bool(user.is_active),
            "is_admin": bool(getattr(user, "is_admin", False)),
            "ban_until": _as_iso(getattr(user, "ban_until", None)),
            "ban_reason": (getattr(user, "ban_reason", None) or "").strip() or None,
            "is_banned_now": bool(getattr(user, "ban_until", None) and user.ban_until > now),
        },
    }


# -
# QUESTIONS
# -


@admin_router.get("/questions")
# Return paginated question inventory for diagnostics and governance review.
async def admin_list_questions(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    topic: Optional[str] = Query(default=None),
    current=Depends(get_admin_read_access),
    db: AsyncSession = Depends(get_db),
):
    """Paginated list of all questions with optional topic filter.

    Returns question text, topic, difficulty, and usage stats.
    """
    if current is not None:
        _require_admin(current)

    query = select(
        QuestionBank.id,
        QuestionBank.question_text,
        QuestionBank.correct_answer,
        QuestionBank.options_json,
        QuestionBank.explanation,
        QuestionBank.topic,
        QuestionBank.difficulty_irt,
        QuestionBank.source,
        QuestionBank.usage_count,
        QuestionBank.times_seen,
        QuestionBank.last_served_at,
        QuestionBank.created_at,
        QuestionBank.primary_concept_id,
        QuestionBank.gov_approved,
        QuestionBank.gov_safe,
        QuestionBank.gov_flags_json,
    )
    count_query = select(func.count()).select_from(QuestionBank)

    if topic:
        query = query.where(QuestionBank.topic.ilike(f"%{topic}%"))
        count_query = count_query.where(QuestionBank.topic.ilike(f"%{topic}%"))

    total = await db.scalar(count_query) or 0
    offset = (page - 1) * per_page

    result = await db.execute(
        query.order_by(QuestionBank.created_at.desc()).offset(offset).limit(per_page)
    )
    questions = result.all()

    return {
        "items": [
            {
                "id": str(row[0]),
                "question_text": row[1],
                "text": (row[1] or "")[:100],
                "correct_answer": row[2],
                "options_json": row[3],
                "explanation": row[4],
                "topic": row[5],
                "difficulty_irt": round(float(row[6] or 0.0), 2),
                "source": row[7],
                "usage_count": int(row[8] or 0),
                "times_seen": int(row[9] or 0),
                "last_served_at": _as_iso(row[10]),
                "created_at": _as_iso(row[11]),
                "primary_concept_id": str(row[12]) if row[12] else None,
                "gov_approved": bool(row[13]) if row[13] is not None else None,
                "gov_safe": bool(row[14]) if row[14] is not None else None,
                "gov_flags_json": row[15],
            }
            for row in questions
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# -
# SESSIONS
# -


@admin_router.post("/questions")
# Create a question row for admin curation and moderation workflows.
async def admin_create_question(
    body: AdminQuestionCreateIn,
    current=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current)

    question_text = _normalize_text(body.question_text)
    correct_answer = _normalize_text(body.correct_answer)
    explanation = _normalize_text(body.explanation)
    topic = _normalize_text(body.topic)
    source = _normalize_text(body.source) or "admin"
    options = _normalize_options(body.options)
    primary_concept_id = _parse_uuid_or_422(body.primary_concept_id, "primary_concept_id")

    if not question_text or not correct_answer or not explanation or not topic:
        raise HTTPException(status_code=400, detail="question_text, correct_answer, explanation, and topic are required")

    if primary_concept_id is not None:
        concept = await db.get(Concept, primary_concept_id)
        if not concept:
            raise HTTPException(status_code=404, detail="Primary concept not found")

    row = QuestionBank(
        question_text=question_text,
        correct_answer=correct_answer,
        options_json=json.dumps(options, ensure_ascii=True),
        explanation=explanation,
        topic=topic,
        difficulty_irt=float(body.difficulty_irt),
        source=source,
        primary_concept_id=primary_concept_id,
    )
    db.add(row)
    await db.flush()

    if primary_concept_id is not None:
        db.add(
            QuestionConcept(
                question_id=row.id,
                concept_id=primary_concept_id,
                is_primary=True,
            )
        )

    await db.commit()
    await db.refresh(row)
    return _question_admin_payload(row)


@admin_router.patch("/questions/{question_id}")
# Update an existing admin question.
async def admin_update_question(
    question_id: str,
    body: AdminQuestionUpdateIn,
    current=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current)
    qid = _parse_uuid_or_422(question_id, "question_id")
    assert qid is not None

    row = await db.get(QuestionBank, qid)
    if not row:
        raise HTTPException(status_code=404, detail="Question not found")

    changed = False
    if body.question_text is not None:
        next_question_text = _normalize_text(body.question_text)
        if not next_question_text:
            raise HTTPException(status_code=400, detail="question_text cannot be empty")
        if next_question_text != row.question_text:
            row.question_text = next_question_text
            changed = True

    if body.correct_answer is not None:
        next_correct_answer = _normalize_text(body.correct_answer)
        if not next_correct_answer:
            raise HTTPException(status_code=400, detail="correct_answer cannot be empty")
        if next_correct_answer != row.correct_answer:
            row.correct_answer = next_correct_answer
            changed = True

    if body.options is not None:
        normalized_options = _normalize_options(body.options)
        next_options_json = json.dumps(normalized_options, ensure_ascii=True)
        if next_options_json != (row.options_json or ""):
            row.options_json = next_options_json
            changed = True

    if body.explanation is not None:
        next_explanation = _normalize_text(body.explanation)
        if not next_explanation:
            raise HTTPException(status_code=400, detail="explanation cannot be empty")
        if next_explanation != row.explanation:
            row.explanation = next_explanation
            changed = True

    if body.topic is not None:
        next_topic = _normalize_text(body.topic)
        if not next_topic:
            raise HTTPException(status_code=400, detail="topic cannot be empty")
        if next_topic != row.topic:
            row.topic = next_topic
            changed = True

    if body.difficulty_irt is not None:
        if float(body.difficulty_irt) != float(row.difficulty_irt or 0.0):
            row.difficulty_irt = float(body.difficulty_irt)
            changed = True

    if body.source is not None:
        next_source = _normalize_text(body.source)
        if not next_source:
            raise HTTPException(status_code=400, detail="source cannot be empty")
        if next_source != row.source:
            row.source = next_source
            changed = True

    if "primary_concept_id" in body.model_fields_set:
        next_primary_concept_id = _parse_uuid_or_422(body.primary_concept_id, "primary_concept_id")
        if next_primary_concept_id is not None:
            concept = await db.get(Concept, next_primary_concept_id)
            if not concept:
                raise HTTPException(status_code=404, detail="Primary concept not found")

        if next_primary_concept_id != row.primary_concept_id:
            row.primary_concept_id = next_primary_concept_id
            changed = True

        await db.execute(
            update(QuestionConcept)
            .where(QuestionConcept.question_id == qid)
            .values(is_primary=False)
        )
        if next_primary_concept_id is not None:
            existing_link = await db.scalar(
                select(QuestionConcept).where(
                    QuestionConcept.question_id == qid,
                    QuestionConcept.concept_id == next_primary_concept_id,
                )
            )
            if existing_link:
                existing_link.is_primary = True
            else:
                db.add(
                    QuestionConcept(
                        question_id=qid,
                        concept_id=next_primary_concept_id,
                        is_primary=True,
                    )
                )

    if changed:
        await db.commit()
        await db.refresh(row)

    payload = _question_admin_payload(row)
    payload["changed"] = changed
    return payload


@admin_router.delete("/questions/{question_id}")
# Delete a question row from the bank.
async def admin_delete_question(
    question_id: str,
    current=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current)
    qid = _parse_uuid_or_422(question_id, "question_id")
    assert qid is not None

    row = await db.get(QuestionBank, qid)
    if not row:
        raise HTTPException(status_code=404, detail="Question not found")

    await db.delete(row)
    await db.commit()
    return {"success": True, "question_id": question_id}


@admin_router.get("/sessions")
# Return cross-room session activity feed with optional filters.
async def admin_list_sessions(
    session_type: Optional[str] = Query(default=None, description="classic, challenge, custom, or pvp"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    current=Depends(get_admin_read_access),
    db: AsyncSession = Depends(get_db),
):
    """Combined session list across all room types.

    Filter by session_type (challenge, custom, pvp) or get all.
    """
    if current is not None:
        _require_admin(current)

    normalized_session_type = session_type.lower().strip() if session_type else None
    if normalized_session_type not in (None, "classic", "challenge", "custom", "pvp"):
        raise HTTPException(422, "session_type must be one of: classic, challenge, custom, pvp")

    items = []
    offset = (page - 1) * per_page

    if normalized_session_type in (None, "classic"):
        result = await db.execute(
            select(ClassicSession)
            .order_by(ClassicSession.created_at.desc())
        )
        for s in result.scalars().all():
            items.append({
                "type": "classic",
                "id": str(s.id),
                "user_id": str(s.user_id),
                "topic": s.topic,
                "questions": int(s.questions_answered or 0),
                "correct": int(s.correct_count or 0),
                "started_at": _as_iso(s.created_at),
                "is_completed": s.ended_at is not None,
            })

    if normalized_session_type in (None, "challenge"):
        result = await db.execute(
            select(ChallengeSession)
            .order_by(ChallengeSession.started_at.desc())
        )
        for s in result.scalars().all():
            items.append({
                "type": "challenge",
                "id": str(s.id),
                "user_id": str(s.user_id),
                "topic": s.topic,
                "questions": s.total_questions,
                "correct": s.correct_answers,
                "started_at": _as_iso(s.started_at),
                "is_completed": s.is_completed,
            })

    if normalized_session_type in (None, "custom"):
        result = await db.execute(
            select(CustomSession)
            .order_by(CustomSession.started_at.desc())
        )
        for s in result.scalars().all():
            items.append({
                "type": "custom",
                "id": str(s.id),
                "user_id": str(s.user_id),
                "topic": s.topic,
                "questions": s.total_questions,
                "correct": s.correct_count,
                "started_at": _as_iso(s.started_at),
                "is_completed": s.ended_at is not None,
            })

    if normalized_session_type in (None, "pvp"):
        try:
            from database.pvp_models import PvPMatch

            result = await db.execute(
                select(PvPMatch)
                .order_by(PvPMatch.started_at.desc())
            )
            for s in result.scalars().all():
                items.append({
                    "type": "pvp",
                    "id": str(s.id),
                    "user_id": str(s.user1_id),
                    "topic": s.topic,
                    "questions": s.total_questions,
                    "correct": max(int(s.user1_score or 0), int(s.user2_score or 0)),
                    "started_at": _as_iso(s.started_at),
                    "is_completed": s.status == "completed",
                    "user1_id": str(s.user1_id),
                    "user2_id": str(s.user2_id),
                    "user1_score": int(s.user1_score or 0),
                    "user2_score": int(s.user2_score or 0),
                    "winner_id": str(s.winner_id) if s.winner_id else None,
                    "status": s.status,
                })
        except Exception as exc:
            logger.warning("PvP sessions unavailable for admin sessions list: %s", exc)

    user_uuid_set: set[uuid.UUID] = set()
    for item in items:
        for key in ("user_id", "user1_id", "user2_id"):
            raw = item.get(key)
            if not raw:
                continue
            try:
                user_uuid_set.add(uuid.UUID(str(raw)))
            except ValueError:
                continue

    name_by_id: dict[str, str] = {}
    if user_uuid_set:
        users_result = await db.execute(select(User).where(User.id.in_(list(user_uuid_set))))
        for row in users_result.scalars().all():
            name_by_id[str(row.id)] = _display_name_for_user(row)

    for item in items:
        item["user_name"] = name_by_id.get(str(item.get("user_id")), str(item.get("user_id", ""))[:8])
        if item.get("type") == "pvp":
            item["user1_name"] = name_by_id.get(str(item.get("user1_id")), str(item.get("user1_id", ""))[:8])
            item["user2_name"] = name_by_id.get(str(item.get("user2_id")), str(item.get("user2_id", ""))[:8])

    items.sort(key=lambda item: item.get("started_at") or "", reverse=True)
    total = len(items)
    paged_items = items[offset:offset + per_page]

    return {
        "items": paged_items,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@admin_router.get("/sessions/{session_type}/{session_id}")
async def admin_get_session_detail(
    session_type: str,
    session_id: str,
    current=Depends(get_admin_read_access),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve detailed progress, questions, answers, and user info for a session."""
    _require_admin(current)

    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID format")

    session_type = session_type.lower().strip()
    if session_type not in ("classic", "challenge", "custom", "pvp"):
        raise HTTPException(status_code=400, detail="Invalid session type")

    session_info = {}
    users_info = []
    answers_info = []

    if session_type == "classic":
        s = await db.get(ClassicSession, sid)
        if not s:
            raise HTTPException(status_code=404, detail="Classic session not found")
        session_info = {
            "id": str(s.id),
            "type": "classic",
            "topic": s.topic,
            "questions": int(s.questions_answered or 0),
            "correct": int(s.correct_count or 0),
            "started_at": _as_iso(s.created_at),
            "ended_at": _as_iso(s.ended_at),
            "is_completed": s.ended_at is not None,
        }
        user = await db.get(User, s.user_id)
        if user:
            users_info.append({
                "id": str(user.id),
                "username": user.username,
                "email": user.email,
                "level": user.level,
                "points": user.points,
            })

        responses = (await db.execute(
            select(UserResponse).where(UserResponse.session_id == s.id).order_by(UserResponse.created_at.asc())
        )).scalars().all()

        for idx, resp in enumerate(responses):
            q = await db.get(QuestionBank, resp.question_id)
            q_text = q.question_text if q else f"Question ID: {resp.question_id}"
            options = json.loads(q.options_json) if q and q.options_json else []
            correct_ans = q.correct_answer if q else "N/A"
            explanation = q.explanation if q else "N/A"
            chosen_ans = correct_ans if resp.answered_correct else "Incorrect selection (details not logged)"
            answers_info.append({
                "index": idx + 1,
                "question_text": q_text,
                "options": options,
                "correct_answer": correct_ans,
                "chosen_answer": chosen_ans,
                "is_correct": resp.answered_correct,
                "time_taken": resp.time_taken,
                "used_hint": resp.used_hint,
                "explanation": explanation,
            })

    elif session_type == "custom":
        s = await db.get(CustomSession, sid)
        if not s:
            raise HTTPException(status_code=404, detail="Custom session not found")
        session_info = {
            "id": str(s.id),
            "type": "custom",
            "topic": s.topic,
            "questions": s.total_questions,
            "correct": s.correct_count,
            "started_at": _as_iso(s.started_at),
            "ended_at": _as_iso(s.ended_at),
            "is_completed": s.ended_at is not None,
        }
        user = await db.get(User, s.user_id)
        if user:
            users_info.append({
                "id": str(user.id),
                "username": user.username,
                "email": user.email,
                "level": user.level,
                "points": user.points,
            })

        responses = (await db.execute(
            select(UserResponse).where(UserResponse.session_id == s.id).order_by(UserResponse.created_at.asc())
        )).scalars().all()

        for idx, resp in enumerate(responses):
            q = await db.get(QuestionBank, resp.question_id)
            q_text = q.question_text if q else f"Question ID: {resp.question_id}"
            options = json.loads(q.options_json) if q and q.options_json else []
            correct_ans = q.correct_answer if q else "N/A"
            explanation = q.explanation if q else "N/A"
            chosen_ans = correct_ans if resp.answered_correct else "Incorrect selection (details not logged)"
            answers_info.append({
                "index": idx + 1,
                "question_text": q_text,
                "options": options,
                "correct_answer": correct_ans,
                "chosen_answer": chosen_ans,
                "is_correct": resp.answered_correct,
                "time_taken": resp.time_taken,
                "used_hint": resp.used_hint,
                "explanation": explanation,
            })

    elif session_type == "challenge":
        s = await db.get(ChallengeSession, sid)
        if not s:
            raise HTTPException(status_code=404, detail="Challenge session not found")
        session_info = {
            "id": str(s.id),
            "type": "challenge",
            "topic": s.topic,
            "questions": s.total_questions,
            "correct": s.correct_answers,
            "started_at": _as_iso(s.started_at),
            "ended_at": _as_iso(s.ended_at),
            "is_completed": s.is_completed,
            "rank_points": s.rank_points,
            "starting_level": s.starting_level,
            "current_level": s.current_level,
        }
        user = await db.get(User, s.user_id)
        if user:
            users_info.append({
                "id": str(user.id),
                "username": user.username,
                "email": user.email,
                "level": user.level,
                "points": user.points,
            })

        from database.challenge_models import ChallengeAnswer
        answers = (await db.execute(
            select(ChallengeAnswer).where(ChallengeAnswer.session_id == s.id).order_by(ChallengeAnswer.created_at.asc())
        )).scalars().all()

        for idx, ans in enumerate(answers):
            q = await db.get(QuestionBank, ans.question_id)
            q_text = q.question_text if q else f"Question ID: {ans.question_id}"
            options = json.loads(q.options_json) if q and q.options_json else []
            correct_ans = q.correct_answer if q else "N/A"
            explanation = q.explanation if q else "N/A"
            answers_info.append({
                "index": idx + 1,
                "question_text": q_text,
                "options": options,
                "correct_answer": correct_ans,
                "chosen_answer": ans.chosen_answer,
                "is_correct": ans.is_correct,
                "time_taken": ans.time_taken,
                "points_change": ans.points_change,
                "level_at_answer": ans.level_at_answer,
                "explanation": explanation,
            })

    elif session_type == "pvp":
        from database.pvp_models import PvPMatch, PvPMatchAnswer
        s = await db.get(PvPMatch, sid)
        if not s:
            raise HTTPException(status_code=404, detail="PvP match not found")
        session_info = {
            "id": str(s.id),
            "type": "pvp",
            "topic": s.topic,
            "questions": s.total_questions,
            "started_at": _as_iso(s.started_at),
            "ended_at": _as_iso(s.ended_at),
            "is_completed": s.status == "completed",
            "winner_id": str(s.winner_id) if s.winner_id else None,
            "status": s.status,
            "user1_score": s.user1_score,
            "user2_score": s.user2_score,
        }

        u1 = await db.get(User, s.user1_id)
        u2 = await db.get(User, s.user2_id)
        if u1:
            users_info.append({
                "id": str(u1.id),
                "role": "Player 1",
                "username": u1.username,
                "email": u1.email,
                "score": s.user1_score,
            })
        if u2:
            users_info.append({
                "id": str(u2.id),
                "role": "Player 2",
                "username": u2.username,
                "email": u2.email,
                "score": s.user2_score,
            })

        answers = (await db.execute(
            select(PvPMatchAnswer).where(PvPMatchAnswer.match_id == s.id).order_by(PvPMatchAnswer.question_index.asc(), PvPMatchAnswer.user_id.asc())
        )).scalars().all()

        questions_list = json.loads(s.questions_json) if s.questions_json else []

        for idx, q_data in enumerate(questions_list):
            q_text = q_data.get("question_text") or q_data.get("text") or f"Question {idx+1}"
            options = q_data.get("options") or []
            correct_ans = q_data.get("correct_answer") or q_data.get("answer") or "N/A"
            explanation = q_data.get("explanation") or "N/A"

            q_answers = []
            for ans in answers:
                if ans.question_index == idx:
                    ans_user = u1 if ans.user_id == s.user1_id else u2
                    q_answers.append({
                        "user_id": str(ans.user_id),
                        "username": ans_user.username if ans_user else "Unknown",
                        "chosen_answer": ans.chosen_answer,
                        "is_correct": ans.is_correct,
                        "time_taken": ans.time_taken,
                    })

            answers_info.append({
                "index": idx + 1,
                "question_text": q_text,
                "options": options,
                "correct_answer": correct_ans,
                "user_answers": q_answers,
                "explanation": explanation,
            })

    return {
        "session": session_info,
        "users": users_info,
        "details": answers_info,
    }


# -
# DB INSPECTOR
# -


@admin_router.get("/db/schema")
# Return read-only database schema and table counts for admin inspection.
async def admin_db_schema(
    current=Depends(get_admin_read_access),
    db: AsyncSession = Depends(get_db),
):
    if current is not None:
        _require_admin(current)

    async with db.bind.connect() as conn:
        tables = await conn.run_sync(_collect_db_schema)

    return {
        "tables": tables,
        "total_tables": len(tables),
    }


def _collect_db_schema(sync_conn):
    inspector = sa_inspect(sync_conn)
    metadata = MetaData()
    tables = []

    for table_name in sorted(inspector.get_table_names()):
        columns_info = inspector.get_columns(table_name)
        primary_keys = set(inspector.get_pk_constraint(table_name).get("constrained_columns") or [])
        reflected = Table(table_name, metadata, autoload_with=sync_conn)
        total_rows = sync_conn.execute(select(func.count()).select_from(reflected)).scalar() or 0

        tables.append(
            {
                "name": table_name,
                "row_count": int(total_rows),
                "columns": [
                    {
                        "name": str(col.get("name") or ""),
                        "type": str(col.get("type") or ""),
                        "nullable": bool(col.get("nullable", True)),
                        "primary_key": str(col.get("name") or "") in primary_keys,
                    }
                    for col in columns_info
                ],
            }
        )

    return tables


@admin_router.get("/db/table/{table_name}")
# Return paginated table rows and typed column metadata for one table.
async def admin_db_table_rows(
    table_name: str,
    limit: int = Query(
        default=ADMIN_DB_INSPECTOR_DEFAULT_LIMIT,
        ge=1,
        le=ADMIN_DB_INSPECTOR_MAX_LIMIT,
    ),
    offset: int = Query(default=0, ge=0),
    current=Depends(get_admin_read_access),
    db: AsyncSession = Depends(get_db),
):
    if current is not None:
        _require_admin(current)

    # Lightweight validation: only inspect the requested table, not the full schema.
    async with db.bind.connect() as conn:
        selected = await conn.run_sync(_get_single_table_info, table_name)

    if selected is None:
        raise HTTPException(404, "Table not found")

    quoted = table_name.replace('"', '""')
    query = text(f'SELECT * FROM "{quoted}" LIMIT :limit OFFSET :offset')
    rows_result = await db.execute(query, {"limit": int(limit), "offset": int(offset)})
    rows = [
        {key: _to_jsonable(redact_db_value(key, value)) for key, value in row.items()}
        for row in rows_result.mappings().all()
    ]

    total_result = await db.execute(text(f'SELECT COUNT(*) AS total FROM "{quoted}"'))
    total = int(total_result.scalar() or 0)

    return {
        "table": table_name,
        "columns": [
            {
                **column,
                "redacted": bool(
                    is_sensitive_column(str(column.get("name") or ""))
                    or str(column.get("name") or "").lower() in {"email"}
                    or str(column.get("name") or "").lower().endswith("_email")
                ),
            }
            for column in selected["columns"]
        ],
        "rows": rows,
        "total": total,
        "limit": int(limit),
        "offset": int(offset),
    }


def _get_single_table_info(sync_conn, table_name: str):
    """Inspect a single table's columns without scanning the full schema."""
    inspector = sa_inspect(sync_conn)
    if table_name not in inspector.get_table_names():
        return None

    columns_info = inspector.get_columns(table_name)
    primary_keys = set(
        inspector.get_pk_constraint(table_name).get("constrained_columns") or []
    )

    return {
        "name": table_name,
        "columns": [
            {
                "name": str(col.get("name") or ""),
                "type": str(col.get("type") or ""),
                "nullable": bool(col.get("nullable", True)),
                "primary_key": str(col.get("name") or "") in primary_keys,
            }
            for col in columns_info
        ],
    }


# -
# MONITORING
# -


@admin_router.get("/monitoring")
# Return operational monitoring snapshot for the admin dashboard.
async def admin_monitoring(current=Depends(get_admin_read_access)):
    """Get system monitoring stats - request counts, errors, rate limits.

    Uses the in-memory Monitoring singleton from services/monitoring.py.
    """
    if current is not None:
        _require_admin(current)
    monitoring = get_monitoring()
    return monitoring.get_stats()

