"""
rag/agentic.py — 3-Agent RAG pipeline.

AGENT 1 — ROUTER:
    Takes topic + difficulty + user_history → decides source weights
    and builds a targeted retrieval plan.

AGENT 2 — RETRIEVER:
    Executes the plan:  70% Wikipedia | 20% HF dataset | 10% Wikidata
    Cascades on failure (never returns empty-handed if any source works).

AGENT 3 — VALIDATOR:
    LLM self-check: "Is this question at difficulty {target}?"
    Regenerates once if the answer is NO.
"""

from __future__ import annotations
import asyncio
import random
import logging
import httpx
from typing import Optional

from rag.wikipedia import fetch_wikipedia_context, fetch_related_titles
from rag.wikidata import fetch_wikidata_facts, format_wikidata_as_context
from rag.hf_dataset import async_get_hf_question

logger = logging.getLogger(__name__)


# ── Agent 1: ROUTER ────────────────────────────────────────────────────────

class RouterAgent:
    """
    Analyses user history and topic to select RAG source weights.

    Rules:
    - History easy (diff 1-2):  Wikipedia "major battles" queries
    - Geography hard (diff 4-5): Wikidata "capitals pop<1M"
    - Mixed: 60% weak topic + 40% strong
    - Default: 70% Wikipedia | 20% HF | 10% Wikidata
    """

    def route(
        self,
        topic: str,
        difficulty: int,
        user_accuracy: float,
        weak_topic: Optional[str] = None,
    ) -> dict:
        """
        Returns source weights and retrieval strategy metadata.
        weights = {wikipedia: int, huggingface: int, wikidata: int} (sum=100)
        """
        weights = {"wikipedia": 70, "huggingface": 20, "wikidata": 10}

        # Hard Geography → lean on Wikidata structured facts
        if topic == "Geography" and difficulty >= 4:
            weights = {"wikipedia": 40, "huggingface": 20, "wikidata": 40}
            logger.info("Router: Geography hard → Wikidata boost")

        # Hard History → more Wikipedia for narrative context
        elif topic == "History" and difficulty >= 4:
            weights = {"wikipedia": 70, "huggingface": 10, "wikidata": 20}

        # Easy → lean on HF pre-validated pairs (most reliable recall Qs)
        elif difficulty <= 2:
            weights = {"wikipedia": 60, "huggingface": 35, "wikidata": 5}

        # Mixed + identified weak topic → bias toward weak topic
        elif topic == "Mixed" and weak_topic:
            # This is handled at a higher level; just note it in metadata
            pass

        # User struggling (low accuracy) → easier sources for confidence
        if user_accuracy < 0.4 and difficulty > 2:
            weights["huggingface"] = min(40, weights["huggingface"] + 15)
            weights["wikipedia"] = max(40, weights["wikipedia"] - 10)
            weights["wikidata"] = max(5, weights["wikidata"] - 5)

        return {
            "weights": weights,
            "topic": topic,
            "difficulty": difficulty,
            "strategy": self._describe_strategy(topic, difficulty, user_accuracy),
        }

    @staticmethod
    def _describe_strategy(topic: str, difficulty: int, accuracy: float) -> str:
        if difficulty <= 2:
            return "easy_recall"
        elif difficulty == 3:
            return "conceptual_connections"
        else:
            return "multi_hop_inference"


# ── Agent 2: RETRIEVER ────────────────────────────────────────────────────

class RetrieverAgent:
    """
    Executes the Router's plan using weighted random source selection.
    Cascades across sources on failure.
    """

    async def retrieve(
        self,
        plan: dict,
        client: httpx.AsyncClient,
    ) -> dict | None:
        """
        Returns a context bundle:
        {source, context_text, title, raw_hf_question (if HF source)}
        """
        weights = plan["weights"]
        topic   = plan["topic"]
        diff    = plan["difficulty"]

        # Build weighted source list
        source_order = self._weighted_order(weights)

        for source in source_order:
            result = await self._fetch_from(source, topic, diff, client)
            if result:
                result["source"] = source
                logger.info(f"Retriever: got context from {source}")
                return result

        logger.warning("Retriever: all sources failed")
        return None

    @staticmethod
    def _weighted_order(weights: dict) -> list[str]:
        """Convert weights dict → ordered list using weighted random draw."""
        total = sum(weights.values())
        roll = random.randint(1, total)
        cum = 0
        chosen = "wikipedia"
        for src, w in weights.items():
            cum += w
            if roll <= cum:
                chosen = src
                break
        # Remaining sources as fallbacks
        rest = [s for s in weights if s != chosen]
        random.shuffle(rest)
        return [chosen] + rest

    async def _fetch_from(
        self,
        source: str,
        topic: str,
        difficulty: int,
        client: httpx.AsyncClient,
    ) -> dict | None:
        if source == "wikipedia":
            ctx = await fetch_wikipedia_context(topic, difficulty, client)
            if ctx:
                return {"context_text": ctx["context"], "title": ctx["title"]}

        elif source == "huggingface":
            hf = await async_get_hf_question(topic, difficulty)
            if hf:
                return {
                    "context_text": hf.get("explanation", ""),
                    "title": "HF Dataset",
                    "raw_hf_question": hf,
                }

        elif source == "wikidata":
            facts = await fetch_wikidata_facts(topic, difficulty, client)
            if facts:
                ctx = format_wikidata_as_context(facts)
                return {"context_text": ctx, "title": "Wikidata Facts"}

        return None


# ── Agent 3: VALIDATOR ────────────────────────────────────────────────────

class ValidatorAgent:
    """
    Self-check: after LLM generates a question, send a lightweight
    validation prompt to confirm the difficulty matches the target.
    Regenerates once if it doesn't pass.
    """

    def build_validation_prompt(self, question: dict, target_difficulty: int) -> str:
        difficulty_descriptions = {
            1: "very easy direct recall (major capitals, famous battles)",
            2: "easy recall (well-known facts)",
            3: "medium (requires connecting two facts)",
            4: "hard (multi-hop reasoning, less-known facts)",
            5: "very hard (obscure, requires expert knowledge)",
        }
        desc = difficulty_descriptions.get(target_difficulty, "medium")
        return f"""You are a difficulty validator for educational MCQs.

Target difficulty: {target_difficulty}/5 ({desc})

Question to validate:
"{question.get('text', '')}"

Correct Answer: "{question.get('correctAnswer', '')}"

Does this question match difficulty {target_difficulty}/5?
Answer with ONLY: YES or NO"""

    def is_valid(self, validation_response: str, target_difficulty: int) -> bool:
        """Parse validator response."""
        text = validation_response.strip().upper()
        return text.startswith("YES")


# ── Orchestrator ──────────────────────────────────────────────────────────

class AgenticRAGPipeline:
    """
    Coordinates all 3 agents to produce a validated question.
    
    1. Router decides source weights from context
    2. Retriever fetches relevant facts from chosen source(s)
    3. LLM generates the MCQ from retrieved facts
    4. Validator checks difficulty alignment (1 regeneration allowed)
    """

    def __init__(self):
        self.router    = RouterAgent()
        self.retriever = RetrieverAgent()
        self.validator = ValidatorAgent()

    @staticmethod
    def _fallback_context(topic: str, difficulty: int, weak_topic: Optional[str] = None) -> str:
        focus = (weak_topic or topic or "general knowledge").strip()
        return (
            f"Create one original educational MCQ about {focus}. "
            f"Target difficulty level is {difficulty}/5. "
            "Use concrete factual anchors, avoid trivia ambiguity, and provide a concise explanation."
        )

    async def run(
        self,
        topic: str,
        difficulty: int,
        user_accuracy: float,
        llm_client,  # LLMClient instance
        http_client: httpx.AsyncClient,
        weak_topic: Optional[str] = None,
    ) -> dict | None:
        """
        Full pipeline. Returns a question dict matching QuestionOut fields,
        or None if all sources + LLM failed.
        """
        # Agent 1: Route
        plan = self.router.route(topic, difficulty, user_accuracy, weak_topic)
        logger.info(f"RAG plan: {plan['strategy']} | weights: {plan['weights']}")

        # Agent 2: Retrieve
        context_bundle = await self.retriever.retrieve(plan, http_client)
        if not context_bundle:
            logger.warning("AgenticRAG: no context retrieved; using generation fallback context")
            context_bundle = {
                "source": "fallback_context",
                "title": "Local Fallback Context",
                "context_text": self._fallback_context(topic, difficulty, weak_topic),
            }

        # Never serve HF dataset questions as-is; always generate a fresh MCQ
        # from retrieved context so runtime output is fully generation-based.
        if "raw_hf_question" in context_bundle and not context_bundle.get("context_text"):
            hf_q = context_bundle["raw_hf_question"]
            context_bundle["context_text"] = (
                f"Question context: {hf_q.get('question', '')}\n"
                f"Reference explanation: {hf_q.get('explanation', '')}"
            )

        # Agent 3: LLM generates + Validator checks
        question = await self._generate_with_validation(
            context_bundle, topic, difficulty, llm_client, plan["strategy"]
        )
        return question

    async def _generate_with_validation(
        self,
        context_bundle: dict,
        topic: str,
        difficulty: int,
        llm_client,
        strategy: str,
        max_retries: int = 2,
    ) -> dict | None:
        """Generate MCQ and validate difficulty. Retry once if failed."""
        context_text = context_bundle.get("context_text", "")
        source       = context_bundle.get("source", "wikipedia")

        for attempt in range(max_retries):
            question = await llm_client.generate_mcq(
                context=context_text,
                topic=topic,
                difficulty=difficulty,
                strategy=strategy,
            )
            if not question:
                continue

            # Validate difficulty (Agent 3)
            if attempt < max_retries - 1:   # skip on last attempt to avoid loops
                validation_prompt = self.validator.build_validation_prompt(
                    question, difficulty
                )
                try:
                    val_response = await llm_client.simple_completion(validation_prompt)
                    if not self.validator.is_valid(val_response, difficulty):
                        logger.info(f"Validator rejected question at attempt {attempt}, regenerating...")
                        # Bump difficulty slightly for regeneration
                        difficulty = max(1, min(5, difficulty))
                        continue
                except Exception as e:
                    logger.warning("Validator error (accepting question): %s", e)

            question["source"] = source
            return question

        return None
