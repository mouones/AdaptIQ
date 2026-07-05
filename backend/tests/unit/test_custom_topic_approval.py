"""Regression tests for test custom topic approval behavior."""

import uuid

import pytest
from pydantic import ValidationError

from database.custom_models import Fact
from routers.admin import (
    AdminCustomTopicApproveIn,
    _catalogue_topic_state,
    _difficulty_hint_from_question,
    _fact_content_from_question,
    _slugify_topic,
    _topic_family,
    _topic_label,
)


def test_custom_topic_approval_body_limits_max_facts() -> None:
    with pytest.raises(ValidationError):
        AdminCustomTopicApproveIn(type="History", name="World War I", max_facts=501)

    body = AdminCustomTopicApproveIn(type="History", name="World War I", max_facts=100)
    assert body.type == "History"
    assert body.name == "World War I"


def test_topic_helpers_build_stable_custom_room_labels() -> None:
    assert _slugify_topic("History - World War I!") == "history-world-war-i"
    assert _topic_label("History", "World War I") == "History - World War I"
    assert _topic_label("History", "History - World War I") == "History - World War I"
    assert _topic_family("History - World War I") == "history"
    assert _topic_family("Geography - France") == "geography"


def test_catalogue_topics_are_existing_custom_rooms_by_default() -> None:
    state = _catalogue_topic_state(
        "ww1",
        {"total_facts": 0},
        approved_info={},
    )

    assert state["approved"] is True
    assert state["is_active"] is True
    assert state["total_facts_count"] == 0


def test_catalogue_topic_state_honors_deactivation_override() -> None:
    state = _catalogue_topic_state(
        "ww1",
        {"total_facts": 0},
        approved_info={"ww1": {"is_active": False, "total_facts_count": 7}},
    )

    assert state["approved"] is True
    assert state["is_active"] is False
    assert state["total_facts_count"] == 7


def test_fact_model_has_source_question_provenance_column() -> None:
    column = Fact.__table__.c.source_question_id
    foreign_keys = {fk.column.table.name for fk in column.foreign_keys}

    assert column.nullable is True
    assert "question_bank" in foreign_keys


def test_fact_content_from_question_redacts_nothing_but_uses_safe_fallback() -> None:
    question_id = uuid.uuid4()

    class Question:
        id = question_id
        question_text = "When did World War I begin?"
        correct_answer = "1914"
        explanation = ""
        difficulty_irt = 4.0

    content = _fact_content_from_question(Question)

    assert "When did World War I begin?" in content
    assert "Correct answer: 1914" in content
    assert "The correct answer is 1914." in content
    assert _difficulty_hint_from_question(Question) == "hard"
