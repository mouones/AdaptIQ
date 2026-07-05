"""
services/llm.py — Groq Llama 3.1-8B-instant client.
Fixed:
  1. correctAnswer always first → now shuffled AFTER generation
  2. Hint reveals answer → stricter prompt that forbids naming the answer
  3. Repeated questions → higher temperature + explicit "do not repeat" instruction

Provides:
    - MCQ generation with strict JSON parsing and retry logic
    - Hint generation with anti-answer-leak safeguards
    - Rate-limit aware chat-completion wrapper
"""

import json
import re
import uuid
import random
import logging
import asyncio
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL   = "llama-3.1-8b-instant"

DIFFICULTY_INSTRUCTIONS = {
    1: "VERY EASY — single well-known fact. Major capitals, famous battles, common dates.",
    2: "EASY — known fact with minor context. Identify a country from its capital, cause of a war.",
    3: "MEDIUM — connect two facts. E.g., combining a historical figure with a specific event, or a cause with an effect.",
    4: "HARD — multi-hop reasoning, less-famous facts. Lesser-known treaties, small capitals.",
    5: "VERY HARD — expert-level, obscure. Pre-medieval events, smallest capitals by population.",
}

MCQ_SYSTEM_PROMPT = """You are an expert educational MCQ generator.
Return ONLY a valid JSON object — no markdown, no backticks, no extra text.

STRICT JSON structure:
{
  "text": "the question",
  "correct": "the single correct answer",
  "wrong1": "plausible wrong answer",
  "wrong2": "plausible wrong answer",
  "wrong3": "plausible wrong answer",
  "explanation": "1-2 sentences explaining WHY the correct answer is right, without restating the question"
}

RULES:
- QUESTION QUALITY: The question MUST be a properly formatted interrogative sentence (starting with Who, What, Where, When, Why, How, or Which).
- DO NOT generate statement-like questions with a question mark at the end (e.g. "Tokyo is the capital?").
- DO NOT include the answer in the question text.
- correct and wrong1/2/3 must all be different
- wrong answers must be plausible (same category as correct — e.g. all capitals, all dates)
- question text must be short: one sentence, at most 22 words
- explanation must NOT just repeat the question — add a new fact or context
- Return ONLY the JSON, nothing else"""


# Trim generated question text to a compact single-sentence form.
def _shorten_question_text(text: str, max_words: int = 22) -> str:
    """Keep generated questions concise and single-sentence for better UX."""
    cleaned = " ".join((text or "").strip().split())
    if not cleaned:
        return ""

    first_sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0]
    words = first_sentence.split()
    if len(words) <= max_words:
        return first_sentence
    shortened = " ".join(words[:max_words]).rstrip(" ,;:")
    if not shortened.endswith("?"):
        shortened += "?"
    return shortened


class LLMClient:

    # Initialize reusable async HTTP client and request state metadata.
    def __init__(self, api_key: str, timeout: float = 20.0):
        self.api_key = api_key
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)
        self.last_status_code: Optional[int] = None
        self.last_error_message: str = ""

    @staticmethod
    # Derive an appropriate retry wait from rate-limit headers.
    def _rate_limit_wait_seconds(resp: httpx.Response) -> float:
        retry_after = resp.headers.get("retry-after")
        for raw in [retry_after, resp.headers.get("x-ratelimit-reset-tokens"), resp.headers.get("x-ratelimit-reset-requests")]:
            if not raw:
                continue
            try:
                value = float(str(raw).strip())
                # Some providers return milliseconds in reset headers.
                if value > 100:
                    value = value / 1000.0
                return max(0.5, min(value, 5.0))
            except ValueError:
                continue
        return 1.5

    # Generate a single MCQ payload and normalize options/output format.
    async def generate_mcq(
        self,
        context: str,
        topic: str,
        difficulty: int,
        strategy: str = "easy_recall",
        user_accuracy: float = 0.5,
        extra_instructions: str = "",
    ) -> Optional[dict]:
        difficulty = max(1, min(5, difficulty))
        diff_instruction = DIFFICULTY_INSTRUCTIONS[difficulty]

        extra_rules = f"\nADDITIONAL RULES:\n{extra_instructions.strip()}\n" if extra_instructions.strip() else ""

        user_prompt = f"""TOPIC: {topic}
DIFFICULTY: {difficulty}/5 — {diff_instruction}

CONTEXT (base your question on this — do not hallucinate beyond it):
\"\"\"{context[:800]}\"\"\"

    STRATEGY: {strategy}
    {extra_rules}

Generate ONE unique MCQ. Make it different from common textbook questions.
Return ONLY the JSON."""

        try:
            # Higher temperature = more variety, fewer repeats
            response = await self._chat_completion(
                system=MCQ_SYSTEM_PROMPT,
                user=user_prompt,
                temperature=0.92,
                max_tokens=500,
            )
            if not response:
                return None

            parsed = self._parse_json_response(response)
            if not parsed:
                return None

            # Validate all required fields present
            required = ["text", "correct", "wrong1", "wrong2", "wrong3", "explanation"]
            if not all(k in parsed for k in required):
                logger.warning(f"LLM missing fields: {list(parsed.keys())}")
                return None

            # ── FIX 1: Build options and SHUFFLE so correct is never always first ──
            correct = str(parsed["correct"]).strip()
            options = [
                correct,
                str(parsed["wrong1"]).strip(),
                str(parsed["wrong2"]).strip(),
                str(parsed["wrong3"]).strip(),
            ]
            # Remove duplicates
            seen = set()
            unique_options = []
            for opt in options:
                if opt.lower() not in seen:
                    seen.add(opt.lower())
                    unique_options.append(opt)

            # Pad if duplicates removed
            pads = ["None of the above", "Cannot be determined", "All of the above", "Insufficient data"]
            while len(unique_options) < 4:
                unique_options.append(pads.pop(0))

            # SHUFFLE — correct answer ends up in random position
            random.shuffle(unique_options)

            return {
                "id": str(uuid.uuid4()),
                "text": _shorten_question_text(str(parsed["text"])),
                "options": unique_options,
                "correctAnswer": correct,   # still points to the right answer after shuffle
                "explanation": str(parsed["explanation"]).strip(),
            }

        except Exception as e:
            logger.error(f"LLM generate_mcq failed: {e}")
            return None

    # Generate a short hint while preventing explicit answer leakage.
    async def generate_hint(
        self,
        question_text: str,
        correct_answer: str,
    ) -> Optional[str]:
        # ── FIX 2: Strict hint prompt — must NOT reveal the answer ──
        prompt = f"""You are giving a hint for a quiz question. 

Question: "{question_text}"
Correct Answer (DO NOT reveal this): "{correct_answer}"

Write ONE short hint (max 20 words) that:
- Helps the student think in the right direction
- Does NOT say the answer or any part of it
- Points to a category, time period, or geographic region
- Is cryptic enough to still be a challenge

Examples of GOOD hints:
- "Think about events in the early 20th century in Eastern Europe."
- "This is a landlocked country in Central Asia."
- "Consider which empire dominated the Mediterranean before Rome."

Examples of BAD hints (too direct):
- "The answer involves Napoleon." (names the subject)
- "It starts with the letter F." (too obvious)

Return ONLY the hint text, nothing else."""

        try:
            response = await self._chat_completion(
                system="You are a quiz hint generator. Give short cryptic hints. Never reveal the answer. Max 20 words.",
                user=prompt,
                temperature=0.8,
                max_tokens=60,   # Force short response
            )
            if not response:
                return None
            hint = response.strip()
            # Safety: if hint contains the answer, return generic fallback
            if correct_answer.lower()[:8] in hint.lower():
                return "Think about the broader historical and geographical context of this topic."
            return hint
        except Exception as e:
            logger.error(f"LLM generate_hint failed: {e}")
            return None

    # Lightweight helper for short general completions.
    async def simple_completion(self, prompt: str) -> str:
        resp = await self._chat_completion(
            system="Answer concisely.",
            user=prompt,
            temperature=0.1,
            max_tokens=10,
        )
        return resp or ""

    # Execute chat completion with retries and rate-limit handling.
    async def _chat_completion(
        self,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: int = 500,
    ) -> Optional[str]:
        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": 0.95,
            "frequency_penalty": 0.4,   # Penalise repeated tokens → more variety
        }
        self.last_status_code = None
        self.last_error_message = ""

        for attempt in range(3):
            try:
                resp = await self._client.post(
                    GROQ_API_URL,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=self.timeout,
                )
                self.last_status_code = resp.status_code

                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"]

                self.last_error_message = resp.text[:300]
                if resp.status_code == 429 and attempt < 2:
                    wait_s = self._rate_limit_wait_seconds(resp) + random.uniform(0.0, 0.3)
                    logger.warning("Groq rate limited (attempt %s), retrying in %.2fs", attempt + 1, wait_s)
                    await asyncio.sleep(wait_s)
                    continue

                logger.error(f"Groq API error {resp.status_code}: {resp.text[:200]}")
                return None
            except (httpx.RequestError, KeyError) as e:
                logger.error(f"Groq request failed: {e}")
                self.last_status_code = None
                self.last_error_message = str(e)
                return None

        return None

    @staticmethod
    # Parse model output into JSON, including fenced/embedded fallbacks.
    def _parse_json_response(raw: str) -> Optional[dict]:
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
        logger.warning(f"Could not parse LLM JSON: {raw[:200]}")
        return None

    # Close the underlying shared HTTP client.
    async def close(self):
        await self._client.aclose()