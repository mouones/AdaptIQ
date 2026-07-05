"""
services/question_generator_enhanced.py
Enhanced question generation pipeline.

Full flow:
  1. SourceBlender — fetch 40% structured + 40% Wikipedia + 20% HF
  2. LLM — generate blended MCQ from the SourceBundle
  3. ConfidenceScorer — compute 50/50 score
  4. Validator — length / narrative / structured / sensitive checks
  5. GovernanceService — audit log + sensitive block
  6. Return question with confidence metadata

On confidence < 0.6 or validation failure → regenerate (max 3×).
On 3× exhaustion → fall back to existing classic LLM path.

ALL existing room endpoints call this instead of the old
_generate_with_llm_direct() — but the signature is backward-compatible.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

import httpx

from services.source_blender import SourceBlender, SourceBundle
from services.confidence_scorer import compute_confidence, ConfidenceScore, THRESHOLD_ACCEPT, THRESHOLD_REVIEW
from services.question_validator import validate_question, RegenerationTracker
from services.governance_service import (
    is_sensitive,
    is_topic_blocked,
    get_safe_redirect,
    log_audit_enhanced,
)

logger = logging.getLogger(__name__)

# ─── Blended agent prompt (exact copy from spec) ─────────────────────────────

BLENDED_AGENT_PROMPT = """You are an expert educational MCQ generator with access to verified facts and rich narratives.

MISSION: Generate ONE PERFECT MCQ that blends verified facts with engaging storytelling.

MANDATORY BLENDING RULE:
- Take the structured facts as your TRUTH BACKBONE (do not hallucinate beyond them)
- Weave in the narrative context to make the question engaging
- Use the HF question pattern (if provided) as inspiration for phrasing
- Result: "In [narrative context with place/event], [HF-style pattern] [structured fact]?"

EXAMPLE BLEND:
  Fact: "Paris — capital of France, population 2.16M (2023)"
  Narrative: "Paris, the City of Light, has been Europe's cultural heart since..."
  Pattern: "What is the population of..."
  → FINAL Q: "Paris, often called the City of Light and capital of France, had approximately what population in 2023?"

BEAUTY AND QUALITY RULES:
- Sound like an engaging teacher, not a textbook. Every question must be interesting.
- The question MUST be a properly formatted interrogative sentence (starting with Who, What, Where, When, Why, How, or Which).
- DO NOT generate statement-like questions with a question mark at the end (e.g. "Tokyo is the capital?").
- NEVER include the correct answer in the question text.

STRICT JSON RESPONSE FORMAT (no markdown, no extra text):
{
  "text": "<beautiful blended question — 15-50 words>",
  "correct": "<single correct answer>",
  "wrong1": "<plausible wrong answer>",
  "wrong2": "<plausible wrong answer>",
  "wrong3": "<plausible wrong answer>",
  "explanation": "<1-2 sentences of genuine learning value>",
  "confidence": <0.0-1.0>,
  "fact_trust": <0.0-1.0>,
  "narrative": <0.0-1.0>,
  "topic_tag": "<Category-Subtopic>"
}

BLOCK RULE: If the topic contains sensitive content (politics, elections, religion,
terrorism, abortion, war crimes) → redirect to a safe geography/history fact instead."""


# ─── Blender singleton ────────────────────────────────────────────────────────

_blender = SourceBlender()


# ─── Enhanced generator ───────────────────────────────────────────────────────

async def generate_question_enhanced(
    topic:          str,
    difficulty:     int,
    llm_client,             # services.llm.LLMClient
    http_client:    httpx.AsyncClient,
    db_session=None,        # AsyncSession | None  (for audit logging)
    user_accuracy:  float = 0.5,
    # Pre-fetched from existing agentic pipeline (optional)
    wikidata_facts: Optional[list[str]] = None,
    wiki_narrative: Optional[str] = None,
    hf_question:    Optional[dict] = None,
) -> Optional[dict]:
    """
    Main entry point for enhanced question generation.

    Returns a question dict (QuestionOut-compatible) with extra fields:
      confidence, fact_trust, narrative_quality, sources, safe_flag, approved
    OR None if all 3 attempts fail (caller should use Wikipedia fallback).
    """
    # ── Hard block: topic in runtime blocklist ────────────────────────────────
    if is_topic_blocked(topic):
        redirect = get_safe_redirect(topic)
        logger.warning(f"[Enhanced] Topic {topic!r} blocked → redirecting to {redirect!r}")
        topic = redirect

    tracker = RegenerationTracker()

    while not tracker.exhausted:
        result = await _single_attempt(
            topic          = topic,
            difficulty     = difficulty,
            llm_client     = llm_client,
            http_client    = http_client,
            db_session     = db_session,
            wikidata_facts = wikidata_facts,
            wiki_narrative = wiki_narrative,
            hf_question    = hf_question,
            attempt_num    = tracker.attempts + 1,
        )

        if result is None:
            tracker.record_failure("LLM_FAILURE")
            logger.warning(f"[Enhanced] Attempt {tracker.attempts} → LLM returned None")
            continue

        validation = result.get("_validation")
        score      = result.get("_score")

        # Check validation
        if validation and not validation.passed:
            tracker.record_failure(validation.rejection_code or "VALIDATION_FAIL")
            logger.info(f"[Enhanced] Attempt {tracker.attempts} rejected: {validation.rejection_code}")
            continue

        # Check confidence threshold
        if score and score.confidence < THRESHOLD_REVIEW:
            tracker.record_failure("LOW_CONFIDENCE")
            logger.info(f"[Enhanced] Attempt {tracker.attempts} low confidence: {score.confidence:.2f}")
            continue

        # Strip internal fields before returning
        result.pop("_validation", None)
        result.pop("_score", None)
        result.pop("_bundle", None)

        logger.info(
            f"[Enhanced] ✅ Question accepted after {tracker.attempts + 1} attempt(s) "
            f"conf={result.get('confidence', 0):.2f} topic={topic!r}"
        )
        return result

    logger.warning(f"[Enhanced] ❌ All attempts exhausted ({tracker.summary}) — caller should fallback")
    return None


async def _single_attempt(
    topic:          str,
    difficulty:     int,
    llm_client,
    http_client:    httpx.AsyncClient,
    db_session,
    wikidata_facts: Optional[list[str]],
    wiki_narrative: Optional[str],
    hf_question:    Optional[dict],
    attempt_num:    int,
) -> Optional[dict]:
    """
    One generation attempt: blend → LLM → score → validate → audit.
    Returns a dict with internal _validation/_score/_bundle keys, or None.
    """
    # 1. Blend sources
    bundle: SourceBundle = await _blender.blend(
        topic          = topic,
        difficulty     = difficulty,
        http_client    = http_client,
        wikidata_facts = wikidata_facts,
        wiki_narrative = wiki_narrative,
        hf_question    = hf_question,
    )

    # 2. Build the blended context string
    context = _blender.build_blend_prompt(bundle)
    if not context.strip():
        # Absolutely no context — skip LLM call, waste of tokens
        logger.debug("[Enhanced] Empty context, skipping LLM call")
        return None

    # 3. Generate with blended agent prompt
    user_msg = f"""TOPIC: {topic}
DIFFICULTY: {difficulty}/5
ATTEMPT: {attempt_num}/3

{context}

Generate ONE perfect blended MCQ following all rules.
Return ONLY the JSON."""

    raw = await llm_client._chat_completion(
        system      = BLENDED_AGENT_PROMPT,
        user        = user_msg,
        temperature = 0.88,
        max_tokens  = 600,
    )
    if not raw:
        return None

    parsed = llm_client._parse_json_response(raw)
    if not parsed:
        return None

    # Validate required fields
    required = ["text", "correct", "wrong1", "explanation"]
    if not all(k in parsed for k in required):
        logger.debug(f"[Enhanced] Missing fields: {list(parsed.keys())}")
        return None

    # Build options + shuffle (same pattern as existing llm.py)
    import random
    correct = str(parsed["correct"]).strip()
    wrongs  = [
        str(parsed.get("wrong1", "")).strip(),
        str(parsed.get("wrong2", "")).strip(),
        str(parsed.get("wrong3", "")).strip(),
    ]
    wrongs = [w for w in wrongs if w]
    pads   = ["None of the above", "Cannot be determined", "All of the above"]
    while len(wrongs) < 3:
        wrongs.append(pads.pop(0))
    options = [correct] + wrongs[:3]
    # Dedup
    seen, unique_opts = set(), []
    for o in options:
        if o.lower() not in seen:
            seen.add(o.lower())
            unique_opts.append(o)
    while len(unique_opts) < 4:
        unique_opts.append(pads.pop(0) if pads else "Unknown")
    random.shuffle(unique_opts)

    question_text = str(parsed["text"]).strip()
    explanation   = str(parsed.get("explanation", "")).strip()

    # 4. Confidence scoring
    score: ConfidenceScore = await compute_confidence(
        bundle        = bundle,
        question_text = question_text,
        explanation   = explanation,
        llm_client    = llm_client,
    )

    # 5. Validate
    validation = validate_question(
        question_text     = question_text,
        options           = unique_opts,
        correct_answer    = correct,
        topic             = topic,
        narrative_quality = score.narrative_quality,
        bundle            = bundle,
    )

    # 6. Governance — sensitive content check
    is_blocked, sensitive_keywords = is_sensitive(question_text, topic)
    safe      = not is_blocked
    approved  = safe and (score.confidence >= THRESHOLD_ACCEPT)

    question_id = str(uuid.uuid4())

    # 7. Audit log (non-fatal if DB unavailable)
    if db_session is not None:
        try:
            await log_audit_enhanced(
                db_session,
                question_id = question_id,
                topic       = topic,
                confidence  = score.confidence,
                sources     = bundle.sources,
                safe        = safe,
                approved    = approved,
            )
        except Exception as e:
            logger.warning(f"[Audit] Log failed (non-fatal): {e}")

    if is_blocked:
        logger.warning(
            f"[Enhanced] Question blocked — sensitive keywords: {sensitive_keywords}"
        )
        validation_override = type("V", (), {"passed": False, "rejection_code": "SENSITIVE_CONTENT"})()
        return {
            "_validation": validation_override,
            "_score": score,
            "_bundle": bundle,
        }

    # Build the return dict (QuestionOut-compatible + extra governance fields)
    return {
        "id":               question_id,
        "text":             question_text,
        "options":          unique_opts,
        "correctAnswer":    correct,
        "explanation":      explanation,
        # Governance / quality fields
        "confidence":       score.confidence,
        "fact_trust":       score.fact_trust,
        "narrative_quality":score.narrative_quality,
        "sources":          bundle.sources,
        "safe_flag":        safe,
        "approved":         approved,
        # Internal (stripped before returning to caller)
        "_validation":      validation,
        "_score":           score,
        "_bundle":          bundle,
    }
