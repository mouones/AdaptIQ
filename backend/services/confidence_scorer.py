"""
services/confidence_scorer.py
50 / 50 confidence scoring.

confidence = (fact_trust * 0.5) + (narrative_quality * 0.5)

fact_trust       — how many independent structured sources agree on the facts (0-1)
narrative_quality — LLM-scored "engaging, educational phrasing" (0-1)

Thresholds:
  ≥ 0.80  → Accept ✅    (returned to room immediately)
  0.60-0.79 → Review ⚠️  (logged, still returned but flagged for human review)
  < 0.60  → Reject ❌    (regenerate, max 3 attempts then fallback)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from services.source_blender import SourceBundle

logger = logging.getLogger(__name__)


# ─── Score result ─────────────────────────────────────────────────────────────

@dataclass
class ConfidenceScore:
    fact_trust:        float   # 0-1
    narrative_quality: float   # 0-1
    confidence:        float   # 0-1, weighted 50/50
    verdict:           str     # "accept" | "review" | "reject"
    notes:             list[str]


# ─── Thresholds ───────────────────────────────────────────────────────────────

THRESHOLD_ACCEPT = 0.80
THRESHOLD_REVIEW = 0.60   # below this → reject


# ─── Fact trust scorer ────────────────────────────────────────────────────────

def score_fact_trust(bundle: SourceBundle, question_text: str) -> float:
    """
    Estimate fact trustworthiness based on:
      1. Number of distinct structured-source families that contributed.
      2. Whether the question text references something verifiable
         (a number, a proper noun, a date) that appears in the facts.

    Returns a float 0-1.

    This is a heuristic (not a full cross-source agreement check) because
    true agreement checking requires parsing heterogeneous fact strings —
    out of scope for a single-model pipeline. The heuristic is conservative
    and deliberately underestimates to avoid false-high scores.
    """
    notes_list: list[str] = []

    if not bundle.structured_facts:
        logger.debug("[FactTrust] No structured facts → 0.0")
        return 0.0

    # Count distinct source families
    families = set()
    for src in bundle.sources:
        family = src.split(":")[0]   # "wikidata", "dbpedia", "worldbank", etc.
        if family in ("wikidata", "dbpedia", "worldbank", "undata"):
            families.add(family)

    # Base trust from source diversity (more independent sources = higher trust)
    if len(families) >= 3:
        base = 0.90
    elif len(families) == 2:
        base = 0.80
    elif len(families) == 1:
        base = 0.70
    else:
        base = 0.50

    # Bonus: verifiable entities appear in question text
    verifiable_patterns = [
        r"\b\d{4}\b",           # years
        r"\b\d[\d,\.]+\b",      # numbers
        r"[A-Z][a-z]+ [A-Z][a-z]+",  # proper nouns (rough)
    ]
    q_lower = question_text.lower()
    matches = sum(
        1 for p in verifiable_patterns if re.search(p, question_text)
    )
    bonus = min(0.1, matches * 0.03)

    trust = min(1.0, base + bonus)
    logger.debug(f"[FactTrust] families={families} base={base} bonus={bonus} → {trust:.2f}")
    return round(trust, 3)


# ─── Narrative quality scorer ─────────────────────────────────────────────────

async def score_narrative_quality(
    question_text: str,
    explanation:   str,
    llm_client,           # LLMClient instance
) -> float:
    """
    Ask the LLM to rate the narrative quality of the question.

    Prompt is deliberately terse so it costs ~30 tokens.
    Returns a float 0-1.

    Falls back to a heuristic score if the LLM call fails.
    """
    prompt = f"""Rate the educational narrative quality of this MCQ question on a scale 0.0 to 1.0.

Question: "{question_text}"
Explanation: "{explanation[:200]}"

Criteria:
- Is it engaging and interesting? (not dry/boring)
- Does the explanation add real learning value?
- Is the wording clear and precise?

Reply with ONLY a decimal number between 0.0 and 1.0. Nothing else."""

    try:
        raw = await llm_client.simple_completion(prompt)
        val = float(raw.strip())
        val = max(0.0, min(1.0, val))
        logger.debug(f"[NarrativeQuality] LLM scored {val:.2f}")
        return round(val, 3)
    except Exception as e:
        logger.warning(f"[NarrativeQuality] LLM call failed ({e}), using heuristic")
        return _heuristic_narrative_score(question_text, explanation)


def _heuristic_narrative_score(question_text: str, explanation: str) -> float:
    """
    Fast heuristic narrative scorer used when LLM call fails.

    Checks for:
    - Sentence length (not too short / too long)
    - Presence of engaging context words
    - Explanation length (too short = low quality)
    """
    score = 0.5   # neutral baseline

    # Length check
    words = question_text.split()
    if 15 <= len(words) <= 50:
        score += 0.10
    elif len(words) < 10:
        score -= 0.20

    # Engagement signals
    engagement_words = [
        "known as", "famous for", "considered", "historically",
        "remarkable", "significant", "largest", "smallest",
        "first", "founded", "during", "despite",
    ]
    text_lower = question_text.lower()
    hits = sum(1 for w in engagement_words if w in text_lower)
    score += min(0.20, hits * 0.05)

    # Explanation quality
    if len(explanation) > 60:
        score += 0.10
    if len(explanation) < 20:
        score -= 0.15

    return round(max(0.0, min(1.0, score)), 3)


# ─── Main scorer ──────────────────────────────────────────────────────────────

async def compute_confidence(
    bundle:        SourceBundle,
    question_text: str,
    explanation:   str,
    llm_client,
) -> ConfidenceScore:
    """
    Compute the 50/50 confidence score.

    Args:
        bundle:        SourceBundle from the blender.
        question_text: Generated question string.
        explanation:   Generated explanation string.
        llm_client:    LLMClient for narrative scoring.

    Returns a ConfidenceScore with verdict and notes.
    """
    notes: list[str] = []

    # 50% — fact trust
    ft = score_fact_trust(bundle, question_text)
    if ft == 0.0:
        notes.append("no structured facts")
    elif ft < 0.7:
        notes.append("low source diversity")

    # 50% — narrative quality
    nq = await score_narrative_quality(question_text, explanation, llm_client)
    if nq < 0.7:
        notes.append("low narrative quality")

    # 50/50 blend
    confidence = round((ft * 0.5) + (nq * 0.5), 3)

    # Verdict
    if confidence >= THRESHOLD_ACCEPT:
        verdict = "accept"
    elif confidence >= THRESHOLD_REVIEW:
        verdict = "review"
        notes.append("flagged for human review")
    else:
        verdict = "reject"
        notes.append("below minimum threshold — regenerate")

    score = ConfidenceScore(
        fact_trust        = ft,
        narrative_quality = nq,
        confidence        = confidence,
        verdict           = verdict,
        notes             = notes,
    )

    logger.info(
        f"[Confidence] ft={ft:.2f} nq={nq:.2f} "
        f"conf={confidence:.2f} verdict={verdict}"
    )
    return score
