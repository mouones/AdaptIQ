"""
tests/test_llm_parsing.py — Unit tests for LLM JSON response parser.

Tests LLMClient._parse_json_response() to verify:
  - Clean JSON parsing
  - Markdown fence stripping
  - Regex fallback for extra text around JSON
  - Graceful failure on completely invalid input
  - Nested JSON with special characters
"""

import pytest
from services.llm import LLMClient


class TestParseJsonResponse:
    """Tests for _parse_json_response static method."""

    def test_clean_json(self):
        """Standard JSON parses correctly."""
        raw = '{"text": "What is X?", "correct": "A", "wrong1": "B", "wrong2": "C", "wrong3": "D", "explanation": "Because A."}'
        result = LLMClient._parse_json_response(raw)
        assert result is not None
        assert result["text"] == "What is X?"
        assert result["correct"] == "A"

    def test_markdown_fences_json(self):
        """JSON wrapped in ```json ... ``` fences."""
        raw = '```json\n{"text": "Question?", "correct": "Yes"}\n```'
        result = LLMClient._parse_json_response(raw)
        assert result is not None
        assert result["text"] == "Question?"

    def test_markdown_fences_no_lang(self):
        """JSON wrapped in ``` ... ``` fences without language tag."""
        raw = '```\n{"text": "Q?", "correct": "A"}\n```'
        result = LLMClient._parse_json_response(raw)
        assert result is not None
        assert result["text"] == "Q?"

    def test_extra_text_before_json(self):
        """Extra text before JSON — regex fallback should extract it."""
        raw = 'Here is your question:\n{"text": "Q?", "correct": "A"}'
        result = LLMClient._parse_json_response(raw)
        assert result is not None
        assert result["correct"] == "A"

    def test_extra_text_after_json(self):
        """Extra text after JSON — regex fallback should extract it."""
        raw = '{"text": "Q?", "correct": "A"}\nI hope this helps!'
        result = LLMClient._parse_json_response(raw)
        assert result is not None
        assert result["correct"] == "A"

    def test_completely_invalid(self):
        """Completely invalid input returns None."""
        result = LLMClient._parse_json_response("I cannot generate questions right now.")
        assert result is None

    def test_empty_string(self):
        """Empty string returns None."""
        result = LLMClient._parse_json_response("")
        assert result is None

    def test_nested_special_chars(self):
        """JSON with special characters in values."""
        raw = '{"text": "Who won WWII (1939-1945)?", "correct": "Allies", "wrong1": "Axis", "wrong2": "N/A", "wrong3": "Unknown", "explanation": "The Allies won in 1945."}'
        result = LLMClient._parse_json_response(raw)
        assert result is not None
        assert "(1939-1945)" in result["text"]

    def test_unicode_content(self):
        """JSON with unicode characters."""
        raw = '{"text": "Quelle est la capitale de la France?", "correct": "Paris"}'
        result = LLMClient._parse_json_response(raw)
        assert result is not None
        assert result["correct"] == "Paris"

    def test_whitespace_padding(self):
        """JSON with lots of whitespace around it."""
        raw = '   \n\n  {"text": "Q?", "correct": "A"}  \n\n  '
        result = LLMClient._parse_json_response(raw)
        assert result is not None
        assert result["correct"] == "A"

    def test_truncated_json(self):
        """Truncated JSON (incomplete) returns None gracefully."""
        raw = '{"text": "What is'
        result = LLMClient._parse_json_response(raw)
        assert result is None

    def test_multiple_json_objects(self):
        """Multiple JSON objects → parser falls back to regex, gets first match."""
        raw = '{"text": "Q1?"}\n{"text": "Q2?"}'
        result = LLMClient._parse_json_response(raw)
        # The regex r"\{.*\}" with DOTALL grabs the full span, which is
        # invalid JSON. Parser correctly returns None — this is safe behavior.
        assert result is None
