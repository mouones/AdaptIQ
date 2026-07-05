"""Regression tests for test db integrity audit behavior."""

import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import scripts.repair_data_integrity as repair_module
from database.concept_models import Concept, QuestionConcept
from database.custom_models import Fact, Topic
from database.governance_models import GovernanceBlockRule
from database.models import Base, QuestionBank
from database.visual_models import VisualSession
from scripts.repair_data_integrity import (
    GENERIC_FALLBACK_EXPLANATION_FLAG,
    MANUAL_REVIEW_ANSWER,
    PLACEHOLDER_DATA_FLAG,
    audit_database,
    fallback_explanation,
    fallback_options,
    governance_needs_backfill,
    normalize_topic_display,
    options_integrity_error,
    placeholder_option_index,
    repair_database,
)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


def test_options_integrity_accepts_valid_options() -> None:
    assert options_integrity_error(json.dumps(["Paris", "Lyon", "Nice"]), "Paris") is None


def test_options_integrity_rejects_bad_json_and_missing_correct_answer() -> None:
    assert options_integrity_error("{bad", "Paris") == "invalid_json"
    assert (
        options_integrity_error(json.dumps(["Lyon", "Nice", "Marseille"]), "Paris")
        == "correct_answer_missing_from_options"
    )


def test_options_integrity_rejects_placeholder_answers_and_options() -> None:
    assert placeholder_option_index("A") == 0
    assert placeholder_option_index("Option 3") == 2
    assert options_integrity_error(json.dumps(["Paris", "Lyon", "Nice"]), "A") == "placeholder_correct_answer"
    assert options_integrity_error(json.dumps(["Option 1", "Option 2"]), "Option 1") == "placeholder_options"


def test_fallback_explanation_is_deterministic_and_flagged() -> None:
    explanation = fallback_explanation("What started in 1914?", "World War I", "History")

    assert "World War I" in explanation
    assert GENERIC_FALLBACK_EXPLANATION_FLAG in explanation
    assert normalize_topic_display("History - World War I") == "History"


def test_fallback_options_include_correct_answer_and_are_unique() -> None:
    options = fallback_options("1914", "History")

    assert options[0] == "1914"
    assert len(options) == 4
    assert len(set(options)) == 4


@pytest.mark.asyncio
async def test_audit_database_reports_nulls_mismatches_and_custom_counts(db_session) -> None:
    question_id = uuid.uuid4()
    concept_id = uuid.uuid4()
    wrong_concept_id = uuid.uuid4()

    db_session.add_all(
        [
            Concept(id=concept_id, name="History - Causes", topic="history"),
            Concept(id=wrong_concept_id, name="History - Timeline", topic="history"),
            QuestionBank(
                id=question_id,
                question_text="When did World War I begin?",
                correct_answer="1914",
                options_json=json.dumps(["1914", "1918"]),
                explanation="",
                topic="History",
                primary_concept_id=wrong_concept_id,
            ),
            QuestionConcept(
                question_id=question_id,
                concept_id=concept_id,
                is_primary=True,
            ),
            Topic(type="History", slug="ww1-approved", name="World War I", total_facts_count=1),
            Fact(topic="History - World War I", content="Fact"),
        ]
    )
    await db_session.commit()

    report = await audit_database(db_session)

    assert report["question_bank_total"] == 1
    assert report["critical_empty_fields"]["explanation"] == 1
    assert report["question_concepts"]["primary_mismatches"] == 1
    assert report["question_concepts"]["topic_mismatches"] == 0
    assert report["question_source_summary"]["generated"] == 1
    assert report["custom_topics_total"] == 1
    assert report["custom_facts_total"] == 1


@pytest.mark.asyncio
async def test_audit_reports_concept_prefix_and_visual_timestamp_anomalies(db_session) -> None:
    db_session.add_all(
        [
            Concept(
                id=uuid.uuid4(),
                name="Mixed - Core Concepts",
                topic="mixed",
                scope="general",
            ),
            VisualSession(
                id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                topic="History",
                level=1,
                total_questions=1,
                current_index=1,
                is_completed=True,
                ended_at=None,
            ),
        ]
    )
    await db_session.commit()

    report = await audit_database(db_session)

    assert report["concepts_redundant_scope_prefixes"] == 1
    assert report["visual_sessions"]["completed_without_ended_at"] == 1


@pytest.mark.asyncio
async def test_repair_normalizes_concept_prefix_and_visual_ended_at(db_session) -> None:
    concept = Concept(
        id=uuid.uuid4(),
        name="Mixed - Core Concepts",
        topic="mixed",
        scope="general",
    )
    session = VisualSession(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        topic="History",
        level=1,
        total_questions=1,
        current_index=1,
        is_completed=True,
        ended_at=None,
    )
    db_session.add_all([concept, session])
    await db_session.commit()

    result = await repair_database(db_session, apply=True)
    await db_session.refresh(concept)
    await db_session.refresh(session)

    assert result["planned_or_applied_changes"]["concept_names_normalized"] == 1
    assert concept.name == "Core Concepts"
    assert concept.scope == "Mixed"
    assert result["planned_or_applied_changes"]["visual_completed_sessions_ended_at_backfilled"] == 1
    assert session.ended_at is not None


@pytest.mark.asyncio
async def test_repair_maps_letter_answer_to_real_option(db_session) -> None:
    question = QuestionBank(
        id=uuid.uuid4(),
        question_text="Which city is the capital of France?",
        correct_answer="A",
        options_json=json.dumps(["Paris", "Lyon", "Nice", "Marseille"]),
        explanation="Valid explanation",
        topic="Geography",
    )
    db_session.add(question)
    await db_session.commit()

    result = await repair_database(db_session, apply=True)
    await db_session.refresh(question)

    assert result["planned_or_applied_changes"]["placeholder_correct_answers_mapped"] == 1
    assert question.correct_answer == "Paris"
    assert question.gov_safe is True
    assert question.gov_approved is True


@pytest.mark.asyncio
async def test_repair_flags_unusable_placeholder_question(db_session) -> None:
    question = QuestionBank(
        id=uuid.uuid4(),
        question_text="Sample question?",
        correct_answer="Option 1",
        options_json=json.dumps(["Option 1", "Option 2", "Option 3"]),
        explanation="",
        topic="History",
    )
    db_session.add(question)
    await db_session.commit()

    result = await repair_database(db_session, apply=True)
    await db_session.refresh(question)
    report = await audit_database(db_session)

    assert result["planned_or_applied_changes"]["placeholder_questions_flagged_for_review"] == 1
    assert question.correct_answer == MANUAL_REVIEW_ANSWER
    assert question.gov_safe is False
    assert question.gov_approved is False
    assert PLACEHOLDER_DATA_FLAG in (question.gov_flags_json or "")
    assert report["question_bank_flagged_for_review"] == 1


@pytest.mark.asyncio
async def test_repair_removes_custom_facts_from_flagged_questions(db_session) -> None:
    question = QuestionBank(
        id=uuid.uuid4(),
        question_text="Unsafe sample?",
        correct_answer=MANUAL_REVIEW_ANSWER,
        options_json=json.dumps([MANUAL_REVIEW_ANSWER, "Not enough information"]),
        explanation="Manual review required",
        topic="History",
        gov_approved=False,
        gov_safe=False,
        gov_flags_json=json.dumps([PLACEHOLDER_DATA_FLAG]),
    )
    db_session.add_all(
        [
            question,
            Topic(type="History", slug="ww1", name="World War I", total_facts_count=1),
            Fact(topic="History - World War I", content="Bad harvested fact", source_question_id=question.id),
        ]
    )
    await db_session.commit()

    before = await audit_database(db_session)
    result = await repair_database(db_session, apply=True)
    after = await audit_database(db_session)

    assert before["custom_facts_from_flagged_questions"] == 1
    assert result["planned_or_applied_changes"]["custom_facts_from_flagged_questions_removed"] == 1
    assert after["custom_facts_from_flagged_questions"] == 0
    assert after["custom_facts_total"] == 0


@pytest.mark.asyncio
async def test_repair_backfills_clean_governance_state(monkeypatch, db_session) -> None:
    monkeypatch.setattr(repair_module.GovernanceService, "enabled", staticmethod(lambda: True))
    question = QuestionBank(
        id=uuid.uuid4(),
        question_text="Which treaty ended World War I?",
        correct_answer="Treaty of Versailles",
        options_json=json.dumps(["Treaty of Versailles", "Treaty of Paris", "Treaty of Rome"]),
        explanation="The Treaty of Versailles formally ended World War I.",
        topic="History",
        gov_checked_at=None,
    )
    db_session.add(question)
    await db_session.commit()

    assert governance_needs_backfill(question) is True
    result = await repair_database(db_session, apply=True)
    await db_session.refresh(question)

    assert result["planned_or_applied_changes"]["governance_rows_backfilled"] == 1
    assert question.gov_approved is True
    assert question.gov_safe is True
    assert question.gov_checked_at is not None
    assert governance_needs_backfill(question) is False


@pytest.mark.asyncio
async def test_repair_backfills_rejected_governance_state(monkeypatch, db_session) -> None:
    monkeypatch.setattr(repair_module.GovernanceService, "enabled", staticmethod(lambda: True))
    db_session.add(
        GovernanceBlockRule(kind="keyword", pattern="blocked phrase", is_active=True)
    )
    question = QuestionBank(
        id=uuid.uuid4(),
        question_text="This question contains a blocked phrase.",
        correct_answer="Answer",
        options_json=json.dumps(["Answer", "Alternative"]),
        explanation="A normal explanation.",
        topic="History",
        gov_checked_at=None,
    )
    db_session.add(question)
    await db_session.commit()

    result = await repair_database(db_session, apply=True)
    await db_session.refresh(question)

    assert result["planned_or_applied_changes"]["governance_rows_backfilled"] == 1
    assert question.gov_approved is False
    assert question.gov_safe is False
    assert "blocked:keyword:blocked phrase" in (question.gov_flags_json or "")


@pytest.mark.asyncio
async def test_repair_reports_governance_backfill_skipped_when_disabled(monkeypatch, db_session) -> None:
    monkeypatch.setattr(repair_module.GovernanceService, "enabled", staticmethod(lambda: False))
    question = QuestionBank(
        id=uuid.uuid4(),
        question_text="Which city is the capital of Italy?",
        correct_answer="Rome",
        options_json=json.dumps(["Rome", "Milan"]),
        explanation="Rome is the capital city of Italy.",
        topic="Geography",
        gov_checked_at=None,
    )
    db_session.add(question)
    await db_session.commit()

    result = await repair_database(db_session, apply=True)
    await db_session.refresh(question)

    assert result["planned_or_applied_changes"]["governance_backfill_skipped_disabled"] == 1
    assert result["planned_or_applied_changes"]["governance_rows_backfilled"] == 0
    assert question.gov_checked_at is None
