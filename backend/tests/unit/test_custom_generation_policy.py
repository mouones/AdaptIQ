"""Regression tests for test custom generation policy behavior."""

import uuid

import routers.custom as custom_router
from routers.custom import (
    _broader_geography_keywords,
    _custom_concept_tracking_enabled,
    _custom_generation_rules,
    _custom_progress_total,
    _custom_simple_generation_payload,
    _generated_payload_matches_keywords,
    _normalize_generated_options,
    _recent_question_key,
    _simple_geography_scope_fallback_payload,
    _topic_keywords,
)


def test_geography_country_keywords_include_country_scope() -> None:
    primary = _topic_keywords("Geography - France")
    broader = _broader_geography_keywords("Geography - France")

    assert "france" in [k.lower() for k in primary]
    assert "europe" in [k.lower() for k in broader]


def test_recent_question_cache_key_is_concept_scoped() -> None:
    user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    key_a = _recent_question_key(
        user_id=user_id,
        topic_label="History - World War I",
        concept_id="concept-a",
    )
    key_b = _recent_question_key(
        user_id=user_id,
        topic_label="History - World War I",
        concept_id="concept-b",
    )

    assert key_a != key_b


def test_generation_rules_enforce_strict_geography_scope() -> None:
    rules = _custom_generation_rules(
        topic_label="Geography - United States",
        concept_name=None,
        generation_style="physical geography and landforms",
        recent_signatures=["capital-city trivia pattern"],
        allow_broader_geography_scope=False,
    )

    assert "strictly anchored to the selected country" in rules
    assert "recent angle:" in rules


def test_generated_payload_keyword_match() -> None:
    assert _generated_payload_matches_keywords(
        question_text="Which river flows through Paris?",
        explanation="The Seine is central to French urban geography.",
        options=["Seine", "Rhine", "Danube", "Po"],
        keywords=["france", "french", "paris"],
    )

    assert not _generated_payload_matches_keywords(
        question_text="Which river flows through Cairo?",
        explanation="The Nile is central to Egypt.",
        options=["Nile", "Congo", "Niger", "Limpopo"],
        keywords=["united states", "usa", "american"],
    )


def test_simple_mode_generation_payload_is_non_adaptive() -> None:
    payload = _custom_simple_generation_payload(
        topic_label="History - World War II",
        fact_content="A key fact",
    )

    assert payload["topic"] == "History - World War II"
    assert payload["difficulty"] == 3
    assert payload["strategy"] == "direct"
    assert payload["user_accuracy"] == 0.5


def test_simple_mode_disables_concept_tracking(monkeypatch) -> None:
    monkeypatch.setattr(custom_router, "CUSTOM_ROOM_SIMPLE_MODE", True)
    assert _custom_concept_tracking_enabled() is False

    monkeypatch.setattr(custom_router, "CUSTOM_ROOM_SIMPLE_MODE", False)
    assert _custom_concept_tracking_enabled() is True


def test_custom_progress_total_uses_session_fallback_for_empty_topics(monkeypatch) -> None:
    monkeypatch.setattr(custom_router, "CUSTOM_ROOM_PROGRESS_FALLBACK_TOTAL", 10)

    assert _custom_progress_total(total_in_db=0, catalogue_total=0) == 10
    assert _custom_progress_total(total_in_db=0, catalogue_total=25) == 25
    assert _custom_progress_total(total_in_db=12, catalogue_total=25) == 12


def test_simple_geography_fallback_payload_is_country_scoped() -> None:
    topic = "Geography - United States"
    payload = _simple_geography_scope_fallback_payload(topic)

    assert payload is not None
    assert "United States" in str(payload["text"])
    assert _generated_payload_matches_keywords(
        question_text=str(payload["text"]),
        explanation=str(payload["explanation"]),
        options=[str(v) for v in payload["options"]],
        keywords=_topic_keywords(topic),
    )


def test_normalize_generated_options_never_empty() -> None:
    """Adaptive fallback chain always produces at least four MCQ options."""
    options = _normalize_generated_options(["Alpha"], "Alpha")
    assert len(options) >= 4
    assert "Alpha" in options


def test_rag_fallback_template_has_minimum_options() -> None:
    """When RAG fails, geography template fallback still yields a playable question."""
    payload = _simple_geography_scope_fallback_payload("Geography - France")
    assert payload is not None
    opts = [str(v).strip() for v in payload.get("options", []) if str(v).strip()]
    assert payload.get("text")
    assert len(opts) >= 2
