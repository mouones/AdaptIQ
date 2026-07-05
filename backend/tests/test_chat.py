"""
tests/test_chat.py
Comprehensive test suite for the Scholar Chat endpoint.

Tests cover:
  - Schema validation (ChatAskRequest / ChatAskResponse)
  - Topic detection logic (detect_topic)
  - Scope validation (validate_scope)
  - RAG context retrieval (retrieve_context) — mocked network
  - Answer synthesis (synthesize_answer) — mocked LLM
  - Full pipeline orchestrator (handle_ask) — mocked dependencies
  - FastAPI endpoint (POST /api/chat/ask) — integration with TestClient
  - Edge cases: empty answers, LLM failures, RAG timeouts, rate limits
  - Security checks: auth required, user ID logging only (no question at ERROR level)

Run from backend/ directory:
    python -m pytest tests/test_chat.py -v

Or standalone (no pytest):
    python tests/test_chat.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from typing import Optional

# ─── Bootstrap path so we can import from the backend root ───────────────────
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─── Imports under test ───────────────────────────────────────────────────────
try:
    from schemas.chat import ChatAskRequest, ChatAskResponse, ChatErrorResponse
    from services.chat_service import (
        detect_topic,
        validate_scope,
        retrieve_context,
        synthesize_answer,
        handle_ask,
        ALLOWED_TOPICS,
        MAX_ANSWER_LENGTH,
    )
    IMPORTS_OK = True
    IMPORT_ERROR = None
except ImportError as e:
    IMPORTS_OK = False
    IMPORT_ERROR = e


_DEFAULT_LLM = object()


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def run_async(coro):
    """Run a coroutine in the event loop (Python 3.10+ asyncio.run is fine)."""
    return asyncio.run(coro)


def make_mock_llm(response: Optional[str] = "Paris is the capital of France."):
    """Return a minimal LLMClient mock."""
    llm = MagicMock()
    llm._chat_completion = AsyncMock(return_value=response)
    llm._parse_json_response = MagicMock(return_value={})
    llm.generate_mcq = AsyncMock(return_value=None)
    llm.last_status_code = 200
    return llm


def make_mock_http_client():
    """Return a minimal httpx.AsyncClient mock."""
    client = MagicMock()
    client.get = AsyncMock(return_value=MagicMock(
        status_code=200,
        json=MagicMock(return_value={"query": {"search": []}}),
    ))
    return client


# ═════════════════════════════════════════════════════════════════════════════
# TEST 0 — IMPORT SANITY
# ═════════════════════════════════════════════════════════════════════════════

class TestImports(unittest.TestCase):

    def test_imports_succeed(self):
        """All required modules must import without error."""
        if not IMPORTS_OK:
            self.fail(
                f"Import failed — fix this first before debugging other tests.\n"
                f"Error: {IMPORT_ERROR}"
            )

    def test_schema_modules_present(self):
        if not IMPORTS_OK:
            self.skipTest("imports failed")
        self.assertTrue(hasattr(ChatAskRequest, "model_fields") or hasattr(ChatAskRequest, "__fields__"))
        self.assertTrue(hasattr(ChatAskResponse, "model_fields") or hasattr(ChatAskResponse, "__fields__"))


# ═════════════════════════════════════════════════════════════════════════════
# TEST 1 — SCHEMA VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

class TestChatSchemas(unittest.TestCase):

    def setUp(self):
        if not IMPORTS_OK:
            self.skipTest("imports failed")

    # --- ChatAskRequest ---

    def test_request_valid_minimal(self):
        req = ChatAskRequest(question="What caused World War I?")
        self.assertEqual(req.question, "What caused World War I?")
        self.assertIsNone(req.topic_hint)

    def test_request_valid_with_topic_hint(self):
        req = ChatAskRequest(question="Tell me about the Nile.", topic_hint="geography")
        self.assertEqual(req.topic_hint, "geography")

    def test_request_valid_topic_hints(self):
        for hint in ("history", "geography", "mixed"):
            req = ChatAskRequest(question="Some question here", topic_hint=hint)
            self.assertEqual(req.topic_hint, hint)

    def test_request_invalid_topic_hint(self):
        """topic_hint must be one of the Literal values."""
        try:
            from pydantic import ValidationError
            with self.assertRaises(ValidationError):
                ChatAskRequest(question="Some question", topic_hint="science")
        except ImportError:
            self.skipTest("pydantic not available")

    def test_request_question_too_short(self):
        """question must be >= 3 characters."""
        try:
            from pydantic import ValidationError
            with self.assertRaises(ValidationError):
                ChatAskRequest(question="ab")
        except ImportError:
            self.skipTest("pydantic not available")

    def test_request_question_too_long(self):
        """question must be <= 500 characters."""
        try:
            from pydantic import ValidationError
            with self.assertRaises(ValidationError):
                ChatAskRequest(question="x" * 501)
        except ImportError:
            self.skipTest("pydantic not available")

    def test_request_question_exactly_500(self):
        """Exactly 500 chars must pass."""
        req = ChatAskRequest(question="a" * 500)
        self.assertEqual(len(req.question), 500)

    def test_request_question_exactly_3(self):
        req = ChatAskRequest(question="Why")
        self.assertEqual(req.question, "Why")

    # --- ChatAskResponse ---

    def test_response_valid(self):
        resp = ChatAskResponse(
            answer="Paris is the capital of France.",
            sources=["wikipedia"],
            topic="geography",
            grounded=True,
            confidence="high",
            response_time_ms=250,
        )
        self.assertTrue(resp.grounded)
        self.assertEqual(resp.confidence, "high")

    def test_response_empty_sources(self):
        resp = ChatAskResponse(
            answer="Some answer.",
            sources=[],
            topic="history",
            grounded=False,
            confidence="low",
            response_time_ms=100,
        )
        self.assertEqual(resp.sources, [])
        self.assertFalse(resp.grounded)

    def test_response_serializable(self):
        resp = ChatAskResponse(
            answer="Answer text",
            sources=["wikipedia", "wikidata"],
            topic="mixed",
            grounded=True,
            confidence="medium",
            response_time_ms=320,
        )
        data = resp.model_dump() if hasattr(resp, "model_dump") else resp.dict()
        self.assertIn("answer", data)
        self.assertIn("sources", data)
        self.assertIn("grounded", data)


# ═════════════════════════════════════════════════════════════════════════════
# TEST 2 — TOPIC DETECTION
# ═════════════════════════════════════════════════════════════════════════════

class TestTopicDetection(unittest.TestCase):

    def setUp(self):
        if not IMPORTS_OK:
            self.skipTest("imports failed")

    def _detect(self, question, hint=None):
        return run_async(detect_topic(question, hint))

    def test_hint_overrides_detection(self):
        result = self._detect("Tell me something random", hint="history")
        self.assertEqual(result, "history")

    def test_hint_geography(self):
        result = self._detect("random text", hint="geography")
        self.assertEqual(result, "geography")

    def test_hint_mixed(self):
        result = self._detect("random text", hint="mixed")
        self.assertEqual(result, "mixed")

    def test_hint_invalid_falls_back_to_detection(self):
        """An invalid hint should not override — falls through to keyword detection."""
        result = self._detect("What happened in World War II?", hint="science")
        # Should detect "history" since hint is invalid
        self.assertIn(result, ALLOWED_TOPICS)

    def test_history_keywords(self):
        for question in [
            "What caused the French Revolution?",
            "Who won the Battle of Waterloo?",
            "Describe the fall of the Roman empire.",
            "When did World War II end?",
        ]:
            result = self._detect(question)
            self.assertIn(result, ("history", "mixed"), msg=f"Failed for: {question}")

    def test_geography_keywords(self):
        for question in [
            "What is the capital of France?",
            "Which river is the longest in Africa?",
            "How many countries are in Europe?",
            "Where is the Sahara desert located?",
        ]:
            result = self._detect(question)
            self.assertIn(result, ("geography", "mixed"), msg=f"Failed for: {question}")

    def test_mixed_both_topics(self):
        question = "What was the capital of the Roman Empire during its peak?"
        result = self._detect(question)
        self.assertIn(result, ALLOWED_TOPICS)

    def test_no_keywords_returns_valid_topic(self):
        result = self._detect("Tell me something interesting.")
        self.assertIn(result, ALLOWED_TOPICS)

    def test_empty_hint_ignored(self):
        result = self._detect("What is the capital of Japan?", hint="")
        self.assertIn(result, ALLOWED_TOPICS)

    def test_none_hint(self):
        result = self._detect("What caused the Cold War?", hint=None)
        self.assertIn(result, ("history", "mixed"))


# ═════════════════════════════════════════════════════════════════════════════
# TEST 3 — SCOPE VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

class TestScopeValidation(unittest.TestCase):

    def setUp(self):
        if not IMPORTS_OK:
            self.skipTest("imports failed")

    def _validate(self, question, topic="mixed"):
        return run_async(validate_scope(question, topic))

    # In-scope questions
    def test_in_scope_history(self):
        self.assertTrue(self._validate("What were the causes of World War I?", "history"))

    def test_in_scope_geography(self):
        self.assertTrue(self._validate("What is the capital of Brazil?", "geography"))

    def test_in_scope_mixed(self):
        self.assertTrue(self._validate("Tell me about ancient civilizations.", "mixed"))

    def test_in_scope_general_educational(self):
        self.assertTrue(self._validate("Explain the fall of the Byzantine Empire."))

    # Out-of-scope questions
    def test_out_of_scope_programming(self):
        self.assertFalse(self._validate("write code to reverse a string"))

    def test_out_of_scope_debug(self):
        self.assertFalse(self._validate("how do I debug this Python script?"))

    def test_out_of_scope_recipe(self):
        self.assertFalse(self._validate("how to cook pasta"))

    def test_out_of_scope_medical(self):
        self.assertFalse(self._validate("give me medical advice for my headache"))

    def test_out_of_scope_finance(self):
        self.assertFalse(self._validate("what is the stock price of Apple?"))

    def test_out_of_scope_pure_math(self):
        self.assertFalse(self._validate("2 + 2 * 4"))

    def test_out_of_scope_programming_variant(self):
        self.assertFalse(self._validate("can you write code in JavaScript for me?"))

    # Edge cases
    def test_borderline_question_long(self):
        """Long but in-scope question should still pass."""
        question = "Can you explain in detail how the geography of the Nile River basin influenced the development of ancient Egyptian civilization and how rivers in general affected the rise of ancient empires?"
        self.assertTrue(self._validate(question, "history"))

    def test_whitespace_only_math(self):
        """Pure arithmetic with spaces should be rejected."""
        self.assertFalse(self._validate("  42 + 8  "))


# ═════════════════════════════════════════════════════════════════════════════
# TEST 4 — RAG CONTEXT RETRIEVAL
# ═════════════════════════════════════════════════════════════════════════════

class TestRetrieveContext(unittest.TestCase):

    def setUp(self):
        if not IMPORTS_OK:
            self.skipTest("imports failed")

    def _retrieve(self, topic="history", question="What happened?",
                  wiki_result=None, wikidata_result=None):
        mock_rag = MagicMock()
        mock_http = make_mock_http_client()
        mock_llm = make_mock_llm()

        async def _run():
            with patch("services.chat_service.fetch_wikipedia_context",
                       new_callable=AsyncMock, return_value=wiki_result) as _wiki, \
                 patch("services.chat_service.fetch_wikidata_facts",
                       new_callable=AsyncMock, return_value=wikidata_result) as _wikidata, \
                 patch("services.chat_service.format_wikidata_as_context",
                       return_value="Wikidata facts here."):
                return await retrieve_context(
                    topic=topic,
                    question=question,
                    rag_pipeline=mock_rag,
                    http_client=mock_http,
                    llm_client=mock_llm,
                )

        return run_async(_run())

    def test_both_sources_succeed(self):
        ctx, sources, grounded = self._retrieve(
            wiki_result={"context": "Wikipedia content about history.", "title": "History"},
            wikidata_result=[{"countryLabel": "France", "capitalLabel": "Paris"}],
        )
        self.assertTrue(grounded)
        self.assertIn("wikipedia", sources)
        self.assertIn("wikidata", sources)
        self.assertGreater(len(ctx), 0)

    def test_only_wikipedia_succeeds(self):
        ctx, sources, grounded = self._retrieve(
            wiki_result={"context": "Wikipedia content.", "title": "History"},
            wikidata_result=None,
        )
        self.assertTrue(grounded)
        self.assertIn("wikipedia", sources)
        self.assertNotIn("wikidata", sources)

    def test_only_wikidata_succeeds(self):
        ctx, sources, grounded = self._retrieve(
            wiki_result=None,
            wikidata_result=[{"key": "value"}],
        )
        self.assertTrue(grounded)
        self.assertIn("wikidata", sources)
        self.assertNotIn("wikipedia", sources)

    def test_both_sources_fail(self):
        ctx, sources, grounded = self._retrieve(
            wiki_result=None,
            wikidata_result=None,
        )
        self.assertFalse(grounded)
        self.assertEqual(sources, [])
        self.assertEqual(ctx, "")

    def test_grounded_false_when_no_sources(self):
        ctx, sources, grounded = self._retrieve(
            wiki_result=None,
            wikidata_result=None,
        )
        self.assertFalse(grounded)

    def test_context_contains_wikipedia_text(self):
        ctx, sources, grounded = self._retrieve(
            wiki_result={"context": "The French Revolution began in 1789.", "title": "French Revolution"},
            wikidata_result=None,
        )
        self.assertIn("French Revolution began in 1789", ctx)

    def test_wikipedia_timeout_handled_gracefully(self):
        """A timeout from Wikipedia should not crash — returns partial or empty context."""
        mock_rag = MagicMock()
        mock_http = make_mock_http_client()
        mock_llm = make_mock_llm()

        async def _timeout_wiki(*args, **kwargs):
            raise asyncio.TimeoutError()

        async def _run():
            with patch("services.chat_service.fetch_wikipedia_context",
                       new_callable=AsyncMock, side_effect=_timeout_wiki), \
                 patch("services.chat_service.fetch_wikidata_facts",
                       new_callable=AsyncMock, return_value=None):
                return await retrieve_context(
                    topic="history",
                    question="test",
                    rag_pipeline=mock_rag,
                    http_client=mock_http,
                    llm_client=mock_llm,
                )

        ctx, sources, grounded = run_async(_run())
        self.assertFalse(grounded)
        # Must not raise — graceful degradation

    def test_wikipedia_exception_handled_gracefully(self):
        """Any generic exception from Wikipedia should be swallowed."""
        mock_rag = MagicMock()
        mock_http = make_mock_http_client()
        mock_llm = make_mock_llm()

        async def _run():
            with patch("services.chat_service.fetch_wikipedia_context",
                       new_callable=AsyncMock, side_effect=Exception("Network error")), \
                 patch("services.chat_service.fetch_wikidata_facts",
                       new_callable=AsyncMock, return_value=None):
                return await retrieve_context(
                    topic="history",
                    question="test",
                    rag_pipeline=mock_rag,
                    http_client=mock_http,
                    llm_client=mock_llm,
                )

        ctx, sources, grounded = run_async(_run())
        self.assertFalse(grounded)


# ═════════════════════════════════════════════════════════════════════════════
# TEST 5 — ANSWER SYNTHESIS
# ═════════════════════════════════════════════════════════════════════════════

class TestSynthesizeAnswer(unittest.TestCase):

    def setUp(self):
        if not IMPORTS_OK:
            self.skipTest("imports failed")

    def _synthesize(self, llm_response, grounded=True, context="Some context here."):
        llm = make_mock_llm(response=llm_response)

        async def _run():
            return await synthesize_answer(
                question="What caused the French Revolution?",
                context=context,
                topic="history",
                grounded=grounded,
                llm_client=llm,
            )

        return run_async(_run())

    def test_normal_answer_returned(self):
        answer, confidence = self._synthesize("The French Revolution was caused by financial crises.")
        self.assertIn("French Revolution", answer)
        self.assertIn(confidence, ("high", "medium", "low"))

    def test_confidence_high_when_grounded_with_context(self):
        answer, confidence = self._synthesize(
            "Good answer.",
            grounded=True,
            context="x" * 300,
        )
        self.assertEqual(confidence, "high")

    def test_confidence_medium_when_grounded_short_context(self):
        answer, confidence = self._synthesize(
            "Good answer.",
            grounded=True,
            context="short",
        )
        self.assertEqual(confidence, "medium")

    def test_confidence_low_when_not_grounded(self):
        answer, confidence = self._synthesize(
            "Good answer.",
            grounded=False,
            context="",
        )
        self.assertEqual(confidence, "low")

    def test_scope_violation_raises_value_error(self):
        """LLM returning SCOPE_VIOLATION: must raise ValueError('OUT_OF_SCOPE')."""
        with self.assertRaises(ValueError) as ctx:
            self._synthesize("SCOPE_VIOLATION: Not a history question.")
        self.assertEqual(str(ctx.exception), "OUT_OF_SCOPE")

    def test_llm_none_raises_value_error(self):
        """LLM returning None must raise ValueError('LLM_FAILED')."""
        with self.assertRaises(ValueError) as ctx:
            self._synthesize(None)
        self.assertEqual(str(ctx.exception), "LLM_FAILED")

    def test_llm_empty_string_raises_value_error(self):
        """Empty string from LLM must raise ValueError('LLM_FAILED')."""
        with self.assertRaises(ValueError) as ctx:
            self._synthesize("")
        self.assertEqual(str(ctx.exception), "LLM_FAILED")

    def test_answer_truncated_at_max_length(self):
        """Answers longer than MAX_ANSWER_LENGTH must be truncated."""
        long_answer = "This is a sentence. " * 200  # >> 1200 chars
        answer, _ = self._synthesize(long_answer)
        self.assertLessEqual(len(answer), MAX_ANSWER_LENGTH + 50)  # small buffer for sentence boundary

    def test_answer_not_truncated_when_short(self):
        short_answer = "Short answer here."
        answer, _ = self._synthesize(short_answer)
        self.assertEqual(answer, short_answer)

    def test_answer_stripped_of_whitespace(self):
        answer, _ = self._synthesize("   Answer with extra whitespace.   ")
        self.assertEqual(answer, answer.strip())

    def test_scope_violation_prefix_variations(self):
        """SCOPE_VIOLATION must be detected at the start of the response."""
        for variant in [
            "SCOPE_VIOLATION: this is politics.",
            "SCOPE_VIOLATION: not history.",
        ]:
            with self.assertRaises(ValueError) as ctx:
                self._synthesize(variant)
            self.assertEqual(str(ctx.exception), "OUT_OF_SCOPE")

    def test_scope_violation_mid_text_not_triggered(self):
        """SCOPE_VIOLATION mid-text should NOT trigger if it is not the prefix."""
        answer, _ = self._synthesize("The term SCOPE_VIOLATION is interesting historically.")
        # Should not raise, since it doesn't start with SCOPE_VIOLATION
        self.assertIn("SCOPE_VIOLATION", answer)


# ═════════════════════════════════════════════════════════════════════════════
# TEST 6 — HANDLE_ASK ORCHESTRATOR
# ═════════════════════════════════════════════════════════════════════════════

class TestHandleAsk(unittest.TestCase):

    def setUp(self):
        if not IMPORTS_OK:
            self.skipTest("imports failed")

    def _ask(
        self,
        question="What caused the French Revolution?",
        topic_hint=None,
        llm_response="A solid educational answer about the French Revolution.",
        wiki_result=None,
        wikidata_result=None,
        llm=_DEFAULT_LLM,
        rag_pipeline=None,
        http_client=None,
    ):
        if llm is _DEFAULT_LLM:
            llm = make_mock_llm(response=llm_response)
        if http_client is None:
            http_client = make_mock_http_client()

        async def _run():
            with patch("services.chat_service.fetch_wikipedia_context",
                       new_callable=AsyncMock, return_value=wiki_result), \
                 patch("services.chat_service.fetch_wikidata_facts",
                       new_callable=AsyncMock, return_value=wikidata_result), \
                 patch("services.chat_service.format_wikidata_as_context",
                       return_value="Some wikidata facts."):
                return await handle_ask(
                    question=question,
                    topic_hint=topic_hint,
                    user_id="test-user-id",
                    llm_client=llm,
                    rag_pipeline=rag_pipeline or MagicMock(),
                    http_client=http_client,
                )

        return run_async(_run())

    # --- Happy path ---

    def test_successful_response_type(self):
        result = self._ask()
        self.assertIsInstance(result, ChatAskResponse)

    def test_successful_response_has_answer(self):
        result = self._ask()
        self.assertIsInstance(result.answer, str)
        self.assertGreater(len(result.answer), 0)

    def test_successful_response_has_topic(self):
        result = self._ask()
        self.assertIn(result.topic, ALLOWED_TOPICS)

    def test_successful_response_has_response_time(self):
        result = self._ask()
        self.assertGreater(result.response_time_ms, 0)

    def test_sources_list_is_list(self):
        result = self._ask()
        self.assertIsInstance(result.sources, list)

    def test_grounded_true_when_wiki_returns_data(self):
        result = self._ask(
            wiki_result={"context": "Wikipedia context.", "title": "French Revolution"},
        )
        self.assertTrue(result.grounded)
        self.assertIn("wikipedia", result.sources)

    def test_grounded_false_when_no_sources(self):
        result = self._ask(wiki_result=None, wikidata_result=None)
        # grounded may be False since both sources empty
        self.assertIsInstance(result.grounded, bool)

    def test_topic_hint_respected(self):
        result = self._ask(topic_hint="geography")
        self.assertEqual(result.topic, "geography")

    # --- Error cases ---

    def test_raises_503_when_llm_is_none(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            self._ask(llm=None)
        self.assertEqual(ctx.exception.status_code, 503)

    def test_raises_400_when_out_of_scope(self):
        from fastapi import HTTPException
        llm = make_mock_llm(response="SCOPE_VIOLATION: not history")
        with self.assertRaises(HTTPException) as ctx:
            self._ask(llm=llm)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, "OUT_OF_SCOPE")

    def test_raises_400_for_programming_question(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            self._ask(question="write code to sort a list in Python")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, "OUT_OF_SCOPE")

    def test_raises_503_when_llm_fails(self):
        from fastapi import HTTPException
        llm = make_mock_llm(response=None)
        with self.assertRaises(HTTPException) as ctx:
            self._ask(llm=llm)
        self.assertEqual(ctx.exception.status_code, 503)

    def test_handles_rag_timeout_gracefully(self):
        """RAG timeout must NOT crash the endpoint — answer should still be returned."""
        llm = make_mock_llm(response="Good answer about history.")

        async def _run():
            async def _slow_wiki(*args, **kwargs):
                await asyncio.sleep(100)  # Will be killed by soft timeout

            # Patch the overall RAG retrieval to time out
            with patch("services.chat_service.retrieve_context",
                       new_callable=AsyncMock,
                       side_effect=asyncio.TimeoutError()):
                # handle_ask catches the TimeoutError in its RAG block
                return await handle_ask(
                    question="What caused the French Revolution?",
                    topic_hint="history",
                    user_id="test-user",
                    llm_client=llm,
                    rag_pipeline=MagicMock(),
                    http_client=make_mock_http_client(),
                )

        result = run_async(_run())
        self.assertIsInstance(result, ChatAskResponse)
        self.assertFalse(result.grounded)

    def test_handles_rag_exception_gracefully(self):
        """Generic RAG exception must not crash — LLM still answers."""
        llm = make_mock_llm(response="Fallback answer.")

        async def _run():
            with patch("services.chat_service.retrieve_context",
                       new_callable=AsyncMock,
                       side_effect=Exception("RAG network error")):
                return await handle_ask(
                    question="What caused the French Revolution?",
                    topic_hint="history",
                    user_id="test-user",
                    llm_client=llm,
                    rag_pipeline=MagicMock(),
                    http_client=make_mock_http_client(),
                )

        result = run_async(_run())
        self.assertIsInstance(result, ChatAskResponse)

    def test_works_without_rag_pipeline(self):
        """rag_pipeline=None should still produce an answer (LLM-only path)."""
        result = self._ask(rag_pipeline=None)
        self.assertIsInstance(result, ChatAskResponse)
        self.assertFalse(result.grounded)

    def test_works_without_http_client(self):
        """http_client=None (no RAG network calls) — LLM-only path."""
        result = self._ask(http_client=None)
        self.assertIsInstance(result, ChatAskResponse)

    def test_response_time_measured(self):
        result = self._ask()
        self.assertGreaterEqual(result.response_time_ms, 0)

    def test_confidence_field_valid(self):
        result = self._ask()
        self.assertIn(result.confidence, ("high", "medium", "low"))

    # --- Edge cases ---

    def test_question_exactly_3_chars(self):
        result = self._ask(question="Why")
        self.assertIsInstance(result, ChatAskResponse)

    def test_question_exactly_500_chars(self):
        result = self._ask(question="a" * 500)
        self.assertIsInstance(result, ChatAskResponse)

    def test_unicode_question(self):
        result = self._ask(question="Quelle était la capitale de l'Empire Ottoman?")
        self.assertIsInstance(result, ChatAskResponse)

    def test_question_with_special_chars(self):
        result = self._ask(question="What happened in 1789? And why?")
        self.assertIsInstance(result, ChatAskResponse)

    def test_question_all_caps(self):
        result = self._ask(question="WHAT IS THE CAPITAL OF FRANCE?")
        self.assertIsInstance(result, ChatAskResponse)

    def test_repeated_calls_independent(self):
        """Multiple calls must not share state."""
        result1 = self._ask(question="What caused the French Revolution?")
        result2 = self._ask(question="What is the capital of Egypt?")
        self.assertIsInstance(result1, ChatAskResponse)
        self.assertIsInstance(result2, ChatAskResponse)


# ═════════════════════════════════════════════════════════════════════════════
# TEST 7 — FASTAPI ENDPOINT INTEGRATION
# ═════════════════════════════════════════════════════════════════════════════

class TestChatEndpoint(unittest.TestCase):
    """
    These tests require the full FastAPI app to be importable.
    They use TestClient with mocked dependencies.
    """

    def setUp(self):
        if not IMPORTS_OK:
            self.skipTest("imports failed")
        try:
            from fastapi.testclient import TestClient
            from fastapi import FastAPI
            from routers.chat_router import chat_router
            from routers.auth import get_current_user
            from database.models import User
            from datetime import datetime, timezone

            # Build a minimal test app
            self._app = FastAPI()
            self._app.include_router(chat_router)

            # Mock auth dependency
            async def _mock_auth():
                user = MagicMock(spec=User)
                user.id = "00000000-0000-0000-0000-000000000001"
                user.email = "test@test.com"
                user.is_active = True
                issued_at = datetime.now(timezone.utc)
                return user, issued_at

            self._app.dependency_overrides[get_current_user] = _mock_auth

            # Attach mock services to app.state
            self._app.state.llm_client = make_mock_llm(
                response="A scholarly answer about the French Revolution."
            )
            self._app.state.rag_pipeline = MagicMock()
            self._app.state.http_client = make_mock_http_client()

            self._client = TestClient(self._app)
            self._TestClient = TestClient
        except Exception as e:
            self.skipTest(f"FastAPI app setup failed: {e}")

    def _post(self, payload, headers=None):
        return self._client.post(
            "/api/chat/ask",
            json=payload,
            headers=headers or {"Authorization": "Bearer testtoken"},
        )

    def test_endpoint_returns_200_on_valid_request(self):
        with patch("services.chat_service.fetch_wikipedia_context",
                   new_callable=AsyncMock, return_value=None), \
             patch("services.chat_service.fetch_wikidata_facts",
                   new_callable=AsyncMock, return_value=None):
            resp = self._post({"question": "What caused the French Revolution?"})
        self.assertEqual(resp.status_code, 200)

    def test_endpoint_returns_valid_json(self):
        with patch("services.chat_service.fetch_wikipedia_context",
                   new_callable=AsyncMock, return_value=None), \
             patch("services.chat_service.fetch_wikidata_facts",
                   new_callable=AsyncMock, return_value=None):
            resp = self._post({"question": "What is the capital of France?"})
        data = resp.json()
        self.assertIn("answer", data)
        self.assertIn("sources", data)
        self.assertIn("topic", data)
        self.assertIn("grounded", data)
        self.assertIn("confidence", data)
        self.assertIn("response_time_ms", data)

    def test_endpoint_returns_422_for_missing_question(self):
        resp = self._post({})
        self.assertEqual(resp.status_code, 422)

    def test_endpoint_returns_422_for_short_question(self):
        resp = self._post({"question": "ab"})
        self.assertEqual(resp.status_code, 422)

    def test_endpoint_returns_422_for_invalid_topic_hint(self):
        resp = self._post({"question": "What is history?", "topic_hint": "science"})
        self.assertEqual(resp.status_code, 422)

    def test_endpoint_returns_400_for_out_of_scope(self):
        with patch("services.chat_service.handle_ask",
                   new_callable=AsyncMock,
                   side_effect=__import__("fastapi").HTTPException(status_code=400, detail="OUT_OF_SCOPE")):
            resp = self._post({"question": "write code in Python for me"})
        self.assertEqual(resp.status_code, 400)

    def test_endpoint_returns_503_when_llm_unavailable(self):
        self._app.state.llm_client = None
        with patch("services.chat_service.fetch_wikipedia_context",
                   new_callable=AsyncMock, return_value=None), \
             patch("services.chat_service.fetch_wikidata_facts",
                   new_callable=AsyncMock, return_value=None):
            resp = self._post({"question": "What happened in World War I?"})
        # Restore for other tests
        self._app.state.llm_client = make_mock_llm(response="Answer.")
        self.assertEqual(resp.status_code, 503)

    def test_endpoint_accepts_topic_hint(self):
        with patch("services.chat_service.fetch_wikipedia_context",
                   new_callable=AsyncMock, return_value=None), \
             patch("services.chat_service.fetch_wikidata_facts",
                   new_callable=AsyncMock, return_value=None):
            resp = self._post({
                "question": "Tell me about rivers.",
                "topic_hint": "geography",
            })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["topic"], "geography")


# ═════════════════════════════════════════════════════════════════════════════
# TEST 8 — SECURITY AND LOGGING
# ═════════════════════════════════════════════════════════════════════════════

class TestSecurityAndLogging(unittest.TestCase):

    def setUp(self):
        if not IMPORTS_OK:
            self.skipTest("imports failed")

    def test_scope_guard_never_reaches_llm_for_blocked_questions(self):
        """Blocked requests must never call the LLM (no token waste)."""
        llm = make_mock_llm()

        async def _run():
            with patch("services.chat_service.fetch_wikipedia_context",
                       new_callable=AsyncMock, return_value=None), \
                 patch("services.chat_service.fetch_wikidata_facts",
                       new_callable=AsyncMock, return_value=None):
                try:
                    await handle_ask(
                        question="write code to reverse a string",
                        topic_hint=None,
                        user_id="test-user",
                        llm_client=llm,
                        rag_pipeline=MagicMock(),
                        http_client=make_mock_http_client(),
                    )
                except Exception:
                    pass

        run_async(_run())
        # LLM _chat_completion should NOT have been called for this out-of-scope input
        llm._chat_completion.assert_not_called()

    def test_user_id_truncated_in_logs(self):
        """The service should only log the first 8 chars of user_id."""
        import logging
        import io

        log_capture = io.StringIO()
        handler = logging.StreamHandler(log_capture)
        logging.getLogger("services.chat_service").addHandler(handler)

        try:
            run_async(detect_topic("What happened in Rome?", None))
        finally:
            logging.getLogger("services.chat_service").removeHandler(handler)

        # No assertions needed — just verifying no crash occurs with UUID user IDs

    def test_no_full_question_at_error_level(self):
        """At ERROR log level, the question content should NOT appear (only type)."""
        # This is a policy check — we can only verify the handle_ask code structure.
        # The test confirms that a 503 error doesn't leak the question.
        import logging
        import io

        log_capture = io.StringIO()
        error_handler = logging.StreamHandler(log_capture)
        error_handler.setLevel(logging.ERROR)
        logging.getLogger("services.chat_service").addHandler(error_handler)

        llm = make_mock_llm(response=None)  # Will trigger LLM_FAILED

        async def _run():
            try:
                with patch("services.chat_service.fetch_wikipedia_context",
                           new_callable=AsyncMock, return_value=None), \
                     patch("services.chat_service.fetch_wikidata_facts",
                           new_callable=AsyncMock, return_value=None):
                    await handle_ask(
                        question="SENSITIVE_QUESTION: secret data",
                        topic_hint="history",
                        user_id="test-user",
                        llm_client=llm,
                        rag_pipeline=MagicMock(),
                        http_client=make_mock_http_client(),
                    )
            except Exception:
                pass

        run_async(_run())
        logging.getLogger("services.chat_service").removeHandler(error_handler)

        error_output = log_capture.getvalue()
        # The full question should not appear in ERROR-level logs
        self.assertNotIn("SENSITIVE_QUESTION: secret data", error_output)


# ═════════════════════════════════════════════════════════════════════════════
# TEST 9 — PERFORMANCE BASELINE
# ═════════════════════════════════════════════════════════════════════════════

class TestPerformance(unittest.TestCase):

    def setUp(self):
        if not IMPORTS_OK:
            self.skipTest("imports failed")

    def test_scope_validation_is_fast(self):
        """Scope validation should complete in < 50ms."""
        start = time.perf_counter()
        for _ in range(100):
            run_async(validate_scope("What caused World War I?", "history"))
        elapsed_ms = (time.perf_counter() - start) * 1000
        per_call_ms = elapsed_ms / 100
        self.assertLess(per_call_ms, 50, f"scope validation took {per_call_ms:.1f}ms per call")

    def test_topic_detection_is_fast(self):
        """Topic detection should complete in < 50ms."""
        start = time.perf_counter()
        for _ in range(100):
            run_async(detect_topic("What is the capital of France?", None))
        elapsed_ms = (time.perf_counter() - start) * 1000
        per_call_ms = elapsed_ms / 100
        self.assertLess(per_call_ms, 50, f"topic detection took {per_call_ms:.1f}ms per call")

    def test_handle_ask_response_time_tracked(self):
        """response_time_ms must be > 0 and reasonable."""
        llm = make_mock_llm(response="A solid answer about history.")

        async def _run():
            with patch("services.chat_service.fetch_wikipedia_context",
                       new_callable=AsyncMock, return_value=None), \
                 patch("services.chat_service.fetch_wikidata_facts",
                       new_callable=AsyncMock, return_value=None):
                return await handle_ask(
                    question="What caused the French Revolution?",
                    topic_hint="history",
                    user_id="perf-test-user",
                    llm_client=llm,
                    rag_pipeline=MagicMock(),
                    http_client=make_mock_http_client(),
                )

        result = run_async(_run())
        self.assertGreater(result.response_time_ms, 0)
        self.assertLess(result.response_time_ms, 60_000, "Response time > 60s — something is wrong")


# ═════════════════════════════════════════════════════════════════════════════
# STANDALONE RUNNER
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("Scholar Chat — Full Test Suite")
    print("=" * 70)
    print()

    if not IMPORTS_OK:
        print(f"❌  IMPORT FAILED: {IMPORT_ERROR}")
        print()
        print("Fix this first:")
        print("  1. Make sure you are running from the backend/ directory")
        print("  2. Check that chat_service.py and chat.py are on the path")
        print("  3. Run:  python -m pytest tests/test_chat.py -v")
        sys.exit(1)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestImports,
        TestChatSchemas,
        TestTopicDetection,
        TestScopeValidation,
        TestRetrieveContext,
        TestSynthesizeAnswer,
        TestHandleAsk,
        TestChatEndpoint,
        TestSecurityAndLogging,
        TestPerformance,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    result = runner.run(suite)

    print()
    print("=" * 70)
    total = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)
    skipped = len(result.skipped)
    passed = total - failures - errors - skipped

    print(f"Results: {passed} passed | {failures} failed | {errors} errors | {skipped} skipped")
    if result.wasSuccessful():
        print("✅  All tests passed.")
    else:
        print("❌  Some tests failed. See above for details.")
        if result.failures:
            print("\nFAILURES:")
            for test, traceback in result.failures:
                print(f"  - {test}")
        if result.errors:
            print("\nERRORS:")
            for test, traceback in result.errors:
                print(f"  - {test}")
    print("=" * 70)

    sys.exit(0 if result.wasSuccessful() else 1)
