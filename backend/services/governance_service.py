"""services/governance_service.py

Minimal governance layer to enforce topic/keyword blocks and track decisions.

This is feature-flagged via config.ENABLE_TRUSTWORTHY_GENERATION.

Covers:
    - Candidate and bank-row governance checks
    - Rule matching against normalized payload blobs
    - Persisted governance flags on question rows
    - Best-effort audit logging for decision transparency
"""

from __future__ import annotations

import json
import uuid
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import ENABLE_TRUSTWORTHY_GENERATION
from database.governance_models import GovernanceBlockRule, QuestionAudit
from database.models import QuestionBank


logger = logging.getLogger(__name__)


# ─── Sensitive-topic blocklist ────────────────────────────────────────────────
# TODO: Load sensitive keywords dynamically from a database table to avoid hardcoding and redeploys.
SENSITIVE_KEYWORDS: list[str] = [
    "politics", "election", "president", "religion", "terrorism",
    "abortion", "lgbt_controversy", "immigration_policy",
    "race_issues", "war_crimes", "genocide", "ethnic_cleansing",
    "suicide", "self_harm", "drug_abuse",
]

SAFE_REDIRECT_TOPICS: list[str] = [
    "Geography - World Capitals",
    "History - Ancient Civilizations",
    "History - World War II",
    "Geography - Natural Wonders",
]

import random

def is_sensitive(text: str, topic: str = "") -> tuple[bool, list[str]]:
    combined = (text + " " + topic).lower()
    matched = [kw for kw in SENSITIVE_KEYWORDS if kw.replace("_", " ") in combined]
    return bool(matched), matched

def get_safe_redirect(original_topic: str) -> str:
    if "history" in original_topic.lower():
        return "History - Ancient Civilizations"
    if "geography" in original_topic.lower():
        return "Geography - World Capitals"
    return random.choice(SAFE_REDIRECT_TOPICS)

# WARNING: This set is in-memory only. Topics blocked at runtime are not persisted
# to the database and will be reset upon server restart. They are also not shared
# across multiple worker processes (e.g. gunicorn/uvicorn workers).
_runtime_blocked_topics: set[str] = set()

def block_topic_runtime(topic: str) -> None:
    _runtime_blocked_topics.add(topic.lower().strip())
    logger.warning(f"[Governance] Topic runtime-blocked: {topic!r}")

def is_topic_blocked(topic: str) -> bool:
    return topic.lower().strip() in _runtime_blocked_topics

async def log_audit_enhanced(
    db: AsyncSession,
    *,
    question_id: str,
    topic: str,
    confidence: float,
    sources: list[str],
    safe: bool,
    approved: bool,
) -> None:
    try:
        if isinstance(question_id, uuid.UUID):
            qid = question_id
        elif question_id:
            qid = uuid.UUID(str(question_id))
        else:
            qid = None
    except ValueError:
        logger.warning(f"Invalid question_id for audit: {question_id!r}")
        qid = None
    
    reasons = []
    if not safe:
        reasons.append("sensitive_content")
    if not approved and safe:
        reasons.append("low_confidence")

    from database.governance_models import QuestionAudit
    import json
    row = QuestionAudit(
        id=uuid.uuid4(),
        question_id=qid,
        topic=topic[:80] if topic else None,
        action="persist",
        approved=approved,
        reasons_json=json.dumps({"reasons": reasons, "safe": safe, "source": "enhanced_blended"}),
        confidence=confidence,
        sources_json=json.dumps(sources),
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except Exception:
        pass


@dataclass

class GovernanceDecision:
    approved: bool
    safe: bool
    reasons: list[str]
    confidence: Optional[float] = None
    fact_trust: Optional[float] = None
    narrative_quality: Optional[float] = None
    sources: Optional[list[dict[str, Any]]] = None

    # Serialize decision reasons for storage on audit/question rows.
    def flags_json(self) -> str:
        return json.dumps({"reasons": self.reasons}, ensure_ascii=False)

    # Serialize source metadata when available.
    def sources_json(self) -> Optional[str]:
        if self.sources is None:
            return None
        return json.dumps(self.sources, ensure_ascii=False)


class GovernanceService:
    """Central governance checks for generated and cached questions."""

    @staticmethod
    # Read feature flag to decide whether governance checks are active.
    def enabled() -> bool:
        return bool(ENABLE_TRUSTWORTHY_GENERATION)

    @staticmethod
    # Fetch currently active governance block rules.
    async def _active_block_rules(db: AsyncSession) -> list[GovernanceBlockRule]:
        result = await db.execute(
            select(GovernanceBlockRule).where(GovernanceBlockRule.is_active == True)  # noqa: E712
        )
        return list(result.scalars().all())

    @staticmethod
    # Normalize whitespace in arbitrary text inputs.
    def _normalize(value: str) -> str:
        return " ".join((value or "").strip().split())

    @staticmethod
    # Build one searchable text blob from all payload components.
    def _build_blob(
        *,
        topic: str,
        question_text: str,
        correct_answer: str,
        explanation: str,
        options: list[str],
    ) -> str:
        parts = [topic, question_text, explanation, correct_answer]
        parts.extend(options or [])
        return "\n".join([p for p in (GovernanceService._normalize(x) for x in parts) if p])

    @staticmethod
    # Check whether a block rule pattern matches the payload blob.
    def _match_rule(rule: GovernanceBlockRule, *, blob: str) -> bool:
        pattern = GovernanceService._normalize(str(getattr(rule, "pattern", "")))
        if not pattern:
            return False
        return pattern.lower() in blob.lower()

    @staticmethod
    # Evaluate one generated candidate before persistence/serving.
    async def evaluate_candidate(
        db: AsyncSession,
        *,
        question_id: str | uuid.UUID | None,
        room: str,
        action: str,
        topic: str,
        question_text: str,
        correct_answer: str,
        explanation: str,
        options: list[str],
        sources: Optional[list[dict[str, Any]]] = None,
        user_id: str | uuid.UUID | None = None,
    ) -> GovernanceDecision:
        """Evaluate a generated payload prior to persistence (or for audit only)."""
        if not GovernanceService.enabled():
            return GovernanceDecision(approved=True, safe=True, reasons=[])

        normalized_text = GovernanceService._normalize(question_text)
        normalized_correct = GovernanceService._normalize(correct_answer)
        normalized_options = [GovernanceService._normalize(str(o)) for o in (options or [])]

        reasons: list[str] = []

        # For hint evaluations, do not penalize short hint texts — hints are short by design.
        if action != "hint":
            if not normalized_text or len(normalized_text) < 12:
                reasons.append("question_text_too_short")
        if len(normalized_text) > 600:
            reasons.append("question_text_too_long")
        # For hints, we do not require a stored correct answer or options to be present.
        if action != "hint":
            if not normalized_correct:
                reasons.append("missing_correct_answer")
            if len([o for o in normalized_options if o]) < 2:
                reasons.append("insufficient_options")

        # For hints, evaluate against a minimal blob (topic + hint text) to avoid
        # false positives caused by absent correct_answer or option strings.
        if action == "hint":
            blob = "\n".join([p for p in (GovernanceService._normalize(x) for x in [topic, question_text]) if p])
        else:
            blob = GovernanceService._build_blob(
                topic=topic,
                question_text=normalized_text,
                correct_answer=normalized_correct,
                explanation=explanation,
                options=normalized_options,
            )

        for rule in await GovernanceService._active_block_rules(db):
            if GovernanceService._match_rule(rule, blob=blob):
                kind = GovernanceService._normalize(str(getattr(rule, "kind", ""))) or "rule"
                reasons.append(f"blocked:{kind}:{GovernanceService._normalize(rule.pattern)}")
                break

        approved = len(reasons) == 0
        # Lightweight heuristic scores (placeholders for future richer scoring).
        confidence = 0.85 if approved else 0.15
        fact_trust = 0.8 if approved else 0.2
        narrative_quality = 0.75 if approved else 0.25

        decision = GovernanceDecision(
            approved=approved,
            safe=approved,
            reasons=reasons,
            confidence=confidence,
            fact_trust=fact_trust,
            narrative_quality=narrative_quality,
            sources=sources,
        )

        await GovernanceService._log_audit(
            db,
            question_id=question_id,
            room=room,
            action=action,
            topic=topic,
            decision=decision,
            payload={
                "question_text": normalized_text,
                "correct_answer": normalized_correct,
                "options": normalized_options,
                "explanation": explanation,
            },
            user_id=user_id,
        )

        return decision

    @staticmethod
    # Re-evaluate an already persisted question row before serving.
    async def evaluate_bank_row_for_serving(
        db: AsyncSession,
        *,
        row: QuestionBank,
        room: str,
        topic: str,
    ) -> GovernanceDecision:
        """Re-check an existing QuestionBank row just before serving."""
        if not GovernanceService.enabled():
            return GovernanceDecision(approved=True, safe=True, reasons=[])

        reasons: list[str] = []

        if getattr(row, "gov_approved", True) is False:
            reasons.append("bank_row_not_approved")
        if getattr(row, "gov_safe", True) is False:
            reasons.append("bank_row_not_safe")

        options = []
        try:
            options = json.loads(row.options_json or "[]")
        except Exception:
            logger.warning(
                "Governance options_json parse failed for question_id=%s",
                str(getattr(row, "id", ""))[:8],
            )
            options = []

        blob = GovernanceService._build_blob(
            topic=topic,
            question_text=row.question_text or "",
            correct_answer=row.correct_answer or "",
            explanation=row.explanation or "",
            options=[str(o) for o in options if str(o).strip()],
        )

        for rule in await GovernanceService._active_block_rules(db):
            if GovernanceService._match_rule(rule, blob=blob):
                kind = GovernanceService._normalize(str(getattr(rule, "kind", ""))) or "rule"
                reasons.append(f"blocked:{kind}:{GovernanceService._normalize(rule.pattern)}")
                break

        approved = len(reasons) == 0
        decision = GovernanceDecision(
            approved=approved,
            safe=approved,
            reasons=reasons,
            confidence=float(getattr(row, "gov_confidence", 0.85) or 0.85) if approved else 0.15,
            fact_trust=float(getattr(row, "gov_fact_trust", 0.8) or 0.8) if approved else 0.2,
            narrative_quality=float(getattr(row, "gov_narrative_quality", 0.75) or 0.75) if approved else 0.25,
            sources=None,
        )

        await GovernanceService._log_audit(
            db,
            question_id=getattr(row, "id", None),
            room=room,
            action="serve",
            topic=topic,
            decision=decision,
            payload={
                "question_text": row.question_text,
                "correct_answer": row.correct_answer,
                "options": options,
                "explanation": row.explanation,
            },
        )

        if not approved:
            # Persist the rejection so we don't keep selecting this row.
            prior_values = {
                "gov_approved": getattr(row, "gov_approved", None),
                "gov_safe": getattr(row, "gov_safe", None),
                "gov_confidence": getattr(row, "gov_confidence", None),
                "gov_fact_trust": getattr(row, "gov_fact_trust", None),
                "gov_narrative_quality": getattr(row, "gov_narrative_quality", None),
                "gov_flags_json": getattr(row, "gov_flags_json", None),
                "gov_checked_at": getattr(row, "gov_checked_at", None),
            }
            try:
                async with db.begin_nested():
                    row.gov_approved = False
                    row.gov_safe = False
                    row.gov_confidence = decision.confidence
                    row.gov_fact_trust = decision.fact_trust
                    row.gov_narrative_quality = decision.narrative_quality
                    row.gov_flags_json = decision.flags_json()
                    row.gov_checked_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    db.add(row)
                    await db.flush()
            except Exception:
                for key, value in prior_values.items():
                    setattr(row, key, value)
                # Best-effort only; serving code can continue.
                return decision

        return decision

    @staticmethod
    # Persist governance outputs onto a question_bank row.
    async def apply_decision_to_persisted_row(
        db: AsyncSession,
        *,
        row: QuestionBank,
        decision: GovernanceDecision,
    ) -> None:
        if not GovernanceService.enabled():
            return

        row.gov_approved = bool(decision.approved)
        row.gov_safe = bool(decision.safe)
        row.gov_confidence = decision.confidence
        row.gov_fact_trust = decision.fact_trust
        row.gov_narrative_quality = decision.narrative_quality
        row.gov_sources_json = decision.sources_json()
        row.gov_flags_json = decision.flags_json()
        row.gov_checked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add(row)

    @staticmethod
    # Write an audit event; failures are isolated from caller flow.
    async def _log_audit(
        db: AsyncSession,
        *,
        question_id: str | uuid.UUID | None,
        room: str,
        action: str,
        topic: str,
        decision: GovernanceDecision,
        payload: dict[str, Any],
        user_id: str | uuid.UUID | None = None,
    ) -> None:
        if not GovernanceService.enabled():
            return

        qid: Optional[uuid.UUID]
        if question_id is None:
            qid = None
        else:
            try:
                qid = uuid.UUID(str(question_id))
            except ValueError:
                qid = None

        uid: Optional[uuid.UUID] = None
        if user_id is not None:
            try:
                uid = uuid.UUID(str(user_id))
            except ValueError:
                uid = None

        row = QuestionAudit(
            id=uuid.uuid4(),
            question_id=qid,
            room=str(room or "")[:30] or None,
            topic=str(topic or "")[:80] or None,
            action=str(action or "")[:30] or "unknown",
            approved=bool(decision.approved),
            reasons_json=decision.flags_json(),
            confidence=decision.confidence,
            sources_json=decision.sources_json(),
            question_text=str(payload.get("question_text") or "") or None,
            correct_answer=str(payload.get("correct_answer") or "") or None,
            options_json=json.dumps(payload.get("options") or [], ensure_ascii=False),
            explanation=str(payload.get("explanation") or "") or None,
            user_id=uid,
        )
        # Best-effort audit logging: isolate flush so we never rollback caller state.
        try:
            async with db.begin_nested():
                db.add(row)
                await db.flush()
        except Exception:
            # Never fail the user flow due to audit logging.
            return

