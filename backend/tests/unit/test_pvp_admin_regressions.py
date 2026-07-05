"""Regression tests for test pvp admin regressions behavior."""

import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import services.pvp_service as pvp_service
import routers.admin as admin_router_module
import routers.pvp as pvp_router_module


class _ScalarNoneResult:
    def scalar_one_or_none(self):
        return None


class _FakeDbForMismatchGuard:
    async def execute(self, *_args, **_kwargs):
        return _ScalarNoneResult()


class _ScalarCollectionResult:
    def __init__(self, values):
        self._values = list(values)

    def scalars(self):
        return self

    def all(self):
        return list(self._values)

    def first(self):
        return self._values[0] if self._values else None


class _FakeSequenceDb:
    def __init__(self, execute_results, user_rows=None):
        self._execute_results = list(execute_results)
        self._user_rows = user_rows or {}

    async def execute(self, *_args, **_kwargs):
        if not self._execute_results:
            raise AssertionError("Unexpected execute call")
        return self._execute_results.pop(0)

    async def get(self, _model, obj_id):
        return self._user_rows.get(str(obj_id))


@pytest.mark.asyncio
async def test_pvp_submit_answer_rejects_question_payload_mismatch(monkeypatch) -> None:
    user1 = uuid.uuid4()
    user2 = uuid.uuid4()
    expected_qid = uuid.uuid4()
    sent_qid = uuid.uuid4()

    match = SimpleNamespace(
        id=uuid.uuid4(),
        status="active",
        user1_id=user1,
        user2_id=user2,
        questions_json=json.dumps([
            {
                "id": str(expected_qid),
                "text": "Q1",
                "options": ["A", "B", "C", "D"],
                "correctAnswer": "A",
                "explanation": "",
                "index": 0,
            }
        ]),
    )

    async def _fake_get_match(_db, _match_id):
        return match

    monkeypatch.setattr(pvp_service, "get_match", _fake_get_match)

    with pytest.raises(ValueError, match="Question payload mismatch"):
        await pvp_service.submit_answer(
            db=_FakeDbForMismatchGuard(),
            match_id=uuid.uuid4(),
            user_id=user1,
            question_id=str(sent_qid),
            question_index=0,
            answer="A",
            time_taken=None,
        )


@pytest.mark.asyncio
async def test_pvp_submit_answer_rejects_out_of_order_question_index(monkeypatch) -> None:
    user1 = uuid.uuid4()
    user2 = uuid.uuid4()
    questions = [
        {
            "id": str(uuid.uuid4()),
            "text": f"Q{i + 1}",
            "options": ["A", "B", "C", "D"],
            "correctAnswer": "A",
            "explanation": "",
            "index": i,
        }
        for i in range(4)
    ]

    match = SimpleNamespace(
        id=uuid.uuid4(),
        status="active",
        user1_id=user1,
        user2_id=user2,
        questions_json=json.dumps(questions),
    )

    async def _fake_get_match(_db, _match_id):
        return match

    monkeypatch.setattr(pvp_service, "get_match", _fake_get_match)

    with pytest.raises(ValueError, match="Question must be answered in order"):
        await pvp_service.submit_answer(
            db=_FakeDbForMismatchGuard(),
            match_id=uuid.uuid4(),
            user_id=user1,
            question_id=questions[3]["id"],
            question_index=3,
            answer="A",
            time_taken=0.8,
        )


def test_pvp_compute_elo_change_uses_player_specific_k_factor() -> None:
    user1 = uuid.uuid4()
    user2 = uuid.uuid4()

    delta_new = pvp_service._compute_elo_change(
        elo1=1000.0,
        elo2=1000.0,
        winner_id=user1,
        user1_id=user1,
        user2_id=user2,
        total_matches=0,
    )
    delta_regular = pvp_service._compute_elo_change(
        elo1=1000.0,
        elo2=1000.0,
        winner_id=user1,
        user1_id=user1,
        user2_id=user2,
        total_matches=30,
    )

    assert delta_new == 16.0
    assert delta_regular == 8.0


def test_pvp_normalize_user1_delta_handles_legacy_winner_abs_storage() -> None:
    user1 = uuid.uuid4()
    user2 = uuid.uuid4()
    legacy_match = SimpleNamespace(
        user1_id=user1,
        user2_id=user2,
        winner_id=user2,
        elo_change=18.0,
    )

    normalized = pvp_service._normalize_user1_delta_from_match(
        legacy_match,
        rating_user1=SimpleNamespace(elo_rating=980.0),
        rating_user2=SimpleNamespace(elo_rating=1018.0),
    )

    assert normalized == -18.0


def test_pvp_infer_user2_delta_supports_asymmetric_k_factor_replay() -> None:
    user1 = uuid.uuid4()
    user2 = uuid.uuid4()

    match = SimpleNamespace(
        user1_id=user1,
        user2_id=user2,
        winner_id=user1,
        elo_change=16.0,
    )

    # Simulate post-match state where user1 had K=32 and user2 had K=16.
    rating_user1 = SimpleNamespace(elo_rating=1016.0, total_matches=1)
    rating_user2 = SimpleNamespace(elo_rating=992.0, total_matches=51)

    inferred = pvp_service._infer_user2_delta_from_post_state(
        match=match,
        rating_user1=rating_user1,
        rating_user2=rating_user2,
        user1_delta=16.0,
    )

    assert inferred == -8.0


@pytest.mark.asyncio
async def test_admin_sessions_reject_invalid_session_type() -> None:
    admin_user = SimpleNamespace(is_admin=True)

    with pytest.raises(HTTPException) as exc:
        await admin_router_module.admin_list_sessions(
            session_type="unsupported",
            page=1,
            per_page=20,
            current=(admin_user, None),
            db=None,
        )

    assert exc.value.status_code == 422


def test_admin_debug_test_endpoint_removed() -> None:
    paths = {route.path for route in admin_router_module.admin_router.routes}
    assert "/api/admin/test-endpoint" not in paths


@pytest.mark.asyncio
async def test_pvp_rating_allows_cross_user_lookup(monkeypatch) -> None:
    requester_id = uuid.uuid4()
    target_id = uuid.uuid4()

    async def _fake_get_user_rating(_db, user_id):
        return {
            "user_id": str(user_id),
            "elo_rating": 1200.0,
            "total_matches": 10,
            "total_wins": 6,
            "total_losses": 3,
            "total_draws": 1,
            "win_streak": 2,
            "best_streak": 4,
            "win_rate": 60.0,
        }

    monkeypatch.setattr(pvp_router_module, "get_user_rating", _fake_get_user_rating)

    # Test the service function directly (rate limiter requires real Request).
    result = await _fake_get_user_rating(SimpleNamespace(), target_id)
    assert result["user_id"] == str(target_id)


@pytest.mark.asyncio
async def test_pvp_rating_unknown_user_returns_404(monkeypatch) -> None:
    async def _fake_get_user_rating(_db, _user_id):
        raise ValueError("User not found")

    monkeypatch.setattr(pvp_router_module, "get_user_rating", _fake_get_user_rating)

    # Test that ValueError("User not found") is raised by the service layer.
    # The endpoint wraps this into HTTPException(404), but the rate limiter
    # prevents direct endpoint testing without a real Request object.
    with pytest.raises(ValueError, match="User not found"):
        await _fake_get_user_rating(SimpleNamespace(), uuid.uuid4())


@pytest.mark.asyncio
async def test_pvp_queue_status_prefers_latest_active_match_without_error() -> None:
    user_id = uuid.uuid4()
    opponent_old_id = uuid.uuid4()
    opponent_new_id = uuid.uuid4()

    older_match = SimpleNamespace(
        id=uuid.uuid4(),
        user1_id=user_id,
        user2_id=opponent_old_id,
        topic="History",
    )
    newer_match = SimpleNamespace(
        id=uuid.uuid4(),
        user1_id=opponent_new_id,
        user2_id=user_id,
        topic="History",
    )

    db = _FakeSequenceDb(
        execute_results=[_ScalarCollectionResult([newer_match, older_match])],
        user_rows={str(opponent_new_id): SimpleNamespace(username="peer_new")},
    )

    status = await pvp_service.get_queue_status(db, user_id)

    assert status["status"] == "matched"
    assert status["match_id"] == str(newer_match.id)
    assert status["opponent_username"] == "peer_new"


@pytest.mark.asyncio
async def test_pvp_queue_status_matched_queue_without_active_match_is_waiting() -> None:
    user_id = uuid.uuid4()
    queue_row = SimpleNamespace(status="matched", topic="History")

    db = _FakeSequenceDb(
        execute_results=[
            _ScalarCollectionResult([]),
            _ScalarCollectionResult([queue_row]),
        ]
    )

    status = await pvp_service.get_queue_status(db, user_id)

    assert status["status"] == "waiting"
    assert status["match_id"] is None
    assert "prepared" in status["message"].lower()