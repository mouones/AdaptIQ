"""Regression tests for challenge question option counts by level."""

import pytest

from routers.challenge import _generate_challenge_question_llm, LEVEL_PROMPTS


class _MockLLM:
    def __init__(self, parsed: dict):
        self.parsed = parsed
        self.last_status_code = None

    async def _chat_completion(self, **_kwargs):
        return "{}"

    def _parse_json_response(self, _response):
        return self.parsed


@pytest.mark.asyncio
async def test_level_1_question_has_two_options():
    llm = _MockLLM({
        "text": "What is the capital of France?",
        "correct": "Paris",
        "wrong1": "Berlin",
        "wrong2": "",
        "wrong3": "",
        "explanation": "Paris is the capital.",
    })

    result = await _generate_challenge_question_llm(llm, topic="Geography", level=1)

    assert result is not None
    assert len(result["options"]) == 2
    assert result["is_free_text"] is False
    assert LEVEL_PROMPTS[1]["options_count"] == 2


@pytest.mark.asyncio
async def test_level_2_question_has_four_options():
    llm = _MockLLM({
        "text": "Which empire ruled much of Europe in 800 CE?",
        "correct": "Carolingian Empire",
        "wrong1": "Ottoman Empire",
        "wrong2": "Byzantine Empire",
        "wrong3": "Mughal Empire",
        "explanation": "Charlemagne founded the Carolingian Empire.",
    })

    result = await _generate_challenge_question_llm(llm, topic="History", level=2)

    assert result is not None
    assert len(result["options"]) == 4
    assert result["is_free_text"] is False
    assert LEVEL_PROMPTS[2]["options_count"] == 4


@pytest.mark.asyncio
async def test_level_1_pads_missing_wrong_option():
    llm = _MockLLM({
        "text": "Who wrote the Declaration of Independence?",
        "correct": "Thomas Jefferson",
        "wrong1": "",
        "wrong2": "",
        "wrong3": "",
        "explanation": "Jefferson drafted it.",
    })

    result = await _generate_challenge_question_llm(llm, topic="History", level=1)

    assert result is not None
    assert len(result["options"]) == 2
    assert "Thomas Jefferson" in result["options"]


def test_fallback_question_is_normalized_to_deranked_level_one_options():
    from routers.challenge import _normalize_challenge_options_for_level

    row_payload = {
        "id": "q1",
        "text": "Which European city served as the capital of the Roman Empire?",
        "correctAnswer": "Rome",
        "options": ["Rome", "Athens", "Paris", "Madrid"],
        "explanation": "Rome was the imperial capital.",
        "is_free_text": False,
    }

    normalized = _normalize_challenge_options_for_level(row_payload, 1)

    assert normalized["is_free_text"] is False
    assert len(normalized["options"]) == 2
    assert "Rome" in normalized["options"]


def test_fallback_question_is_normalized_to_level_five_free_text():
    from routers.challenge import _normalize_challenge_options_for_level

    normalized = _normalize_challenge_options_for_level(
        {
            "id": "q1",
            "text": "Which treaty ended the War of the Spanish Succession?",
            "correctAnswer": "Treaty of Utrecht",
            "options": ["Treaty of Utrecht", "Treaty of Paris", "Treaty of Versailles"],
        },
        5,
    )

    assert normalized["is_free_text"] is True
    assert normalized["options"] == []
