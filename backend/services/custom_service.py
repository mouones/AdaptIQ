"""
services/custom_service.py
Business logic for the Custom Room.

FIX: user_id is uuid.UUID everywhere (matches users.id UUID PK).

Covers:
    - Topic catalogue metadata for custom-room flows
    - User topic mastery creation and percentage refresh helpers
    - Fact sampling strategy for progressive practice
    - Session lookup and per-fact mastery progress persistence
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database.custom_models import (
    Fact,
    UserTopicMastery,
    UserFactProgress,
    CustomSession,
)

# - Topic catalogue -

TOPIC_CATALOGUE = [
    {"type": "History",   "slug": "ww1",              "name": "World War I",           "description": "The Great War (1914-1918).",                         "total_facts": 0},
    {"type": "History",   "slug": "ww2",              "name": "World War II",          "description": "The largest conflict in human history (1939-1945).", "total_facts": 0},
    {"type": "History",   "slug": "cold-war",         "name": "Cold War",              "description": "Geopolitical tension between the US and USSR.",       "total_facts": 0},
    {"type": "History",   "slug": "ancient-rome",     "name": "Ancient Rome",          "description": "From city-state to empire.",                          "total_facts": 0},
    {"type": "History",   "slug": "french-rev",       "name": "French Revolution",     "description": "Far-reaching upheaval in France.",                    "total_facts": 0},
    {"type": "History",   "slug": "ind-rev",          "name": "Industrial Revolution", "description": "New manufacturing processes in Europe and the US.",   "total_facts": 0},
    {"type": "Geography", "slug": "geo-usa",          "name": "United States",         "description": "Geography of the United States.",                     "total_facts": 0},
    {"type": "Geography", "slug": "geo-brazil",       "name": "Brazil",                "description": "Geography of Brazil.",                                "total_facts": 0},
    {"type": "Geography", "slug": "geo-france",       "name": "France",                "description": "Geography of France.",                                "total_facts": 0},
    {"type": "Geography", "slug": "geo-egypt",        "name": "Egypt",                 "description": "Geography of Egypt.",                                 "total_facts": 0},
    {"type": "Geography", "slug": "geo-china",        "name": "China",                 "description": "Geography of China.",                                 "total_facts": 0},
    {"type": "Geography", "slug": "geo-australia",    "name": "Australia",             "description": "Geography of Australia.",                             "total_facts": 0},
    {"type": "Geography", "slug": "geo-uk",           "name": "United Kingdom",        "description": "Geography of the United Kingdom.",                    "total_facts": 0},
    {"type": "Geography", "slug": "geo-india",        "name": "India",                 "description": "Geography of India.",                                 "total_facts": 0},
    {"type": "Geography", "slug": "geo-japan",        "name": "Japan",                 "description": "Geography of Japan.",                                 "total_facts": 0},
    {"type": "Geography", "slug": "geo-south-africa", "name": "South Africa",          "description": "Geography of South Africa.",                          "total_facts": 0},
]


def get_canonical_topic_label(topic: str) -> str:
    """Normalize any topic slug, name, or label to Type - Name format."""
    if not topic:
        return topic
    
    topic_clean = topic.strip().lower()
    
    # Try exact slug match
    for t in TOPIC_CATALOGUE:
        if t["slug"].lower() == topic_clean:
            return f"{t['type']} - {t['name']}"
            
    # Try exact name match
    for t in TOPIC_CATALOGUE:
        if t["name"].lower() == topic_clean:
            return f"{t['type']} - {t['name']}"
            
    # Try exact "type - name" match
    for t in TOPIC_CATALOGUE:
        label = f"{t['type']} - {t['name']}".lower()
        if label == topic_clean:
            return f"{t['type']} - {t['name']}"
            
    # Try partial or prefix match
    for t in TOPIC_CATALOGUE:
        if " - " in topic_clean:
            parts = topic_clean.split(" - ", 1)
            name_part = parts[1].strip()
            if t["slug"].lower() == name_part or t["name"].lower() == name_part:
                return f"{t['type']} - {t['name']}"
            if name_part.startswith("geo-") and t["slug"].lower() == name_part:
                return f"{t['type']} - {t['name']}"
            if t["slug"].lower() == f"geo-{name_part}":
                return f"{t['type']} - {t['name']}"
                
    return topic


# - Mastery helpers -

# Get or initialize mastery progress row for a user/topic pair.
async def get_or_create_mastery(
    db: AsyncSession,
    user_id: uuid.UUID,      # UUID - matches users.id
    topic: str,
    total_facts: int,
) -> UserTopicMastery:
    topic = get_canonical_topic_label(topic)
    result = await db.execute(
        select(UserTopicMastery).where(
            UserTopicMastery.user_id == user_id,
            UserTopicMastery.topic   == topic,
        )
    )
    mastery = result.scalar_one_or_none()
    if mastery is None:
        mastery = UserTopicMastery(
            user_id               = user_id,
            topic                 = topic,
            mastered_facts_count  = 0,
            total_facts_count     = total_facts,
            completion_percentage = 0.0,
        )
        db.add(mastery)
        await db.flush()
    else:
        desired_total = int(total_facts or 0)
        current_total = int(mastery.total_facts_count or 0)
        if desired_total > 0 and (
            current_total <= 0
            or (desired_total <= 20 and current_total > desired_total)
        ):
            mastery.total_facts_count = desired_total
            mastery.mastered_facts_count = min(
                int(mastery.mastered_facts_count or 0),
                desired_total,
            )
            await db.flush()
    return mastery


# Recompute completion percentage and refresh last-session timestamp.
async def _refresh_mastery_percentage(mastery: UserTopicMastery) -> None:
    if mastery.total_facts_count > 0:
        mastery.completion_percentage = (
            mastery.mastered_facts_count / mastery.total_facts_count
        ) * 100.0
    mastery.last_session_at = datetime.now(timezone.utc).replace(tzinfo=None)


# - Fact selection -

# Select a fact for practice, preferring unseen/unmastered facts.
async def pick_fact_for_user(
    db: AsyncSession,
    user_id: uuid.UUID,      # UUID
    topic: str,
) -> Optional[Fact]:
    topic = get_canonical_topic_label(topic)
    mastered_ids_q = await db.execute(
        select(UserFactProgress.fact_id).where(
            UserFactProgress.user_id     == user_id,
            UserFactProgress.is_mastered.is_(True),
        )
    )
    mastered_ids = {row[0] for row in mastered_ids_q.fetchall()}

    if random.random() < 0.8 or not mastered_ids:
        q = select(Fact).where(
            Fact.topic == topic,
            Fact.id.notin_(mastered_ids) if mastered_ids else True,
        ).order_by(func.random()).limit(1)
    else:
        q = select(Fact).where(Fact.topic == topic).order_by(func.random()).limit(1)

    result = await db.execute(q)
    return result.scalar_one_or_none()


# - LLM prompt builder -

# Build a constrained prompt used to generate one custom-room MCQ.
def build_custom_prompt(topic: str, fact_content: str, difficulty_hint: Optional[str]) -> str:
    if difficulty_hint == "easy":
        difficulty_text = "Make this an easy question - two of the wrong options should be obviously incorrect."
    elif difficulty_hint == "hard":
        difficulty_text = "Make this a hard question - all four options should be plausible to an expert."
    else:
        difficulty_text = "Make this a medium-difficulty question."

    return f"""You are a quiz master for the topic: "{topic}".

Use the following fact to generate a multiple-choice question:
FACT: {fact_content}

{difficulty_text}

Generate exactly ONE multiple-choice question with:
- A clear, concise question text that is a properly formatted interrogative sentence.
- Exactly 4 answer options (just the text, do not prefix with A, B, C, D)
- One correct answer WHICH MUST EXACTLY MATCH ONE OF THE OPTIONS.
- A brief educational explanation (1-2 sentences)
- DO NOT include the correct answer in the question text.

Respond in this exact JSON format (no markdown, no extra text):
{{
  "question": "...",
  "options": ["...", "...", "...", "..."],
  "correct_answer": "...",
  "explanation": "..."
}}
"""


# - Session helpers -

# Fetch a custom session by id, returning None for invalid UUIDs.
async def get_session(db: AsyncSession, session_id: str) -> Optional[CustomSession]:
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        return None
    result = await db.execute(
        select(CustomSession).where(CustomSession.id == sid)
    )
    return result.scalar_one_or_none()


# Update per-user fact mastery counters and return whether newly mastered.
async def update_fact_progress(
    db: AsyncSession,
    user_id: uuid.UUID,      # UUID
    fact_id: uuid.UUID,
    is_correct: bool,
) -> bool:
    result = await db.execute(
        select(UserFactProgress).where(
            UserFactProgress.user_id == user_id,
            UserFactProgress.fact_id == fact_id,
        )
    )
    progress = result.scalar_one_or_none()
    newly_mastered = False

    if progress is None:
        progress = UserFactProgress(
            user_id      = user_id,
            fact_id      = fact_id,
            is_mastered  = is_correct,
            attempts     = 1,
            correct_hits = 1 if is_correct else 0,
        )
        db.add(progress)
        if is_correct:
            newly_mastered = True
    else:
        progress.attempts += 1
        if is_correct:
            progress.correct_hits += 1
            if not progress.is_mastered:
                progress.is_mastered = True
                newly_mastered = True

    return newly_mastered


# Count available facts for a topic.
async def total_facts_for_topic(db: AsyncSession, topic: str) -> int:
    topic = get_canonical_topic_label(topic)
    result = await db.execute(
        select(func.count()).select_from(Fact).where(Fact.topic == topic)
    )
    return result.scalar_one() or 0

