"""Regression tests for test classic room theta contract behavior."""

import inspect
import uuid
from types import SimpleNamespace

import pytest

from routers import classic_room as classic_room_router


class _FakeSessionService:
    def __init__(self, *, user_id: uuid.UUID, session_id: uuid.UUID, question_id: uuid.UUID):
        self._session_id = str(session_id)
        self._session_state = {"user_id": str(user_id), "questions_asked": []}
        self._current_question = {
            "id": str(question_id),
            "correct_answer": "Paris",
            "shuffled_options": ["Rome", "Paris", "Madrid", "Lisbon"],
        }

    async def get_session_state(self, session_id: str):
        if session_id != self._session_id:
            return None
        return self._session_state

    async def get_current_question(self, session_id: str):
        if session_id != self._session_id:
            return None
        return self._current_question


_submit_impl = inspect.unwrap(classic_room_router.submit_answer)


@pytest.mark.asyncio
async def test_submit_answer_derives_theta_updated_from_theta_changes(monkeypatch):
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    question_id = uuid.uuid4()
    fake_session = _FakeSessionService(
        user_id=user_id,
        session_id=session_id,
        question_id=question_id,
    )
    observed: dict[str, int] = {}

    async def _fake_process_answer(
        _db,
        _user_id,
        _session_id,
        _question_id,
        selected_index,
        _time_taken,
        _session_service,
        _used_hint,
    ):
        observed["selected_index"] = int(selected_index)
        return {
            "correct": True,
            "explanation": "Paris is the capital of France.",
            "theta_changes": [
                {"theta_before": 0.2, "theta_after": 0.6},
                {"theta_before": -0.1, "theta_after": 0.1},
            ],
            "next_question": None,
            "session_stats": {"questions_answered": 1, "correct_count": 1, "is_finished": False},
        }

    monkeypatch.setattr(classic_room_router.ClassicService, "process_answer", _fake_process_answer)

    body = classic_room_router.SubmitAnswerRequest(
        session_id=session_id,
        question_id=question_id,
        selected_answer="Paris",
        time_taken=12,
        used_hint=False,
    )

    response = await _submit_impl(
        request=SimpleNamespace(),
        body=body,
        current_user_tuple=(SimpleNamespace(id=user_id), None),
        db=object(),
        session_svc=fake_session,
    )

    assert response.success is True
    assert response.is_correct is True
    assert observed["selected_index"] == 1
    assert response.theta_updated == pytest.approx(0.3, rel=1e-6, abs=1e-6)


@pytest.mark.asyncio
async def test_submit_answer_prefers_explicit_theta_change(monkeypatch):
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    question_id = uuid.uuid4()
    fake_session = _FakeSessionService(
        user_id=user_id,
        session_id=session_id,
        question_id=question_id,
    )

    async def _fake_process_answer(
        _db,
        _user_id,
        _session_id,
        _question_id,
        _selected_index,
        _time_taken,
        _session_service,
        _used_hint,
    ):
        return {
            "correct": False,
            "explanation": "Incorrect.",
            "theta_change": -0.45,
            "theta_changes": [{"theta_before": 0.4, "theta_after": 0.8}],
            "next_question": None,
            "session_stats": {"questions_answered": 1, "correct_count": 0, "is_finished": False},
        }

    monkeypatch.setattr(classic_room_router.ClassicService, "process_answer", _fake_process_answer)

    body = classic_room_router.SubmitAnswerRequest(
        session_id=session_id,
        question_id=question_id,
        selected_index=0,
        time_taken=8,
        used_hint=True,
    )

    response = await _submit_impl(
        request=SimpleNamespace(),
        body=body,
        current_user_tuple=(SimpleNamespace(id=user_id), None),
        db=object(),
        session_svc=fake_session,
    )

    assert response.is_correct is False
    assert response.theta_updated == pytest.approx(-0.45, rel=1e-6, abs=1e-6)
