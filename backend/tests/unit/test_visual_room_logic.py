"""Regression tests for test visual room logic behavior."""

from datetime import datetime
import uuid

import pytest
from services.visual_room_service import (
    LEVEL_OPTIONS_COUNT, SHAPE_PROBABILITY,
    _pick_preferred_visual_candidate,
    should_show_shape, visual_question_needs_generation
)
from database.visual_models import VisualQuestion
from routers.visual_room import _close_visual_session_if_needed
from database.visual_models import VisualSession

def test_visual_level_options_count():
    assert LEVEL_OPTIONS_COUNT[1] == 2
    assert LEVEL_OPTIONS_COUNT[2] == 4
    assert LEVEL_OPTIONS_COUNT[5] == 0  # Text input

def test_should_show_shape():
    # History never has shapes
    assert not should_show_shape(4, "history", True)
    
    # Geography with no shape never has shape
    assert not should_show_shape(4, "geography", False)
    
    # Level 1 geography never has shape
    assert not should_show_shape(1, "geography", True)

def test_visual_question_needs_generation():
    # MCQ missing correct answer
    q1 = VisualQuestion(question_text="Q?", correct_answer=None, options_json="[]", question_type='M')
    assert visual_question_needs_generation(q1, 2) is True
    
    # Text input (L5) missing correct answer
    q2 = VisualQuestion(question_text="Q?", correct_answer=None, options_json="[]", question_type='T')
    assert visual_question_needs_generation(q2, 5) is True
    
    # Correct
    q3 = VisualQuestion(question_text="Q?", correct_answer="A", options_json='["A", "B"]', question_type='M')
    assert visual_question_needs_generation(q3, 2) is False

def test_visual_question_placeholder_detection():
    from services.visual_room_service import looks_like_placeholder_options
    q = VisualQuestion(options_json='["Option A", "Option B", "Option C"]')
    assert looks_like_placeholder_options(q) is True
    
    q_good = VisualQuestion(options_json='["France", "Germany"]')
    assert looks_like_placeholder_options(q_good) is False


def test_pick_preferred_visual_candidate_prefers_ready_question():
    pending = VisualQuestion(
        id=uuid.uuid4(),
        topic="history",
        question_text=None,
        correct_answer=None,
        options_json=None,
        question_type='M',
    )
    ready = VisualQuestion(
        id=uuid.uuid4(),
        topic="history",
        question_text="Which empire is shown?",
        correct_answer="Roman Empire",
        options_json='["Roman Empire", "Ottoman Empire", "Mughal Empire", "Byzantine Empire"]',
        question_type='M',
    )

    picked = _pick_preferred_visual_candidate([pending, ready], 2, set())
    assert picked is ready


def test_close_visual_session_sets_completion_and_ended_at_once():
    session = VisualSession(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        topic="History",
        level=1,
        is_completed=False,
        ended_at=None,
    )

    assert _close_visual_session_if_needed(session) is True
    first_ended_at = session.ended_at

    assert session.is_completed is True
    assert first_ended_at is not None
    assert _close_visual_session_if_needed(session) is False
    assert session.ended_at == first_ended_at


def test_close_visual_session_preserves_existing_ended_at():
    ended_at = datetime(2026, 6, 4, 12, 0, 0)
    session = VisualSession(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        topic="History",
        level=1,
        is_completed=True,
        ended_at=ended_at,
    )

    assert _close_visual_session_if_needed(session) is False
    assert session.ended_at == ended_at
