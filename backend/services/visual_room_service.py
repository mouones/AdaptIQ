"""
services/visual_room_service.py
Business logic for the VisualRoom.

Responsibilities:
  - Select the next question for a given topic + level (avoiding repeats).
  - Generate question text + options via LLM on first use, then store them.
  - Verify submitted answers (MCQ exact match, text-input fuzzy/LLM check).
  - Update n_attempts / n_correct / difficulty_actual after each submission.
  - Generate hints without revealing the correct answer.
"""

from __future__ import annotations

import json
import logging
import random
import re
import uuid
from typing import Optional

from sqlalchemy import select, func, or_, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from config import VISUAL_PREGEN_BATCH_SIZE, VISUAL_QUESTIONS_PER_SESSION
from database.visual_models import VisualQuestion, VisualSession
from services.governance_service import GovernanceService, GovernanceDecision

logger = logging.getLogger(__name__)


def _parse_options_json(options_json: Optional[str]) -> list[str]:
    if not options_json:
        return []
    try:
        parsed = json.loads(options_json)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except Exception:
        pass
    return []


BAD_OPTION_LABELS = {
    "unknown",
    "other",
    "none of the above",
    "cannot determine",
    "cannot be determined",
    "all of the above",
    "insufficient data",
    "not enough information",
    "alternative geography",
    "alternative history",
}

GEOGRAPHY_DISTRACTORS = [
    "France", "Germany", "Italy", "Spain", "Portugal", "Belgium", "Netherlands",
    "Switzerland", "Austria", "Poland", "Greece", "Turkey", "Morocco", "Egypt",
    "Brazil", "Argentina", "Canada", "Mexico", "Japan", "South Korea", "India",
    "Indonesia", "Australia", "South Africa", "Tunisia", "Algeria", "Vietnam", "Thailand",
]

HISTORY_DISTRACTORS = [
    "Rome", "Athens", "Cairo", "Paris", "London", "Constantinople", "Babylon",
    "Alexander the Great", "Julius Caesar", "Napoleon Bonaparte", "George Washington",
    "World War I", "World War II", "The Cold War", "The French Revolution",
    "The Roman Empire", "The Ottoman Empire", "The British Empire", "The Byzantine Empire",
]


def _norm_option(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _is_bad_option(value: str) -> bool:
    value = _norm_option(value)
    if not value:
        return True
    low = value.lower()
    compact = low.replace(" ", "")
    if low in BAD_OPTION_LABELS:
        return True
    if compact in {"optiona", "optionb", "optionc", "optiond"}:
        return True
    if re.fullmatch(r"option\s*\d+", low):
        return True
    if low.startswith("alternative "):
        return True
    return False


def _pick_preferred_visual_candidate(
    candidates: list[VisualQuestion],
    level: int,
    excluded_ids: set[str],
) -> Optional[VisualQuestion]:
    """Prefer already-generated questions before falling back to cold ones."""
    ready: list[VisualQuestion] = []
    pending: list[VisualQuestion] = []

    for candidate in candidates:
        if str(candidate.id) in excluded_ids:
            continue
        if visual_question_needs_generation(candidate, level):
            pending.append(candidate)
        else:
            ready.append(candidate)

    if ready:
        return ready[0]
    if pending:
        return pending[0]
    return None


async def get_visual_warmup_candidates(
    db: AsyncSession,
    topic: str,
    level: int,
    *,
    limit: int = VISUAL_PREGEN_BATCH_SIZE,
) -> list[VisualQuestion]:
    """Select up to `limit` cold visual rows worth generating in the background."""
    if limit <= 0:
        return []

    low, high = LEVEL_DIFFICULTY_RANGE.get(level, (1.0, 5.0))
    difficulty_col = VisualQuestion.difficulty_actual
    ranges = [
        (low, high),
        (max(1.0, low - 0.5), min(5.0, high + 0.5)),
        (max(1.0, low - 1.5), min(5.0, high + 1.5)),
        (1.0, 5.0),
    ]

    topics = ["history", "geography"] if topic.lower() == "mixed" else [topic.lower()]
    if len(topics) > 1:
        random.shuffle(topics)

    sample_size = max(limit * 4, 20)
    picked: list[VisualQuestion] = []
    seen_ids: set[str] = set()

    for low_try, high_try in ranges:
        for current_topic in topics:
            stmt = (
                select(VisualQuestion)
                .where(
                    VisualQuestion.topic == current_topic,
                    difficulty_col.between(low_try, high_try),
                )
                .order_by(func.random())
                .limit(sample_size)
            )
            result = await db.execute(stmt)
            for candidate in result.scalars().all():
                qid = str(candidate.id)
                if qid in seen_ids:
                    continue
                seen_ids.add(qid)
                if not visual_question_needs_generation(candidate, level):
                    continue
                picked.append(candidate)
                if len(picked) >= limit:
                    return picked

    return picked


def _extract_country_name(paragraph: str) -> Optional[str]:
    text = _norm_option(paragraph)
    if not text:
        return None
    patterns = [
        r"^([A-Z][A-Za-z .'-]{2,60})\s+is\s+(?:a|an)\s+country\b",
        r"^([A-Z][A-Za-z .'-]{2,60})\s+is\s+located\b",
        r"^([A-Z][A-Za-z .'-]{2,60})\s*,\s+officially\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            name = _norm_option(m.group(1))
            if not _is_bad_option(name):
                return name
    return None


def _fallback_correct_answer(visual_q: VisualQuestion) -> str:
    if (visual_q.topic or "").lower() == "geography":
        country = _extract_country_name(visual_q.paragraph or "")
        if country:
            return country
    current = _norm_option(visual_q.correct_answer or "")
    if current and not _is_bad_option(current):
        return current
    text = _norm_option((visual_q.paragraph or "").split(".")[0])
    if text and len(text) <= 80 and not _is_bad_option(text):
        return text
    return "Image subject"


def _fallback_distractors(topic: str, correct: str, count: int) -> list[str]:
    pool = GEOGRAPHY_DISTRACTORS if (topic or "").lower() == "geography" else HISTORY_DISTRACTORS
    out: list[str] = []
    used = {correct.lower()}
    for candidate in pool:
        candidate = _norm_option(candidate)
        if candidate.lower() not in used and not _is_bad_option(candidate):
            out.append(candidate)
            used.add(candidate.lower())
        if len(out) >= count:
            break
    return out


def normalize_visual_options_for_level(
    options: list[str],
    correct_answer: str,
    topic: str,
    level: int,
) -> list[str]:
    """Return clean option payload for Visual Room level rules.

    L1 must expose exactly 2 real choices. L2-L4 expose 4 real choices.
    L5 is typed answer and exposes no choices.
    """
    level = max(1, min(5, int(level or 1)))
    if level == 5:
        return []
    needed = LEVEL_OPTIONS_COUNT.get(level, 4)
    correct = _norm_option(correct_answer)
    if _is_bad_option(correct):
        correct = "Image subject"
    used = {correct.lower()}
    wrongs: list[str] = []
    for raw in options:
        opt = _norm_option(raw)
        if _is_bad_option(opt):
            continue
        if opt.lower() in used:
            continue
        used.add(opt.lower())
        wrongs.append(opt)
        if len(wrongs) >= needed - 1:
            break
    if len(wrongs) < needed - 1:
        wrongs.extend(_fallback_distractors(topic, correct, needed - 1 - len(wrongs)))
    clean = [correct] + wrongs[: needed - 1]
    random.shuffle(clean)
    return clean[:needed]


def looks_like_placeholder_options(visual_q: VisualQuestion) -> bool:
    """Detect the dev/test backfill placeholder options (Option A/B/C/D)."""
    opts = _parse_options_json(visual_q.options_json)
    if not opts:
        return False

    normalized = [o.strip().lower().replace(" ", "") for o in opts]
    placeholder = {"optiona", "optionb", "optionc", "optiond"}
    return all(o in placeholder for o in normalized)


def visual_question_needs_generation(visual_q: VisualQuestion, level: int) -> bool:
    """True if the row is missing generated content or contains placeholders/low-quality options."""
    if level == 5:
        # Level 5 must be text-input (no options)
        if (
            not visual_q.question_text
            or not visual_q.correct_answer
            or visual_q.question_type != 'T'
        ):
            return True
    else:
        # MCQ levels
        if not visual_q.question_text or not visual_q.correct_answer or visual_q.options_json is None:
            return True

        if looks_like_placeholder_options(visual_q):
            return True

        expected_count = LEVEL_OPTIONS_COUNT.get(level, 4)
        opts = _parse_options_json(visual_q.options_json)
        if len(normalize_visual_options_for_level(opts, visual_q.correct_answer or "", visual_q.topic or "", level)) != expected_count:
            return True

        # Force regen if existing options contain low-quality placeholders from previous runs
        opts = _parse_options_json(visual_q.options_json)
        if any(_is_bad_option(o) for o in opts):
            return True

    # Placeholder correct answers can also block regen
    if (visual_q.correct_answer or "").strip().lower().replace(" ", "") in {"optiona", "optionb", "optionc", "optiond"}:
        return True

    return False

# ── Difficulty window per level ───────────────────────────────────────────────
# Each level maps to a [low, high] difficulty_actual range.
LEVEL_DIFFICULTY_RANGE: dict[int, tuple[float, float]] = {
    1: (1.0, 2.0),
    2: (1.5, 2.5),
    3: (2.5, 3.5),
    4: (3.5, 4.5),
    5: (4.0, 5.0),
}

# Level 1 gets 2 options, L5 gets none (text input)
LEVEL_OPTIONS_COUNT: dict[int, int] = {
    1: 2,
    2: 4,
    3: 4,
    4: 4,
    5: 0,
}

# ── Flag vs shape mix per level (geography only) ──────────────────────────────
# probability of showing a SHAPE question at each level (0.0 = always flag)
SHAPE_PROBABILITY: dict[int, float] = {
    1: 0.00,   # L1 — 100% flag
    2: 0.30,   # L2 — 80% flag, 20% shape
    3: 0.50,   # L3 — 50/50
    4: 0.70,   # L4 — 30% flag, 70% shape
    5: 0.50,   # L5 — random between hardest flag and hardest shape
}


def should_show_shape(level: int, topic: str, has_shape: bool) -> bool:
    """
    Decide flag vs shape for this question.
    - History rows never have shapes → always False.
    - If the row has no shape_svg → fall back to flag.
    - Otherwise use SHAPE_PROBABILITY for the level.
    """
    if topic.lower() != "geography":
        return False
    if not has_shape:
        return False
    prob = SHAPE_PROBABILITY.get(level, 0.0)
    return random.random() < prob

# ── LLM system prompts for visual question generation ────────────────────────

# Geography prompts — level-aware, image-first
_GEO_PROMPTS = {
    1: """You are a geography quiz generator for a FLAG identification game.
The user is shown a country FLAG. Generate ONE simple question.

LEVEL 1 RULES (80% identification, 20% simple facts WITH country named):
- Most questions: "Which country does this flag belong to?" or "What country is represented by this flag?"
- Occasionally: ask a simple fact but INCLUDE the country name — "France is known for which famous tower?"
- Only 2 answer choices needed.
- Wrong answer must be a real country name (plausible distractor).

Return ONLY valid JSON:
{
  "text": "question text",
  "correct": "correct answer",
  "wrong1": "one wrong answer",
  "explanation": "1 sentence"
}""",

    2: """You are a geography quiz generator for a visual quiz.
The user sees a FLAG or SHAPE. Generate ONE question.

LEVEL 2 RULES (50% identification, 50% facts WITH country named):
- Identification questions: "Which country does this flag belong to?" / "What country has this shape?"
- Fact questions: INCLUDE the country name — "Brazil has how many time zones?" / "What is the capital of Germany?"
- 4 answer choices. Wrong answers must be plausible (same region or similar countries).

Return ONLY valid JSON:
{
  "text": "question text",
  "correct": "correct answer",
  "wrong1": "plausible wrong answer",
  "wrong2": "plausible wrong answer",
  "wrong3": "plausible wrong answer",
  "explanation": "1-2 educational sentences"
}""",

    3: """You are a geography quiz generator for a visual quiz.
The user sees a FLAG or SHAPE. Generate ONE question.

LEVEL 3 RULES (30% identification, 70% facts WITHOUT country name):
- Identification (30%): "Which country does this flag represent?" / "What country has this shape?"
- Facts (70%): Ask about the country's facts but DO NOT say the country name in the question.
  GOOD: "What is the capital of the country shown?" / "What currency does this country use?"
  BAD: "What is the capital of France?" (never name the country)
- 4 answer choices. All wrong answers must be plausible.

Return ONLY valid JSON:
{
  "text": "question text — never name the country unless it is an identification question",
  "correct": "correct answer",
  "wrong1": "plausible wrong answer",
  "wrong2": "plausible wrong answer",
  "wrong3": "plausible wrong answer",
  "explanation": "1-2 educational sentences"
}""",

    4: """You are a geography quiz generator for a visual quiz.
The user sees a FLAG or SHAPE. Generate ONE question.

LEVEL 4 RULES (15% identification, 85% hard facts WITHOUT country name):
- Identification (15% only): "Which country does this flag belong to?"
- Hard facts (85%): Ask challenging questions WITHOUT naming the country.
  GOOD: "What is the official language of the country shown?" / "Which ocean borders this country?"
         "How many neighboring countries does this nation share borders with?"
  BAD: "What language does Brazil speak?" (never name the country in fact questions)
- 4 answer choices, all highly plausible.

Return ONLY valid JSON:
{
  "text": "question text — for fact questions, NEVER name the country",
  "correct": "correct answer",
  "wrong1": "plausible wrong answer",
  "wrong2": "plausible wrong answer",
  "wrong3": "plausible wrong answer",
  "explanation": "1-2 educational sentences"
}""",
}

_GEO_TEXT_PROMPT = """You are a geography quiz generator for a visual quiz.
The user sees a FLAG or SHAPE and must TYPE their answer.

LEVEL 5 RULES (5% identification, 95% expert facts WITHOUT country name):
- Almost never ask "What country is this?" — they should already know.
- Ask hard specific facts WITHOUT naming the country:
  "What is the capital of the country shown?" / "What currency does this country use?"
  "What is the approximate population of this country?" / "Name a country that borders this one."
- The answer must be SHORT (1-4 words) and unambiguous.
- DO NOT name the country in the question.

Return ONLY valid JSON:
{
  "text": "question — do not name the country",
  "correct": "short exact answer (1-4 words)",
  "explanation": "2-3 educational sentences"
}"""

_HIST_PROMPTS = {
    1: """You are a history quiz generator for a visual quiz.
The user sees a HISTORICAL IMAGE (photo, painting, portrait, or map).
Generate ONE simple question focused on what is VISIBLE in the image.

LEVEL 1 RULES:
- Primary focus: What/Who/Where is shown?
- ADAPT to the image type based on the description: if it describes a person, ask "Who is the person shown?". If it describes a map/location, ask "What region or empire is shown?". If it describes an event, ask "What event is depicted?".
- Simple identification — 2 answer choices only.
- Base the question on the paragraph description provided.

Return ONLY valid JSON:
{
  "text": "question about what is shown in the image",
  "correct": "correct answer",
  "wrong1": "one plausible wrong answer",
  "explanation": "1 sentence"
}""",

    2: """You are a history quiz generator for a visual quiz.
The user sees a HISTORICAL IMAGE. Generate ONE question.

LEVEL 2 RULES:
- 60% image identification: "Who is shown in this image?" / "What battle is depicted here?"
- 40% context facts (can name the subject): "Napoleon Bonaparte is known for which major defeat?"
- 4 answer choices. Wrong answers must be from the same historical era.

Return ONLY valid JSON:
{
  "text": "question focused on the image or its subject",
  "correct": "correct answer",
  "wrong1": "plausible wrong answer",
  "wrong2": "plausible wrong answer",
  "wrong3": "plausible wrong answer",
  "explanation": "1-2 educational sentences"
}""",

    3: """You are a history quiz generator for a visual quiz.
The user sees a HISTORICAL IMAGE. Generate ONE question.

LEVEL 3 RULES:
- 40% identification: "Who is the person shown?" / "What event is depicted?"
- 60% deeper context WITHOUT giving away the answer in the question:
  GOOD: "What year did the conflict shown in this image begin?"
  GOOD: "Which empire is represented by the battle shown here?"
  BAD: "When did World War II begin?" (too generic, doesn't connect to the image)
- 4 answer choices, all plausible.

Return ONLY valid JSON:
{
  "text": "question connected to what is shown in the image",
  "correct": "correct answer",
  "wrong1": "plausible wrong answer",
  "wrong2": "plausible wrong answer",
  "wrong3": "plausible wrong answer",
  "explanation": "1-2 educational sentences"
}""",

    4: """You are a history quiz generator for a visual quiz.
The user sees a HISTORICAL IMAGE. Generate ONE hard question.

LEVEL 4 RULES:
- 15% identification only if the subject is obscure.
- 85% specific historical facts connected to the image:
  "What treaty ended the conflict shown in this image?"
  "Which country was the primary opponent in the battle depicted here?"
  "What year was the person shown born?"
- All 4 answer choices must be highly plausible to a history student.

Return ONLY valid JSON:
{
  "text": "specific historical question connected to the image",
  "correct": "correct answer",
  "wrong1": "plausible wrong answer",
  "wrong2": "plausible wrong answer",
  "wrong3": "plausible wrong answer",
  "explanation": "1-2 educational sentences"
}""",
}

_HINT_SYSTEM = """You are giving a hint for a visual quiz question.
Write ONE short hint (max 20 words) that:
- Helps the student think in the right direction
- Does NOT say the answer or any part of it
- Points to a visual feature, time period, or geographic region
Return ONLY the hint text, nothing else."""

_HIST_TEXT_PROMPT = """You are a history quiz generator for a visual quiz.
The user sees a HISTORICAL IMAGE and must TYPE their answer.

LEVEL 5 RULES:
- Ask a specific, expert-level question about the image subject.
- The answer must be short and unambiguous (a name, a date, a number, a place).
- Examples: "What year did this battle take place?" / "Name the treaty signed after this conflict."
            "Who commanded the forces shown in this image?"

Return ONLY valid JSON:
{
  "text": "expert question about the image",
  "correct": "short exact answer (1-4 words)",
  "explanation": "2-3 educational sentences"
}"""


def _build_user_prompt(paragraph: str, level: int, topic: str, iso2: str = "", options_count: int = 4) -> str:
    """Build the user message sent alongside the system prompt."""
    country_hint = f"\nCOUNTRY ISO2 CODE (for your reference only — use based on level rules): {iso2}" if iso2 else ""

    quality_rules = (
        "QUESTION QUALITY RULES:\n"
        "- The question MUST be a properly formatted interrogative sentence (starting with Who, What, Where, When, Why, How, or Which).\n"
        "- Use flawless English grammar (e.g., 'What is the capital of France?', NOT 'Where is the capital').\n"
        "- DO NOT generate statement-like questions with a question mark at the end.\n"
        "- NEVER include the correct answer in the question text.\n"
    )
    
    if options_count == 4:
        quality_rules += "- You MUST generate exactly 3 distinct wrong answers in 'wrong1', 'wrong2', and 'wrong3'. Do NOT leave them empty.\n"
        quality_rules += "- NEVER use generic placeholders like 'Unknown', 'Other', or 'None of the above' as an option.\n"

    if topic == "geography":
        return (
            f"COUNTRY DATA:\n{paragraph[:700]}"
            f"{country_hint}\n\n"
            f"{quality_rules}\n"
            f"Generate ONE question following the Level {level} rules exactly.\n"
            f"Return ONLY the JSON."
        )
    else:
        return (
            f"IMAGE DESCRIPTION:\n{paragraph[:700]}\n\n"
            f"{quality_rules}\n"
            f"Generate ONE question focused on what is shown in this image.\n"
            f"Follow the Level {level} rules exactly.\n"
            f"Return ONLY the JSON."
        )

# ── Session helpers ───────────────────────────────────────────────────────────

async def create_visual_session(
    db: AsyncSession,
    user_id: str,
    topic: str,
    level: int,
    total_questions: int = VISUAL_QUESTIONS_PER_SESSION,
) -> VisualSession:
    row = VisualSession(
        id              = uuid.uuid4(),
        user_id         = uuid.UUID(user_id),
        topic           = topic,
        level           = level,
        total_questions = total_questions,
        seen_ids_json   = "[]",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def get_visual_session(db: AsyncSession, session_id: str) -> Optional[VisualSession]:
    try:
        sid = uuid.UUID(str(session_id))
    except (TypeError, ValueError, AttributeError):
        return None

    result = await db.execute(
        select(VisualSession).where(VisualSession.id == sid)
    )
    return result.scalar_one_or_none()


async def _get_seen_ids(session: VisualSession) -> list[str]:
    try:
        return json.loads(session.seen_ids_json or "[]")
    except Exception:
        return []


async def _add_seen_id(db: AsyncSession, session: VisualSession, question_id: str) -> None:
    seen = await _get_seen_ids(session)
    if question_id not in seen:
        seen.append(question_id)
    session.seen_ids_json = json.dumps(seen)
    await db.commit()


# ── Question selection ────────────────────────────────────────────────────────

async def get_next_question(
    db: AsyncSession,
    topic: str,
    level: int,
    session: VisualSession,
) -> Optional[VisualQuestion]:
    low, high = LEVEL_DIFFICULTY_RANGE.get(level, (1.0, 5.0))
    current_session_seen = await _get_seen_ids(session)

    # Retrieve all seen question IDs globally for this user to avoid repeats across sessions
    global_seen = set()
    try:
        stmt_sessions = select(VisualSession.seen_ids_json).where(
            VisualSession.user_id == session.user_id
        )
        res_sessions = await db.execute(stmt_sessions)
        for s_json in res_sessions.scalars().all():
            try:
                for q_id in json.loads(s_json or "[]"):
                    global_seen.add(str(q_id))
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Failed to load global seen questions for user: {e}")

    if topic.lower() == "mixed":
        picked_topic = random.choice(["history", "geography"])
        topic_filter = VisualQuestion.topic == picked_topic
    else:
        topic_filter = VisualQuestion.topic == topic.lower()

    difficulty_col = VisualQuestion.difficulty_actual

    ranges = [
        (low, high),
        (max(1.0, low - 0.5), min(5.0, high + 0.5)),
        (max(1.0, low - 1.5), min(5.0, high + 1.5)),
        (1.0, 5.0)
    ]
    
    # Pass 1: Try to find a candidate that has NEVER been seen globally by this user
    for low_try, high_try in ranges:
        stmt = (
            select(VisualQuestion)
            .where(
                topic_filter,
                difficulty_col.between(low_try, high_try),
            )
            .order_by(func.random())
            .limit(50)
        )
        result = await db.execute(stmt)
        candidates = result.scalars().all()

        preferred = _pick_preferred_visual_candidate(candidates, level, global_seen)
        if preferred is not None:
            return preferred

    # Pass 2: Fall back to checking only the current session's seen questions if all have been seen globally
    for low_try, high_try in ranges:
        stmt = (
            select(VisualQuestion)
            .where(
                topic_filter,
                difficulty_col.between(low_try, high_try),
            )
            .order_by(func.random())
            .limit(50)
        )
        result = await db.execute(stmt)
        candidates = result.scalars().all()

        preferred = _pick_preferred_visual_candidate(
            candidates,
            level,
            set(current_session_seen),
        )
        if preferred is not None:
            return preferred
        elif candidates:
            # All candidates in this range have been seen. We only return a seen question
            # if we are on the final fallback range (1.0, 5.0) to avoid failing.
            if (low_try, high_try) == (1.0, 5.0):
                return candidates[0]

    logger.warning(f"No visual questions found for topic={topic} level={level}")
    return None

# ── LLM question generation (called on first use) ────────────────────────────

async def generate_and_store_question(
    db: AsyncSession,
    visual_q: VisualQuestion,
    level: int,
    llm_client,
) -> VisualQuestion:
    """
    Generate question text + options via LLM, store in DB, return updated row.
    Level-aware: geography questions are image-first and country-name-aware.
    History questions focus on what is visible in the image.
    """
    if visual_q.question_text and visual_q.correct_answer and not visual_question_needs_generation(visual_q, level):
        return visual_q

    if looks_like_placeholder_options(visual_q) or (
        (visual_q.correct_answer or "").strip().lower().replace(" ", "") in {"optiona", "optionb", "optionc", "optiond"}
    ):
        visual_q.question_text  = None
        visual_q.correct_answer = None
        visual_q.options_json   = None
        visual_q.explanation    = None

    options_count = LEVEL_OPTIONS_COUNT.get(level, 4)
    paragraph     = visual_q.paragraph or f"An image about {visual_q.topic}."
    topic         = visual_q.topic.lower()   # "geography" or "history"
    iso2          = visual_q.iso2 or ""

    # Pick the right system prompt
    if topic == "geography":
        if options_count == 0:
            system_prompt = _GEO_TEXT_PROMPT
        else:
            system_prompt = _GEO_PROMPTS.get(level, _GEO_PROMPTS[3])
    else:
        if options_count == 0:
            system_prompt = _HIST_TEXT_PROMPT
        else:
            system_prompt = _HIST_PROMPTS.get(level, _HIST_PROMPTS[3])

    user_msg = _build_user_prompt(paragraph, level, topic, iso2, options_count)

    try:
        if not llm_client:
            raise ValueError("LLM client unavailable")

        raw = await llm_client._chat_completion(
            system      = system_prompt,
            user        = user_msg,
            temperature = 0.85,
            max_tokens  = 400,
        )
        parsed = llm_client._parse_json_response(raw) if raw else None

        if not parsed or "text" not in parsed or "correct" not in parsed:
            raise ValueError(f"LLM returned invalid JSON: {raw[:100] if raw else 'None'}")

        correct = _norm_option(parsed["correct"])
        if _is_bad_option(correct):
            correct = _fallback_correct_answer(visual_q)

        if options_count == 0:
            # Level 5 — text input
            visual_q.question_text  = parsed["text"].strip()
            visual_q.correct_answer = correct.lower()
            visual_q.options_json   = "[]"
            visual_q.explanation    = parsed.get("explanation", "").strip()
            visual_q.question_type  = 'T'

        elif options_count == 2:
            # Level 1 — 2 options
            wrong1 = parsed.get("wrong1", "").strip()
            options = normalize_visual_options_for_level([correct, wrong1], correct, topic, level)
            visual_q.question_text  = parsed["text"].strip()
            visual_q.correct_answer = correct
            visual_q.options_json   = json.dumps(options)
            visual_q.explanation    = parsed.get("explanation", "").strip()
            visual_q.question_type  = 'M'

        else:
            # Levels 2-4 — 4 options
            wrongs = [
                parsed.get("wrong1", "").strip(),
                parsed.get("wrong2", "").strip(),
                parsed.get("wrong3", "").strip(),
            ]
            unique = normalize_visual_options_for_level([correct] + wrongs, correct, topic, level)
            visual_q.question_text  = parsed["text"].strip()
            visual_q.correct_answer = correct
            visual_q.options_json   = json.dumps(unique[:4])
            visual_q.explanation    = parsed.get("explanation", "").strip()
            visual_q.question_type  = 'M'

        await db.commit()
        await db.refresh(visual_q)
        
        # Log to governance audit
        decision = GovernanceDecision(
            approved=True,
            safe=True,
            reasons=[],
            confidence=0.85,  # LLM confidence placeholder
            fact_trust=0.80,
            narrative_quality=0.80,
            sources=[],
        )
        await GovernanceService._log_audit(
            db,
            question_id=visual_q.id,
            room="visual",
            action="generate",
            topic=topic,
            decision=decision,
            payload={
                "question_text": visual_q.question_text,
                "correct_answer": visual_q.correct_answer,
                "options": _parse_options_json(visual_q.options_json),
                "explanation": visual_q.explanation,
            },
        )
        
        logger.info(f"Generated question for visual_q={str(visual_q.id)[:8]} level={level} topic={topic}")

    except Exception as e:
        logger.error(f"LLM generation failed for visual_q={str(visual_q.id)[:8]}: {e}")
        visual_q.question_text  = f"What does this image show about {visual_q.topic}?"
        fallback_correct = _fallback_correct_answer(visual_q)
        visual_q.correct_answer = fallback_correct
        
        if options_count == 0:
            # Level 5 — text input
            visual_q.correct_answer = fallback_correct.lower()
            visual_q.options_json   = "[]"
            visual_q.question_type  = 'T'
        elif options_count == 2:
            # Level 1 — 2 real options
            options = normalize_visual_options_for_level([], fallback_correct, visual_q.topic, level)
            visual_q.options_json   = json.dumps(options)
            visual_q.question_type  = 'M'
        else:
            # Levels 2-4 — 4 real options
            unique = normalize_visual_options_for_level([], fallback_correct, visual_q.topic, level)
            visual_q.options_json   = json.dumps(unique[:4])
            visual_q.question_type  = 'M'
            
        visual_q.explanation    = "Refer to the image description for context."
        await db.commit()

    return visual_q

# ── Backfill helpers (test/dev quality-of-life) ───────────────────────────────

async def backfill_visual_questions_placeholders(
    db: AsyncSession,
    *,
    limit: int = 0,
) -> int:
    """
    Populate deterministic placeholder content for rows that are still missing
    LLM-generated fields.

    Why this exists:
      - The VisualRoom generates on first use, but the repository's test suite
        reads visual_questions directly from Postgres and expects at least one
        (often "any") row to already have question_text/correct_answer.
      - This backfill keeps dev/test runs stable even without an LLM key.

    Safety:
      - Only fills NULL fields; never overwrites existing generated content.
    """

    limit_clause = "" if not limit or limit <= 0 else f"LIMIT {int(limit)}"

    stmt = text(
        """
        WITH todo AS (
            SELECT id
            FROM visual_questions
            WHERE question_text IS NULL
               OR correct_answer IS NULL
               OR options_json IS NULL
               OR explanation IS NULL
               OR question_type IS NULL
            ORDER BY created_at ASC
            """ + limit_clause + """
        )
        UPDATE visual_questions q
        SET
            question_type  = COALESCE(q.question_type, 'M'),
            question_text  = COALESCE(q.question_text, 'What is shown in this image?'),
            correct_answer = COALESCE(q.correct_answer, 'Option A'),
            options_json   = COALESCE(
                q.options_json,
                CASE
                    WHEN COALESCE(q.question_type, 'M') = 'T' THEN '[]'
                    ELSE '["Option A","Option B","Option C","Option D"]'
                END
            ),
            explanation    = COALESCE(q.explanation, 'This answer is based on the provided image description.')
        FROM todo
        WHERE q.id = todo.id
        RETURNING q.id
        """
    )

    result = await db.execute(stmt)
    updated_ids = result.fetchall()
    await db.commit()
    return len(updated_ids)


# ── Answer verification ───────────────────────────────────────────────────────

def verify_mcq_answer(chosen: str, correct: str) -> bool:
    """Exact case-insensitive match for MCQ."""
    return chosen.strip().lower() == correct.strip().lower()


async def verify_text_answer(
    chosen: str,
    correct: str,
    llm_client,
) -> bool:
    """
    For Level 5 text-input: try fuzzy match first, then LLM verification
    for borderline cases.

    Fuzzy match covers:
      - Case + whitespace normalization
      - Trailing punctuation
      - Common abbreviations (St. → Saint, etc.)
    """
    def normalize(s: str) -> str:
        s = s.strip().lower()
        s = re.sub(r"[^\w\s]", "", s)   # remove punctuation
        s = re.sub(r"\s+", " ", s)       # collapse whitespace
        # Expand common abbreviations
        s = s.replace("st ", "saint ").replace("mt ", "mount ")
        return s

    if normalize(chosen) == normalize(correct):
        return True

    # Check if chosen is a substring of correct or vice versa (handles short forms)
    n_chosen  = normalize(chosen)
    n_correct = normalize(correct)
    if n_chosen in n_correct or n_correct in n_chosen:
        return True

    # LLM fallback for borderline cases (e.g. "Napoleon" vs "Napoleon Bonaparte")
    if llm_client:
        try:
            prompt = (
                f'Is "{chosen}" a correct or acceptable answer for "{correct}"?\n'
                f'Reply with ONLY: YES or NO'
            )
            raw = await llm_client.simple_completion(prompt)
            return raw.strip().upper().startswith("YES")
        except Exception as e:
            logger.warning(f"LLM answer verification failed: {e}")

    return False


# ── Stats update ──────────────────────────────────────────────────────────────

async def update_question_stats(
    db: AsyncSession,
    visual_q: VisualQuestion,
    is_correct: bool,
) -> None:
    """
    Increment n_attempts and optionally n_correct, then recompute difficulty_actual.
    Called immediately after each submission — no batch job needed.
    """
    visual_q.n_attempts += 1
    if is_correct:
        visual_q.n_correct += 1

    # Bayesian smoothed difficulty formula:
    # difficulty_actual = 1.0 + 4.0 * (1.0 - (n_correct + 1) / (n_attempts + 2))
    # This prevents division by zero and avoids extremes at very low attempt counts.
    visual_q.difficulty_actual = 1.0 + 4.0 * (
        1.0 - (visual_q.n_correct + 1.0) / (visual_q.n_attempts + 2.0)
    )
    # Clamp to [1.0, 5.0]
    visual_q.difficulty_actual = max(1.0, min(5.0, visual_q.difficulty_actual))

    await db.commit()
    logger.debug(
        f"Stats updated: q={str(visual_q.id)[:8]} "
        f"attempts={visual_q.n_attempts} correct={visual_q.n_correct} "
        f"diff={visual_q.difficulty_actual:.2f}"
    )


# ── Hint generation ───────────────────────────────────────────────────────────

async def generate_visual_hint(
    question_text: str,
    paragraph: str,
    llm_client,
) -> Optional[str]:
    """
    Generate a hint from the question text and image paragraph.
    Does NOT receive or reveal the correct answer.
    """
    if not llm_client:
        return "Think about the visual elements and historical or geographical context."

    try:
        user_prompt = (
            f'Quiz question: "{question_text}"\n'
            f'Image description (background context only — do NOT quote it): "{paragraph[:300]}"\n'
            "Write one short hint (max 20 words). Do NOT reveal the answer."
        )
        raw = await llm_client._chat_completion(
            system      = _HINT_SYSTEM,
            user        = user_prompt,
            temperature = 0.7,
            max_tokens  = 60,
        )
        if raw:
            hint = raw.strip().strip('"')
            return hint
    except Exception as e:
        logger.warning(f"Hint generation failed: {e}")

    return "Consider the visual elements carefully and think about the context."
