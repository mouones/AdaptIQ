"""Question-bank source taxonomy and reporting helpers.

`QuestionBank.source` is provenance, not a room selector by itself. This module
centralizes source categories so admin reporting and room reuse filters agree.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from config import (
    QUESTION_SOURCE_ADMIN,
    QUESTION_SOURCE_CHALLENGE_GENERATED,
    QUESTION_SOURCE_CLASSIC_GENERATED,
    QUESTION_SOURCE_CUSTOM_GENERATED_PREFIXES,
    QUESTION_SOURCE_GENERATED_EXACT,
    QUESTION_SOURCE_SEED,
)


def normalize_question_source(source: Any) -> str:
    """Return a lowercase source value, using `unknown` for blanks."""
    value = str(source or "").strip().lower()
    return value or "unknown"


def source_starts_with(source: str, prefixes: tuple[str, ...]) -> bool:
    """Match source families such as `custom_llm_simple` and `custom_rag_wikipedia`."""
    normalized = normalize_question_source(source)
    return any(normalized == prefix or normalized.startswith(f"{prefix}_") for prefix in prefixes)


def categorize_question_source(source: Any) -> str:
    """Map a raw source value into a stable admin/reporting category."""
    normalized = normalize_question_source(source)
    if normalized in QUESTION_SOURCE_SEED:
        return "seeded"
    if normalized in QUESTION_SOURCE_ADMIN:
        return "admin"
    if normalized in QUESTION_SOURCE_CLASSIC_GENERATED:
        return "classic_generated"
    if normalized in QUESTION_SOURCE_CHALLENGE_GENERATED:
        return "challenge_generated"
    if source_starts_with(normalized, QUESTION_SOURCE_CUSTOM_GENERATED_PREFIXES):
        if source_starts_with(normalized, ("custom_template",)):
            return "custom_template"
        if source_starts_with(normalized, ("custom_rag",)):
            return "custom_rag"
        return "custom_generated"
    return "unknown"


def is_generated_question_source(source: Any) -> bool:
    """Return True for LLM/RAG/template/probe generated bank rows."""
    normalized = normalize_question_source(source)
    return normalized in QUESTION_SOURCE_GENERATED_EXACT or source_starts_with(
        normalized,
        QUESTION_SOURCE_CUSTOM_GENERATED_PREFIXES,
    )


def is_non_classic_question_source(source: Any) -> bool:
    """Return True for sources Classic Room should not reuse as baseline questions."""
    normalized = normalize_question_source(source)
    return normalized in QUESTION_SOURCE_CHALLENGE_GENERATED or source_starts_with(
        normalized,
        QUESTION_SOURCE_CUSTOM_GENERATED_PREFIXES,
    )


NON_CLASSIC_SOURCE_VALUES: tuple[str, ...] = (
    *QUESTION_SOURCE_CHALLENGE_GENERATED,
    "custom_llm",
    "custom_llm_simple",
    "custom_template",
    "custom_template_simple",
    "custom_rag",
)
NON_CLASSIC_SOURCE_PREFIXES: tuple[str, ...] = QUESTION_SOURCE_CUSTOM_GENERATED_PREFIXES

NON_CHALLENGE_SOURCE_VALUES: tuple[str, ...] = NON_CLASSIC_SOURCE_VALUES
NON_CHALLENGE_SOURCE_PREFIXES: tuple[str, ...] = NON_CLASSIC_SOURCE_PREFIXES


def summarize_source_counts(raw_counts: Mapping[Any, int]) -> dict[str, Any]:
    """Build admin-facing totals from raw `source -> count` rows."""
    by_source = {
        normalize_question_source(source): int(count or 0)
        for source, count in raw_counts.items()
    }
    by_category: dict[str, int] = {
        "seeded": 0,
        "admin": 0,
        "classic_generated": 0,
        "challenge_generated": 0,
        "custom_generated": 0,
        "custom_template": 0,
        "custom_rag": 0,
        "unknown": 0,
    }
    for source, count in by_source.items():
        by_category[categorize_question_source(source)] = (
            by_category.get(categorize_question_source(source), 0) + int(count)
        )

    generated = sum(
        count for source, count in by_source.items() if is_generated_question_source(source)
    )

    return {
        "generated": int(generated),
        "seeded": int(by_category.get("seeded", 0)),
        "admin": int(by_category.get("admin", 0)),
        "unknown": int(by_category.get("unknown", 0)),
        "by_category": by_category,
        "by_source": dict(sorted(by_source.items())),
    }
