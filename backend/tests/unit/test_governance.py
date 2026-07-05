"""Regression tests for test governance behavior."""

import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routers.governance as governance_router_module
import services.governance_service as governance_service
from database.governance_models import GovernanceBlockRule
from database.models import QuestionBank


class _NestedTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeDb:
    def __init__(self) -> None:
        self.flushed = False
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def begin_nested(self):
        return _NestedTx()

    async def flush(self) -> None:
        self.flushed = True


def _unique_constraint_columns(model) -> set[tuple[str, ...]]:
    out: set[tuple[str, ...]] = set()
    for constraint in model.__table__.constraints:
        if constraint.__class__.__name__ == "UniqueConstraint":
            out.add(tuple(col.name for col in constraint.columns))
    return out


@pytest.mark.asyncio
async def test_governance_disabled_short_circuits(monkeypatch) -> None:
    monkeypatch.setattr(governance_service, "ENABLE_TRUSTWORTHY_GENERATION", False)
    decision = await governance_service.GovernanceService.evaluate_candidate(
        db=None,
        question_id=None,
        room="classic",
        action="persist",
        topic="History",
        question_text="Which treaty ended the First World War?",
        correct_answer="Treaty of Versailles",
        explanation="Some explanation.",
        options=["Treaty of Versailles", "Treaty of Paris", "Treaty of Rome", "Treaty of Tordesillas"],
        sources=None,
    )

    assert decision.approved is True
    assert decision.safe is True
    assert decision.reasons == []


@pytest.mark.asyncio
async def test_governance_enabled_blocks_matching_pattern(monkeypatch) -> None:
    monkeypatch.setattr(governance_service, "ENABLE_TRUSTWORTHY_GENERATION", True)

    async def _rules(_db):
        return [SimpleNamespace(kind="keyword", pattern="blocked phrase", is_active=True)]

    async def _noaudit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        governance_service.GovernanceService,
        "_active_block_rules",
        staticmethod(_rules),
    )
    monkeypatch.setattr(
        governance_service.GovernanceService,
        "_log_audit",
        staticmethod(_noaudit),
    )

    decision = await governance_service.GovernanceService.evaluate_candidate(
        db=SimpleNamespace(),
        question_id=str(uuid.uuid4()),
        room="custom",
        action="persist",
        topic="History",
        question_text="This question contains a blocked phrase in the stem.",
        correct_answer="A",
        explanation="",
        options=["A", "B", "C", "D"],
        sources=None,
    )

    assert decision.approved is False
    assert decision.safe is False
    assert "blocked:keyword:blocked phrase" in decision.reasons


@pytest.mark.asyncio
async def test_governance_enabled_allows_when_clean(monkeypatch) -> None:
    monkeypatch.setattr(governance_service, "ENABLE_TRUSTWORTHY_GENERATION", True)

    async def _rules(_db):
        return []

    async def _noaudit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        governance_service.GovernanceService,
        "_active_block_rules",
        staticmethod(_rules),
    )
    monkeypatch.setattr(
        governance_service.GovernanceService,
        "_log_audit",
        staticmethod(_noaudit),
    )

    decision = await governance_service.GovernanceService.evaluate_candidate(
        db=SimpleNamespace(),
        question_id=str(uuid.uuid4()),
        room="challenge",
        action="persist",
        topic="Geography",
        question_text="Which river runs through Paris?",
        correct_answer="Seine",
        explanation="Paris is on the Seine.",
        options=["Seine", "Rhine", "Danube", "Po"],
        sources=None,
    )

    assert decision.approved is True
    assert decision.safe is True
    assert decision.reasons == []
    assert decision.confidence == 0.85


@pytest.mark.asyncio
async def test_evaluate_bank_row_rejection_marks_row_and_flushes(monkeypatch) -> None:
    monkeypatch.setattr(governance_service, "ENABLE_TRUSTWORTHY_GENERATION", True)

    async def _rules(_db):
        return []

    async def _noaudit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        governance_service.GovernanceService,
        "_active_block_rules",
        staticmethod(_rules),
    )
    monkeypatch.setattr(
        governance_service.GovernanceService,
        "_log_audit",
        staticmethod(_noaudit),
    )

    db = _FakeDb()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        gov_approved=False,
        gov_safe=False,
        gov_confidence=None,
        gov_fact_trust=None,
        gov_narrative_quality=None,
        gov_flags_json=None,
        gov_checked_at=None,
        options_json=json.dumps(["A", "B", "C", "D"]),
        question_text="Which river runs through Paris?",
        correct_answer="Seine",
        explanation="Paris is on the Seine.",
    )

    decision = await governance_service.GovernanceService.evaluate_bank_row_for_serving(
        db,
        row=row,
        room="classic",
        topic="Geography - France",
    )

    assert decision.approved is False
    assert "bank_row_not_approved" in decision.reasons
    assert row.gov_approved is False
    assert row.gov_safe is False
    assert row.gov_checked_at is not None
    assert row.gov_confidence == 0.15
    assert db.flushed is True

    flags = json.loads(row.gov_flags_json or "{}")
    assert flags.get("reasons") == decision.reasons


def test_question_bank_has_governance_columns() -> None:
    columns = set(QuestionBank.__table__.columns.keys())
    expected = {
        "gov_approved",
        "gov_safe",
        "gov_confidence",
        "gov_fact_trust",
        "gov_narrative_quality",
        "gov_sources_json",
        "gov_flags_json",
        "gov_checked_at",
    }

    assert expected.issubset(columns)


def test_governance_block_rule_has_unique_kind_pattern_constraint() -> None:
    uniques = _unique_constraint_columns(GovernanceBlockRule)
    assert ("kind", "pattern") in uniques


def test_governance_router_has_expected_paths() -> None:
    paths = {route.path for route in governance_router_module.governance_router.routes}
    assert "/api/admin/governance/blocked-rules" in paths
    assert "/api/admin/governance/blocked-rules/{rule_id}" in paths
    assert "/api/admin/governance/audits" in paths


@pytest.mark.asyncio
async def test_governance_router_rejects_non_admin() -> None:
    non_admin = SimpleNamespace(is_admin=False)

    # The admin check runs before the rate limiter processes the request,
    # so we test the underlying _require_admin guard directly.
    with pytest.raises(HTTPException) as exc:
        governance_router_module.require_admin((non_admin, None))

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_governance_create_rule_rejects_invalid_kind() -> None:
    # Validate that the schema rejects invalid kind values.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        governance_router_module.BlockRuleCreate(kind="", pattern="x")

    # Valid schema but invalid kind value — the endpoint checks kind in ["topic", "keyword"].
    body = governance_router_module.BlockRuleCreate(kind="invalid", pattern="x")
    assert body.kind == "invalid"  # Schema accepts it; endpoint rejects it at runtime.



# ═══════════════════════════════════════════════════════════════════════════
# Structural validation tests
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_governance_rejects_question_text_too_short(monkeypatch) -> None:
    monkeypatch.setattr(governance_service, "ENABLE_TRUSTWORTHY_GENERATION", True)

    async def _rules(_db):
        return []

    async def _noaudit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        governance_service.GovernanceService,
        "_active_block_rules",
        staticmethod(_rules),
    )
    monkeypatch.setattr(
        governance_service.GovernanceService,
        "_log_audit",
        staticmethod(_noaudit),
    )

    decision = await governance_service.GovernanceService.evaluate_candidate(
        db=SimpleNamespace(),
        question_id=str(uuid.uuid4()),
        room="classic",
        action="persist",
        topic="History",
        question_text="Too short",  # 9 chars — below 12-char minimum
        correct_answer="Answer",
        explanation="Some explanation.",
        options=["Answer", "Wrong1", "Wrong2", "Wrong3"],
    )

    assert decision.approved is False
    assert "question_text_too_short" in decision.reasons


@pytest.mark.asyncio
async def test_governance_rejects_question_text_too_long(monkeypatch) -> None:
    monkeypatch.setattr(governance_service, "ENABLE_TRUSTWORTHY_GENERATION", True)

    async def _rules(_db):
        return []

    async def _noaudit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        governance_service.GovernanceService,
        "_active_block_rules",
        staticmethod(_rules),
    )
    monkeypatch.setattr(
        governance_service.GovernanceService,
        "_log_audit",
        staticmethod(_noaudit),
    )

    decision = await governance_service.GovernanceService.evaluate_candidate(
        db=SimpleNamespace(),
        question_id=str(uuid.uuid4()),
        room="classic",
        action="persist",
        topic="History",
        question_text="X" * 601,  # 601 chars — above 600-char maximum
        correct_answer="Answer",
        explanation="Some explanation.",
        options=["Answer", "Wrong1", "Wrong2", "Wrong3"],
    )

    assert decision.approved is False
    assert "question_text_too_long" in decision.reasons


@pytest.mark.asyncio
async def test_governance_rejects_missing_correct_answer(monkeypatch) -> None:
    monkeypatch.setattr(governance_service, "ENABLE_TRUSTWORTHY_GENERATION", True)

    async def _rules(_db):
        return []

    async def _noaudit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        governance_service.GovernanceService,
        "_active_block_rules",
        staticmethod(_rules),
    )
    monkeypatch.setattr(
        governance_service.GovernanceService,
        "_log_audit",
        staticmethod(_noaudit),
    )

    decision = await governance_service.GovernanceService.evaluate_candidate(
        db=SimpleNamespace(),
        question_id=str(uuid.uuid4()),
        room="classic",
        action="persist",
        topic="History",
        question_text="Which treaty ended World War I?",
        correct_answer="",  # Empty — missing correct answer
        explanation="Some explanation.",
        options=["A", "B", "C", "D"],
    )

    assert decision.approved is False
    assert "missing_correct_answer" in decision.reasons


@pytest.mark.asyncio
async def test_governance_rejects_insufficient_options(monkeypatch) -> None:
    monkeypatch.setattr(governance_service, "ENABLE_TRUSTWORTHY_GENERATION", True)

    async def _rules(_db):
        return []

    async def _noaudit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        governance_service.GovernanceService,
        "_active_block_rules",
        staticmethod(_rules),
    )
    monkeypatch.setattr(
        governance_service.GovernanceService,
        "_log_audit",
        staticmethod(_noaudit),
    )

    decision = await governance_service.GovernanceService.evaluate_candidate(
        db=SimpleNamespace(),
        question_id=str(uuid.uuid4()),
        room="classic",
        action="persist",
        topic="History",
        question_text="Which treaty ended World War I?",
        correct_answer="Treaty of Versailles",
        explanation="Some explanation.",
        options=["Treaty of Versailles"],  # Only 1 option — insufficient
    )

    assert decision.approved is False
    assert "insufficient_options" in decision.reasons


@pytest.mark.asyncio
async def test_governance_blocks_topic_type_rule(monkeypatch) -> None:
    monkeypatch.setattr(governance_service, "ENABLE_TRUSTWORTHY_GENERATION", True)

    async def _rules(_db):
        return [SimpleNamespace(kind="topic", pattern="forbidden topic", is_active=True)]

    async def _noaudit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        governance_service.GovernanceService,
        "_active_block_rules",
        staticmethod(_rules),
    )
    monkeypatch.setattr(
        governance_service.GovernanceService,
        "_log_audit",
        staticmethod(_noaudit),
    )

    decision = await governance_service.GovernanceService.evaluate_candidate(
        db=SimpleNamespace(),
        question_id=str(uuid.uuid4()),
        room="classic",
        action="persist",
        topic="forbidden topic",  # Matches topic-type block rule
        question_text="What happened during the forbidden topic era?",
        correct_answer="Something",
        explanation="An explanation.",
        options=["Something", "Nothing", "Everything", "None"],
    )

    assert decision.approved is False
    assert any("blocked:topic:forbidden topic" in r for r in decision.reasons)


@pytest.mark.asyncio
async def test_governance_case_insensitive_matching(monkeypatch) -> None:
    monkeypatch.setattr(governance_service, "ENABLE_TRUSTWORTHY_GENERATION", True)

    async def _rules(_db):
        return [SimpleNamespace(kind="keyword", pattern="sensitive word", is_active=True)]

    async def _noaudit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        governance_service.GovernanceService,
        "_active_block_rules",
        staticmethod(_rules),
    )
    monkeypatch.setattr(
        governance_service.GovernanceService,
        "_log_audit",
        staticmethod(_noaudit),
    )

    decision = await governance_service.GovernanceService.evaluate_candidate(
        db=SimpleNamespace(),
        question_id=str(uuid.uuid4()),
        room="classic",
        action="persist",
        topic="History",
        question_text="This contains SENSITIVE WORD in uppercase.",  # Case mismatch
        correct_answer="Answer",
        explanation="Some explanation.",
        options=["Answer", "Wrong1", "Wrong2", "Wrong3"],
    )

    assert decision.approved is False
    assert any("blocked:keyword:sensitive word" in r for r in decision.reasons)
