"""Regression tests for test question concept repair behavior."""

import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.concept_models import Concept, QuestionConcept
from database.models import Base, QuestionBank
from scripts.repair_data_integrity import GENERIC_FALLBACK_EXPLANATION_FLAG, repair_database


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield Session
    finally:
        await engine.dispose()


async def _insert_question_with_primary_link(session_factory):
    question_id = uuid.uuid4()
    concept_id = uuid.uuid4()
    async with session_factory() as session:
        session.add_all(
            [
                Concept(id=concept_id, name="World War I - Timeline", topic="history"),
                QuestionBank(
                    id=question_id,
                    question_text="When did World War I begin?",
                    correct_answer="1914",
                    options_json=json.dumps(["1914", "1918", "1939"]),
                    explanation="",
                    topic="history",
                    primary_concept_id=None,
                ),
                QuestionConcept(
                    question_id=question_id,
                    concept_id=concept_id,
                    is_primary=True,
                ),
            ]
        )
        await session.commit()
    return question_id, concept_id


@pytest.mark.asyncio
async def test_repair_dry_run_does_not_mutate_rows(session_factory) -> None:
    question_id, concept_id = await _insert_question_with_primary_link(session_factory)

    async with session_factory() as session:
        report = await repair_database(session, apply=False)

    assert report["mode"] == "dry-run"
    assert report["planned_or_applied_changes"]["primary_concept_id_backfilled_from_primary_link"] == 1
    assert report["planned_or_applied_changes"]["blank_explanations_filled"] == 1

    async with session_factory() as session:
        question = await session.get(QuestionBank, question_id)

    assert question is not None
    assert question.primary_concept_id is None
    assert question.explanation == ""
    assert question.topic == "history"
    assert concept_id is not None


@pytest.mark.asyncio
async def test_repair_apply_backfills_primary_concept_and_explanation(session_factory) -> None:
    question_id, concept_id = await _insert_question_with_primary_link(session_factory)

    async with session_factory() as session:
        report = await repair_database(session, apply=True)

    assert report["mode"] == "apply"

    async with session_factory() as session:
        question = await session.get(QuestionBank, question_id)

    assert question is not None
    assert question.primary_concept_id == concept_id
    assert GENERIC_FALLBACK_EXPLANATION_FLAG in question.explanation
    assert question.topic == "History"


@pytest.mark.asyncio
async def test_repair_apply_infers_concept_for_unlinked_question(session_factory) -> None:
    question_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            QuestionBank(
                id=question_id,
                question_text="Which treaty ended World War I?",
                correct_answer="Treaty of Versailles",
                options_json=json.dumps(["Treaty of Versailles", "Treaty of Paris", "Treaty of Tordesillas"]),
                explanation="It formally ended the war between Germany and the Allied powers.",
                topic="History",
                primary_concept_id=None,
            )
        )
        await session.commit()

    async with session_factory() as session:
        report = await repair_database(session, apply=True)

    assert report["planned_or_applied_changes"]["concepts_inferred_for_unlinked_questions"] == 1

    async with session_factory() as session:
        question = await session.get(QuestionBank, question_id)
        links = (
            await session.execute(
                select(QuestionConcept).where(
                    QuestionConcept.question_id == question_id,
                    QuestionConcept.is_primary == True,  # noqa: E712
                )
            )
        ).scalars().all()

    assert question is not None
    assert question.primary_concept_id is not None
    assert len(links) == 1
    assert links[0].concept_id == question.primary_concept_id
