"""Audit and repair question-bank/custom-topic data integrity.

Usage:
    python scripts/repair_data_integrity.py --dry-run
    python scripts/repair_data_integrity.py --apply

The script never prints secrets. Dry-run mode performs no writes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from config import DATABASE_URL  # noqa: E402
from database.concept_models import Concept, QuestionConcept  # noqa: E402
from database.custom_models import Fact, Topic  # noqa: E402
from database.models import QuestionBank  # noqa: E402
from database.visual_models import VisualSession  # noqa: E402
from services.concept_service import ConceptDiscoveryService, normalize_concept_parts  # noqa: E402
from services.governance_service import GovernanceService  # noqa: E402
from services.question_sources import summarize_source_counts  # noqa: E402


GENERIC_FALLBACK_EXPLANATION_FLAG = "Needs future regenerated explanation."
MANUAL_REVIEW_ANSWER = "Needs manual review"
PLACEHOLDER_DATA_FLAG = "placeholder_question_data"
PLACEHOLDER_VALUE_RE = re.compile(r"^(?:option|choice|answer)\s*([a-d]|[1-4])$", re.IGNORECASE)
REQUIRED_COLUMNS_BY_TABLE = {
    "concepts": {"scope"},
    "visual_questions": {"id", "topic", "difficulty_actual", "question_text"},
    "visual_sessions": {"id", "user_id", "started_at", "ended_at", "is_completed"},
}


def expected_alembic_heads() -> list[str]:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return sorted(ScriptDirectory.from_config(config).get_heads())


async def current_alembic_revisions(db: AsyncSession) -> list[str]:
    result = await db.execute(
        text(
            """
            SELECT version_num
            FROM alembic_version
            ORDER BY version_num
            """
        )
    )
    return [str(row[0]) for row in result.fetchall()]


async def missing_required_columns(db: AsyncSession) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {}
    for table_name, columns in REQUIRED_COLUMNS_BY_TABLE.items():
        result = await db.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = :table_name
                """
            ),
            {"table_name": table_name},
        )
        present = {str(row[0]) for row in result.fetchall()}
        table_missing = sorted(columns - present)
        if table_missing:
            missing[table_name] = table_missing
    return missing


async def schema_preflight(db: AsyncSession) -> dict[str, Any] | None:
    expected_heads = expected_alembic_heads()
    current_revisions = await current_alembic_revisions(db)
    missing_columns = await missing_required_columns(db)
    if current_revisions != expected_heads or missing_columns:
        return {
            "status": "schema_out_of_date",
            "message": "Database schema is not at the local Alembic head. Run `alembic upgrade head` from backend, then rerun this repair script.",
            "current_revisions": current_revisions,
            "expected_heads": expected_heads,
            "missing_required_columns": missing_columns,
        }
    return None


def normalize_topic_family(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized.startswith("history"):
        return "history"
    if normalized.startswith("geography"):
        return "geography"
    if normalized in {"mixed", "mix"}:
        return "mixed"
    return "mixed"


def normalize_topic_display(value: str | None) -> str:
    return normalize_topic_family(value).title()


def is_blank(value: Any) -> bool:
    return not str(value or "").strip()


def parse_options_json(value: str | None) -> tuple[list[str], str | None]:
    if is_blank(value):
        return [], "empty"
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return [], "invalid_json"
    if not isinstance(parsed, list):
        return [], "not_list"
    options = [str(item).strip() for item in parsed if str(item).strip()]
    if not options:
        return [], "empty_list"
    if len({option.casefold() for option in options}) != len(options):
        return options, "duplicate_options"
    if len(options) < 2:
        return options, "too_few_options"
    return options, None


def placeholder_option_index(value: str | None) -> int | None:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return None
    upper = text.upper()
    if upper in {"A", "B", "C", "D"}:
        return ord(upper) - ord("A")
    match = PLACEHOLDER_VALUE_RE.fullmatch(text)
    if not match:
        return None
    token = match.group(1).upper()
    if token in {"A", "B", "C", "D"}:
        return ord(token) - ord("A")
    return int(token) - 1


def is_placeholder_value(value: str | None) -> bool:
    return placeholder_option_index(value) is not None


def options_integrity_error(options_json: str | None, correct_answer: str | None) -> str | None:
    options, error = parse_options_json(options_json)
    if error:
        return error
    if any(is_placeholder_value(option) for option in options):
        return "placeholder_options"
    if is_placeholder_value(correct_answer):
        return "placeholder_correct_answer"
    correct = str(correct_answer or "").strip().casefold()
    if correct and correct not in {option.casefold() for option in options}:
        return "correct_answer_missing_from_options"
    return None


# Rows with no checked timestamp have never been stamped by the current
# governance pipeline, even when their default booleans are already true.
def governance_needs_backfill(question: QuestionBank) -> bool:
    if getattr(question, "gov_approved", None) is False or getattr(question, "gov_safe", None) is False:
        return False
    if PLACEHOLDER_DATA_FLAG in str(getattr(question, "gov_flags_json", "") or ""):
        return False
    return (
        getattr(question, "gov_checked_at", None) is None
        or getattr(question, "gov_approved", None) is None
        or getattr(question, "gov_safe", None) is None
    )


def fallback_explanation(question_text: str | None, correct_answer: str | None, topic: str | None) -> str:
    answer = str(correct_answer or "the correct answer").strip() or "the correct answer"
    family = normalize_topic_display(topic)
    question = str(question_text or "").strip()
    if question:
        return (
            f"In this {family} question, the correct answer is {answer}. "
            f"{GENERIC_FALLBACK_EXPLANATION_FLAG}"
        )
    return f"The correct answer is {answer}. {GENERIC_FALLBACK_EXPLANATION_FLAG}"


def fallback_options(correct_answer: str | None, topic: str | None) -> list[str]:
    answer = str(correct_answer or "").strip()
    if not answer:
        answer = "The correct answer"
    family = normalize_topic_display(topic)
    candidates = [
        answer,
        f"A different {family.lower()} answer",
        "Not enough information",
        "None of the listed alternatives",
    ]
    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        value = candidate.strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    suffix = 1
    while len(out) < 4:
        value = f"Placeholder option {suffix}"
        suffix += 1
        if value.casefold() not in seen:
            seen.add(value.casefold())
            out.append(value)
    return out[:4]


def _merge_flags(raw_flags: str | None, *flags: str) -> str:
    current: list[str] = []
    if raw_flags:
        try:
            parsed = json.loads(raw_flags)
            if isinstance(parsed, list):
                current = [str(item) for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            current = [str(raw_flags)]
    seen = {item.casefold() for item in current}
    for flag in flags:
        if flag and flag.casefold() not in seen:
            current.append(flag)
            seen.add(flag.casefold())
    return json.dumps(current, ensure_ascii=True)


async def audit_database(db: AsyncSession) -> dict[str, Any]:
    questions = (await db.execute(select(QuestionBank))).scalars().all()
    concepts = (await db.execute(select(Concept))).scalars().all()

    option_errors: dict[str, int] = defaultdict(int)
    source_counts: dict[str, int] = defaultdict(int)
    for question in questions:
        error = options_integrity_error(question.options_json, question.correct_answer)
        if error:
            option_errors[error] += 1
        source_counts[str(question.source or "unknown").strip().lower() or "unknown"] += 1

    primary_link_subq = (
        select(QuestionConcept.question_id, func.count(QuestionConcept.id).label("primary_count"))
        .where(QuestionConcept.is_primary == True)  # noqa: E712
        .group_by(QuestionConcept.question_id)
        .subquery()
    )

    duplicate_primary_questions = await db.scalar(
        select(func.count()).select_from(primary_link_subq).where(primary_link_subq.c.primary_count > 1)
    ) or 0

    missing_primary_links = await db.scalar(
        select(func.count())
        .select_from(QuestionBank)
        .outerjoin(
            QuestionConcept,
            (QuestionConcept.question_id == QuestionBank.id)
            & (QuestionConcept.is_primary == True),  # noqa: E712
        )
        .where(QuestionConcept.id.is_(None))
    ) or 0

    primary_mismatches = await db.scalar(
        select(func.count())
        .select_from(QuestionBank)
        .join(
            QuestionConcept,
            (QuestionConcept.question_id == QuestionBank.id)
            & (QuestionConcept.is_primary == True),  # noqa: E712
        )
        .where(
            or_(
                QuestionBank.primary_concept_id.is_(None),
                QuestionBank.primary_concept_id != QuestionConcept.concept_id,
            )
        )
    ) or 0

    dangling_question_links = await db.scalar(
        select(func.count())
        .select_from(QuestionConcept)
        .outerjoin(QuestionBank, QuestionBank.id == QuestionConcept.question_id)
        .where(QuestionBank.id.is_(None))
    ) or 0

    dangling_concept_links = await db.scalar(
        select(func.count())
        .select_from(QuestionConcept)
        .outerjoin(Concept, Concept.id == QuestionConcept.concept_id)
        .where(Concept.id.is_(None))
    ) or 0
    concept_topic_mismatches = 0
    linked_rows = (
        await db.execute(
            select(QuestionBank.topic, Concept.topic)
            .select_from(QuestionConcept)
            .join(QuestionBank, QuestionBank.id == QuestionConcept.question_id)
            .join(Concept, Concept.id == QuestionConcept.concept_id)
        )
    ).all()
    for question_topic, concept_topic in linked_rows:
        if normalize_topic_family(question_topic) != normalize_topic_family(concept_topic):
            concept_topic_mismatches += 1

    custom_facts_from_flagged_questions = await db.scalar(
        select(func.count())
        .select_from(Fact)
        .join(QuestionBank, Fact.source_question_id == QuestionBank.id)
        .where(
            or_(
                QuestionBank.gov_approved == False,  # noqa: E712
                QuestionBank.gov_safe == False,  # noqa: E712
                QuestionBank.gov_flags_json.contains(PLACEHOLDER_DATA_FLAG),
            )
        )
    ) or 0
    visual_completed_without_ended = await db.scalar(
        select(func.count())
        .select_from(VisualSession)
        .where(VisualSession.is_completed == True, VisualSession.ended_at.is_(None))  # noqa: E712
    ) or 0
    visual_ended_without_completed = await db.scalar(
        select(func.count())
        .select_from(VisualSession)
        .where(VisualSession.is_completed == False, VisualSession.ended_at.is_not(None))  # noqa: E712
    ) or 0
    visual_ended_before_started = await db.scalar(
        select(func.count())
        .select_from(VisualSession)
        .where(VisualSession.ended_at.is_not(None), VisualSession.ended_at < VisualSession.started_at)
    ) or 0

    redundant_concept_prefixes = 0
    for concept in concepts:
        next_name, next_topic, next_scope = normalize_concept_parts(
            concept.name,
            concept.topic,
            concept.scope,
        )
        if next_name != concept.name or next_topic != concept.topic or next_scope != concept.scope:
            redundant_concept_prefixes += 1

    return {
        "question_bank_total": len(questions),
        "question_source_summary": summarize_source_counts(source_counts),
        "critical_empty_fields": {
            "question_text": sum(1 for q in questions if is_blank(q.question_text)),
            "correct_answer": sum(1 for q in questions if is_blank(q.correct_answer)),
            "options_json": sum(1 for q in questions if is_blank(q.options_json)),
            "topic": sum(1 for q in questions if is_blank(q.topic)),
            "explanation": sum(1 for q in questions if is_blank(q.explanation)),
        },
        "invalid_options_json": sum(option_errors.values()),
        "invalid_options_by_reason": dict(sorted(option_errors.items())),
        "missing_primary_concept_id": sum(1 for q in questions if q.primary_concept_id is None),
        "question_concepts": {
            "dangling_question_links": int(dangling_question_links),
            "dangling_concept_links": int(dangling_concept_links),
            "missing_primary_links": int(missing_primary_links),
            "duplicate_primary_question_count": int(duplicate_primary_questions),
            "primary_mismatches": int(primary_mismatches),
            "topic_mismatches": int(concept_topic_mismatches),
        },
        "concepts_total": len(concepts),
        "concepts_redundant_scope_prefixes": int(redundant_concept_prefixes),
        "custom_topics_total": int(await db.scalar(select(func.count()).select_from(Topic)) or 0),
        "custom_facts_total": int(await db.scalar(select(func.count()).select_from(Fact)) or 0),
        "custom_facts_from_flagged_questions": int(custom_facts_from_flagged_questions),
        "visual_sessions": {
            "completed_without_ended_at": int(visual_completed_without_ended),
            "ended_without_completed": int(visual_ended_without_completed),
            "ended_before_started": int(visual_ended_before_started),
        },
        "question_bank_flagged_for_review": sum(
            1
            for q in questions
            if q.gov_approved is False
            or q.gov_safe is False
            or PLACEHOLDER_DATA_FLAG in str(q.gov_flags_json or "")
        ),
        "question_bank_governance_unchecked": sum(1 for q in questions if governance_needs_backfill(q)),
    }


async def _load_question_links(db: AsyncSession) -> tuple[dict[Any, list[QuestionConcept]], dict[Any, QuestionConcept]]:
    rows = (await db.execute(select(QuestionConcept).order_by(QuestionConcept.created_at))).scalars().all()
    by_question: dict[Any, list[QuestionConcept]] = defaultdict(list)
    primary_by_question: dict[Any, QuestionConcept] = {}
    for link in rows:
        by_question[link.question_id].append(link)
        if link.is_primary and link.question_id not in primary_by_question:
            primary_by_question[link.question_id] = link
    return by_question, primary_by_question


async def repair_database(db: AsyncSession, *, apply: bool, limit: int | None = None) -> dict[str, Any]:
    questions_stmt = select(QuestionBank).order_by(QuestionBank.created_at, QuestionBank.id)
    if limit is not None:
        questions_stmt = questions_stmt.limit(limit)
    questions = (await db.execute(questions_stmt)).scalars().all()
    links_by_question, primary_by_question = await _load_question_links(db)

    changes = {
        "primary_concept_id_backfilled_from_primary_link": 0,
        "primary_link_promoted_from_existing_link": 0,
        "primary_link_created_from_primary_concept_id": 0,
        "concepts_inferred_for_unlinked_questions": 0,
        "topics_normalized": 0,
        "blank_explanations_filled": 0,
        "invalid_options_repaired": 0,
        "placeholder_correct_answers_mapped": 0,
        "placeholder_questions_flagged_for_review": 0,
        "custom_facts_from_flagged_questions_removed": 0,
        "custom_topic_fact_counts_refreshed": 0,
        "duplicate_primary_links_demoted": 0,
        "dangling_question_concept_links_removed": 0,
        "governance_rows_backfilled": 0,
        "governance_backfill_skipped_disabled": 0,
        "concept_names_normalized": 0,
        "concept_name_normalization_conflicts": 0,
        "visual_completed_sessions_ended_at_backfilled": 0,
    }

    if apply:
        dangling_ids = (
            await db.execute(
                select(QuestionConcept.id)
                .outerjoin(QuestionBank, QuestionBank.id == QuestionConcept.question_id)
                .where(QuestionBank.id.is_(None))
            )
        ).scalars().all()
        if dangling_ids:
            await db.execute(delete(QuestionConcept).where(QuestionConcept.id.in_(dangling_ids)))
            changes["dangling_question_concept_links_removed"] = len(dangling_ids)

    for question in questions:
        expected_topic = normalize_topic_display(question.topic)
        if (question.topic or "") != expected_topic:
            changes["topics_normalized"] += 1
            if apply:
                question.topic = expected_topic

        if is_blank(question.explanation):
            changes["blank_explanations_filled"] += 1
            if apply:
                question.explanation = fallback_explanation(
                    question.question_text,
                    question.correct_answer,
                    question.topic,
                )

        effective_correct_answer = question.correct_answer
        effective_options_json = question.options_json
        options, _options_error = parse_options_json(question.options_json)
        placeholder_index = placeholder_option_index(question.correct_answer)
        has_placeholder_options = any(is_placeholder_value(option) for option in options)
        if placeholder_index is not None and options and not has_placeholder_options and placeholder_index < len(options):
            changes["placeholder_correct_answers_mapped"] += 1
            effective_correct_answer = options[placeholder_index]
            if apply:
                question.correct_answer = options[placeholder_index]
        elif placeholder_index is not None or has_placeholder_options:
            changes["placeholder_questions_flagged_for_review"] += 1
            effective_correct_answer = (
                question.correct_answer
                if question.correct_answer and placeholder_index is None
                else MANUAL_REVIEW_ANSWER
            )
            effective_options_json = json.dumps(
                fallback_options(effective_correct_answer, question.topic),
                ensure_ascii=True,
            )
            if apply:
                question.correct_answer = effective_correct_answer
                question.options_json = effective_options_json
                question.explanation = (
                    f"{fallback_explanation(question.question_text, question.correct_answer, question.topic)} "
                    "Original answer/options looked like placeholders and require human review."
                )
                question.gov_approved = False
                question.gov_safe = False
                question.gov_flags_json = _merge_flags(question.gov_flags_json, PLACEHOLDER_DATA_FLAG)

        if options_integrity_error(effective_options_json, effective_correct_answer):
            changes["invalid_options_repaired"] += 1
            if apply:
                question.options_json = json.dumps(
                    fallback_options(question.correct_answer, question.topic),
                    ensure_ascii=True,
                )
                if GENERIC_FALLBACK_EXPLANATION_FLAG not in (question.explanation or ""):
                    question.explanation = (
                        f"{question.explanation or fallback_explanation(question.question_text, question.correct_answer, question.topic)} "
                        f"{GENERIC_FALLBACK_EXPLANATION_FLAG}"
                    ).strip()

        if governance_needs_backfill(question):
            if not GovernanceService.enabled():
                changes["governance_backfill_skipped_disabled"] += 1
            else:
                changes["governance_rows_backfilled"] += 1
                if apply:
                    parsed_options, _ = parse_options_json(question.options_json)
                    decision = await GovernanceService.evaluate_candidate(
                        db,
                        question_id=question.id,
                        room=str(question.source or "repair"),
                        action="persist",
                        topic=question.topic or "Mixed",
                        question_text=question.question_text or "",
                        correct_answer=question.correct_answer or "",
                        explanation=question.explanation or "",
                        options=parsed_options,
                        sources=None,
                    )
                    await GovernanceService.apply_decision_to_persisted_row(
                        db,
                        row=question,
                        decision=decision,
                    )

        primary_links = [link for link in links_by_question.get(question.id, []) if link.is_primary]
        if len(primary_links) > 1:
            changes["duplicate_primary_links_demoted"] += len(primary_links) - 1
            if apply:
                for duplicate in primary_links[1:]:
                    duplicate.is_primary = False

        primary_link = primary_by_question.get(question.id)
        if primary_link is not None:
            if question.primary_concept_id != primary_link.concept_id:
                changes["primary_concept_id_backfilled_from_primary_link"] += 1
                if apply:
                    question.primary_concept_id = primary_link.concept_id
            continue

        links = links_by_question.get(question.id, [])
        if links:
            chosen = links[0]
            changes["primary_link_promoted_from_existing_link"] += 1
            if apply:
                chosen.is_primary = True
                question.primary_concept_id = chosen.concept_id
            continue

        if question.primary_concept_id is not None:
            concept = await db.get(Concept, question.primary_concept_id)
            if concept is not None:
                changes["primary_link_created_from_primary_concept_id"] += 1
                if apply:
                    db.add(
                        QuestionConcept(
                            question_id=question.id,
                            concept_id=question.primary_concept_id,
                            is_primary=True,
                        )
                    )
                continue

        changes["concepts_inferred_for_unlinked_questions"] += 1
        if apply:
            topic_family = normalize_topic_family(question.topic)
            concept = await ConceptDiscoveryService.ensure_question_has_concept(
                db=db,
                question_text=question.question_text or "",
                correct_answer=question.correct_answer or "",
                topic=topic_family,
                explanation=question.explanation or "",
                topic_label=question.topic or topic_family.title(),
            )
            question.primary_concept_id = concept.id
            db.add(
                QuestionConcept(
                    question_id=question.id,
                    concept_id=concept.id,
                    is_primary=True,
                )
            )

    if apply:
        await db.flush()
        await db.execute(
            update(QuestionBank)
            .where(QuestionBank.topic.is_(None))
            .values(topic="Mixed")
            )

    concepts = (await db.execute(select(Concept).order_by(Concept.created_at, Concept.id))).scalars().all()
    for concept in concepts:
        next_name, next_topic, next_scope = normalize_concept_parts(
            concept.name,
            concept.topic,
            concept.scope,
        )
        if next_name == concept.name and next_topic == concept.topic and next_scope == concept.scope:
            continue
        existing = await db.scalar(
            select(Concept).where(
                func.lower(Concept.name) == next_name.lower(),
                func.lower(Concept.topic) == next_topic.lower(),
                func.lower(Concept.scope) == next_scope.lower(),
                Concept.id != concept.id,
            )
        )
        if existing:
            changes["concept_name_normalization_conflicts"] += 1
            continue
        changes["concept_names_normalized"] += 1
        if apply:
            concept.name = next_name
            concept.topic = next_topic
            concept.scope = next_scope

    completed_without_end = (
        await db.execute(
            select(VisualSession).where(
                VisualSession.is_completed == True,  # noqa: E712
                VisualSession.ended_at.is_(None),
            )
        )
    ).scalars().all()
    if completed_without_end:
        changes["visual_completed_sessions_ended_at_backfilled"] = len(completed_without_end)
        if apply:
            for session in completed_without_end:
                session.ended_at = session.started_at

    flagged_fact_ids = (
        await db.execute(
            select(Fact.id)
            .join(QuestionBank, Fact.source_question_id == QuestionBank.id)
            .where(
                or_(
                    QuestionBank.gov_approved == False,  # noqa: E712
                    QuestionBank.gov_safe == False,  # noqa: E712
                    QuestionBank.gov_flags_json.contains(PLACEHOLDER_DATA_FLAG),
                )
            )
        )
    ).scalars().all()
    if flagged_fact_ids:
        changes["custom_facts_from_flagged_questions_removed"] = len(flagged_fact_ids)
        if apply:
            await db.execute(delete(Fact).where(Fact.id.in_(flagged_fact_ids)))

    topic_rows = (await db.execute(select(Topic))).scalars().all()
    for topic_row in topic_rows:
        topic_label = f"{topic_row.type} - {topic_row.name}"
        fact_count = int(
            await db.scalar(select(func.count()).select_from(Fact).where(Fact.topic == topic_label))
            or 0
        )
        if int(topic_row.total_facts_count or 0) != fact_count:
            changes["custom_topic_fact_counts_refreshed"] += 1
            if apply:
                topic_row.total_facts_count = fact_count
    if apply:
        await db.commit()
    else:
        await db.rollback()

    return {
        "mode": "apply" if apply else "dry-run",
        "questions_scanned": len(questions),
        "planned_or_applied_changes": changes,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    engine = create_async_engine(DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            drift = await schema_preflight(db)
            if drift is not None:
                return {
                    **drift,
                    "dry_run": not args.apply,
                }
            before = await audit_database(db)
            repair = await repair_database(db, apply=args.apply, limit=args.limit)
            after = await audit_database(db) if args.apply else before
            return {
                "status": "ok",
                "dry_run": not args.apply,
                "before": before,
                "repair": repair,
                "after": after,
            }
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Audit and show planned repairs without writing")
    mode.add_argument("--apply", action="store_true", help="Apply deterministic repairs")
    parser.add_argument("--limit", type=int, default=None, help="Optional question processing limit")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, indent=2, sort_keys=True))
    if result.get("status") != "ok":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
