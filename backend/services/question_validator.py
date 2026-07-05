"""
services/question_validator.py
Quality filter — rejects bad questions before they reach users.

Checks (in order):
  1. Length  — question text must be 15-50 words
  2. Narrative — narrative_quality score must be ≥ 0.7
  3. Structured facts — at least one fact present in bundle
  4. Sensitive keywords — hard-block via governance blocklist
  5. Options quality — all options non-empty, no duplicates

On 3x regeneration failure → caller falls back to Wikipedia-only path.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from services.source_blender import SourceBundle
from services.governance_service import is_sensitive

logger = logging.getLogger(__name__)


# ─── Validation result ────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    passed:         bool
    rejection_code: Optional[str]     = None   # e.g. "TOO_SHORT", "SENSITIVE"
    messages:       list[str]         = field(default_factory=list)

    @classmethod
    def ok(cls) -> "ValidationResult":
        return cls(passed=True)

    @classmethod
    def fail(cls, code: str, msg: str) -> "ValidationResult":
        return cls(passed=False, rejection_code=code, messages=[msg])


# ─── Individual checks ────────────────────────────────────────────────────────

def _check_length(question_text: str) -> Optional[ValidationResult]:
    """Question must be between 15 and 50 words."""
    words = question_text.split()
    if len(words) < 15:
        return ValidationResult.fail(
            "TOO_SHORT",
            f"Question too short: {len(words)} words (min 15). Text: {question_text[:80]!r}"
        )
    if len(words) > 50:
        return ValidationResult.fail(
            "TOO_LONG",
            f"Question too long: {len(words)} words (max 50). Text: {question_text[:80]!r}"
        )
    return None


def _check_narrative_score(
    narrative_quality: float,
    threshold: float = 0.70,
) -> Optional[ValidationResult]:
    """Narrative quality must be ≥ 0.70 (boring questions rejected)."""
    if narrative_quality < threshold:
        return ValidationResult.fail(
            "LOW_NARRATIVE",
            f"Narrative quality {narrative_quality:.2f} below threshold {threshold}"
        )
    return None


def _check_structured_facts(bundle: SourceBundle) -> Optional[ValidationResult]:
    """At least one structured fact must be present."""
    if not bundle.structured_facts:
        return ValidationResult.fail(
            "NO_STRUCTURED_FACTS",
            "Question generated with zero structured facts — unreliable"
        )
    return None


def _check_sensitive(question_text: str, topic: str) -> Optional[ValidationResult]:
    """Hard-block any question matching the governance sensitive-keyword list."""
    blocked, keywords = is_sensitive(question_text, topic)
    if blocked:
        return ValidationResult.fail(
            "SENSITIVE_CONTENT",
            f"Sensitive keywords detected: {keywords}. Question blocked."
        )
    return None


def _check_options(options: list[str]) -> Optional[ValidationResult]:
    """Options must be non-empty, distinct, and at least 2."""
    if len(options) < 2:
        return ValidationResult.fail(
            "TOO_FEW_OPTIONS",
            f"Only {len(options)} option(s) — minimum 2 required"
        )
    cleaned = [o.strip().lower() for o in options if o.strip()]
    if len(set(cleaned)) < len(cleaned):
        return ValidationResult.fail(
            "DUPLICATE_OPTIONS",
            "Duplicate options detected"
        )
    return None


def _check_correct_answer_in_options(
    correct_answer: str,
    options: list[str],
) -> Optional[ValidationResult]:
    """The correct answer must appear in the options list."""
    normalized = [o.strip().lower() for o in options]
    if correct_answer.strip().lower() not in normalized:
        return ValidationResult.fail(
            "CORRECT_NOT_IN_OPTIONS",
            f"Correct answer {correct_answer!r} not found in options {options}"
        )
    return None


# ─── Main validator ───────────────────────────────────────────────────────────

def validate_question(
    question_text:     str,
    options:           list[str],
    correct_answer:    str,
    topic:             str,
    narrative_quality: float,
    bundle:            SourceBundle,
) -> ValidationResult:
    """
    Run all quality checks and return the first failure (fast-fail),
    or ValidationResult.ok() if all pass.

    This is synchronous — it's called after the async scoring step.
    """
    checks = [
        _check_length(question_text),
        _check_narrative_score(narrative_quality),
        _check_structured_facts(bundle),
        _check_sensitive(question_text, topic),
        _check_options(options),
        _check_correct_answer_in_options(correct_answer, options),
    ]

    for result in checks:
        if result is not None:
            logger.info(
                f"[Validator] FAIL code={result.rejection_code} "
                f"msg={result.messages[0][:100]}"
            )
            return result

    logger.debug(f"[Validator] PASS for question: {question_text[:60]!r}")
    return ValidationResult.ok()


# ─── Attempt counter helper ───────────────────────────────────────────────────

MAX_REGENERATION_ATTEMPTS = 3


class RegenerationTracker:
    """
    Tracks how many times generation was attempted for a single request.
    On MAX_REGENERATION_ATTEMPTS reached, the caller should fall back
    to the Wikipedia-only (classic) path.
    """
    def __init__(self):
        self.attempts = 0
        self.rejection_codes: list[str] = []

    def record_failure(self, code: str) -> None:
        self.attempts += 1
        self.rejection_codes.append(code)

    @property
    def exhausted(self) -> bool:
        return self.attempts >= MAX_REGENERATION_ATTEMPTS

    @property
    def summary(self) -> str:
        return f"{self.attempts}/{MAX_REGENERATION_ATTEMPTS} attempts, codes={self.rejection_codes}"
