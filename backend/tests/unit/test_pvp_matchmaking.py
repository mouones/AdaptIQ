"""Regression tests for test pvp matchmaking behavior."""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import services.pvp_service as pvp_service
from services.pvp_service import ELO_DEFAULT, ELO_K_NEW, ELO_K_REGULAR, ELO_MAX_DIFF


class _RowsResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)


class _SequenceDb:
    def __init__(self, execute_results):
        self._execute_results = list(execute_results)
        self.execute_calls = 0

    async def execute(self, *_args, **_kwargs):
        self.execute_calls += 1
        if not self._execute_results:
            raise AssertionError("Unexpected execute call")
        return self._execute_results.pop(0)


def _queue_entry(user_id: uuid.UUID, elo: float, concepts_json: str, joined_minutes_ago: int = 0):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return SimpleNamespace(
        user_id=user_id,
        elo_rating=elo,
        concepts_json=concepts_json,
        joined_at=now - timedelta(minutes=joined_minutes_ago),
    )


def test_pvp_constants():
    assert ELO_MAX_DIFF == 300
    assert ELO_DEFAULT == 1000.0
    assert ELO_K_NEW == 32
    assert ELO_K_REGULAR == 16


def test_elo_closeness_score_respects_max_diff():
    assert pvp_service._elo_closeness_score(1000.0, 1000.0) == 1.0
    assert pvp_service._elo_closeness_score(1000.0, 1300.0) == 0.0
    assert pvp_service._elo_closeness_score(1000.0, 1310.0) == 0.0


def test_safe_load_concepts_handles_invalid_payloads():
    assert pvp_service._safe_load_concepts(None) == set()
    assert pvp_service._safe_load_concepts("{") == set()
    assert pvp_service._safe_load_concepts('{"x": 1}') == set()
    assert pvp_service._safe_load_concepts('["a", "b", "a"]') == {"a", "b"}


@pytest.mark.asyncio
async def test_load_user_matchmaking_concepts_prefers_mastered_first():
    mastered_concept_1 = uuid.uuid4()
    mastered_concept_2 = uuid.uuid4()
    fallback_concept = uuid.uuid4()

    db = _SequenceDb(
        execute_results=[
            _RowsResult([(mastered_concept_1,), (mastered_concept_2,)]),
            _RowsResult([(fallback_concept,)]),
        ]
    )

    result = await pvp_service._load_user_matchmaking_concepts(db, uuid.uuid4())

    assert result == [str(mastered_concept_1), str(mastered_concept_2)]
    # Fallback query should not run when mastered concepts exist.
    assert db.execute_calls == 1


@pytest.mark.asyncio
async def test_load_user_matchmaking_concepts_falls_back_when_no_mastered():
    fallback_concept_1 = uuid.uuid4()
    fallback_concept_2 = uuid.uuid4()
    db = _SequenceDb(
        execute_results=[
            _RowsResult([]),
            _RowsResult([(fallback_concept_1,), (fallback_concept_2,)]),
        ]
    )

    result = await pvp_service._load_user_matchmaking_concepts(db, uuid.uuid4())

    assert result == [str(fallback_concept_1), str(fallback_concept_2)]
    assert db.execute_calls == 2


@pytest.mark.asyncio
async def test_calculate_concept_affinity_uses_theta_distance(monkeypatch):
    user_1 = uuid.uuid4()
    user_2 = uuid.uuid4()
    concept_a = str(uuid.uuid4())
    concept_b = str(uuid.uuid4())
    shared = {concept_a, concept_b}

    async def _fake_theta_map(_db, user_id, _concept_ids):
        if user_id == user_1:
            return {concept_a: 2.0, concept_b: 1.0}
        return {concept_a: 1.5, concept_b: -1.0}

    monkeypatch.setattr(pvp_service, "_load_theta_map", _fake_theta_map)

    score = await pvp_service._calculate_concept_affinity(None, user_1, user_2, shared)

    expected_avg = ((1.0 / (1.0 + 0.5)) + (1.0 / (1.0 + 2.0))) / 2.0
    expected_coverage = 0.2
    expected = (expected_avg * 0.8) + (expected_coverage * 0.2)
    assert score == pytest.approx(expected, rel=1e-6)


@pytest.mark.asyncio
async def test_score_candidate_rejects_large_elo_gap(monkeypatch):
    user_1 = uuid.uuid4()
    user_2 = uuid.uuid4()
    entry = _queue_entry(user_1, 1000.0, "[]")
    candidate = _queue_entry(user_2, 1400.0, "[]")

    async def _fake_affinity(_db, _u1, _u2, _shared):
        return 1.0

    monkeypatch.setattr(pvp_service, "_calculate_concept_affinity", _fake_affinity)

    score = await pvp_service._score_candidate(None, entry, candidate)
    assert score == 0.0


@pytest.mark.asyncio
async def test_score_candidate_prefers_higher_affinity(monkeypatch):
    user_1 = uuid.uuid4()
    candidate_good = uuid.uuid4()
    candidate_weak = uuid.uuid4()
    concept = str(uuid.uuid4())

    entry = _queue_entry(user_1, 1000.0, f'["{concept}"]')
    cand_1 = _queue_entry(candidate_good, 1010.0, f'["{concept}"]')
    cand_2 = _queue_entry(candidate_weak, 1010.0, f'["{concept}"]')

    async def _fake_affinity(_db, _u1, u2, _shared):
        return 0.95 if u2 == candidate_good else 0.15

    monkeypatch.setattr(pvp_service, "_calculate_concept_affinity", _fake_affinity)

    score_good = await pvp_service._score_candidate(None, entry, cand_1)
    score_weak = await pvp_service._score_candidate(None, entry, cand_2)
    assert score_good > score_weak
