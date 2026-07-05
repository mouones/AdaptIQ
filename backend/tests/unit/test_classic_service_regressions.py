"""Regression tests for test classic service regressions behavior."""

import uuid
import json
from types import SimpleNamespace

import pytest

import services.classic_service as classic_service


class _FakeSessionService:
    def __init__(self):
        self.stored_state = None
        self.current_question = None

    async def store_session_state(self, session_id: str, state: dict) -> None:
        self.stored_state = (session_id, state)

    async def set_current_question(self, session_id: str, payload: dict) -> None:
        self.current_question = (session_id, payload)


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalarResult(self._rows)


@pytest.mark.asyncio
async def test_classic_start_session_allows_missing_concepts(monkeypatch) -> None:
    user_id = uuid.uuid4()
    session_svc = _FakeSessionService()

    async def _fake_select_concepts_for_session(*_args, **_kwargs):
        return []

    async def _fake_select_next_question(*_args, **_kwargs):
        return None

    async def _unexpected_theta_lookup(*_args, **_kwargs):
        raise AssertionError("Theta lookup must not run when concept list is empty")

    monkeypatch.setattr(
        classic_service.ClassicService,
        "select_concepts_for_session",
        _fake_select_concepts_for_session,
    )
    monkeypatch.setattr(
        classic_service.ClassicService,
        "select_next_question",
        _fake_select_next_question,
    )
    monkeypatch.setattr(
        classic_service.ConceptIRT,
        "get_user_concept_thetas",
        _unexpected_theta_lookup,
    )
    # Build a minimal async db mock with add/commit/refresh stubs.
    _added = []

    async def _noop(*_a, **_k):
        pass

    fake_db = SimpleNamespace(
        add=lambda obj: _added.append(obj),
        commit=_noop,
        refresh=_noop,
    )

    result = await classic_service.ClassicService.start_session(
        db=fake_db,
        user_id=user_id,
        topic="History",
        session_service=session_svc,
    )

    assert result["first_question"] is None
    assert result["session_stats"]["questions_answered"] == 0

    assert session_svc.stored_state is not None
    _sid, state = session_svc.stored_state
    assert state["user_id"] == str(user_id)
    assert state["topic"] == "History"
    assert state["concept_ids"] == []
    assert state["theta_snapshot"] == {}

    assert session_svc.current_question is None


@pytest.mark.asyncio
async def test_classic_select_next_question_uses_all_bank_before_llm(monkeypatch) -> None:
    user_id = uuid.uuid4()
    question_id = uuid.uuid4()
    candidate = SimpleNamespace(
        id=question_id,
        question_text="Which city is the capital of France?",
        correct_answer="Paris",
        options_json=json.dumps(["Paris", "Rome", "Madrid", "Lisbon"]),
        explanation="Paris is the capital of France.",
        topic="geography",
        difficulty_irt=1.0,
        times_seen=0,
    )

    class _FakeDb:
        def __init__(self):
            self.execute_rows = [[], [], [candidate], []]

        async def scalar(self, *_args, **_kwargs):
            return 10

        async def execute(self, *_args, **_kwargs):
            return _FakeExecuteResult(self.execute_rows.pop(0))

        async def commit(self):
            return None

    async def _empty_seen(*_args, **_kwargs):
        return set()

    monkeypatch.setattr(
        classic_service.ClassicService,
        "get_user_seen_question_ids",
        _empty_seen,
    )
    monkeypatch.setattr(classic_service.GovernanceService, "enabled", staticmethod(lambda: False))

    result = await classic_service.ClassicService.select_next_question(
        db=_FakeDb(),
        user_id=user_id,
        topic="history",
        concept_ids=[],
        asked_question_ids=[],
        theta_snapshot={},
    )

    assert result is not None
    assert result["id"] == str(question_id)
    assert result["topic"] == "geography"
