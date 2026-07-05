"""Regression tests for test concept discovery service behavior."""

import pytest

from services.concept_service import ConceptDiscoveryService, normalize_concept_parts


@pytest.mark.asyncio
async def test_extract_history_concept_bucket_from_timeline_cues() -> None:
    name, desc = await ConceptDiscoveryService.extract_concept_from_question(
        question_text="In what year did World War I begin?",
        correct_answer="1914",
        topic="history",
        explanation="This marks the opening of the conflict after the July Crisis.",
        topic_label="History - World War I",
    )

    assert "Timeline and Turning Points" in name
    assert "World War I" in desc
    assert "Historical concept inferred" in desc


@pytest.mark.asyncio
async def test_extract_geography_concept_bucket_from_physical_cues() -> None:
    name, _ = await ConceptDiscoveryService.extract_concept_from_question(
        question_text="Which river flows through Paris?",
        correct_answer="Seine",
        topic="geography",
        explanation="The river is central to the city's transport and urban growth.",
        topic_label="Geography - France",
    )

    assert "Physical Geography" in name


def test_topic_seed_concepts_generate_multiple_clusters() -> None:
    seeds = ConceptDiscoveryService._seed_concept_names("Geography - France")
    names = [name for name, _, _, _ in seeds]
    scopes = [scope for _, _, scope, _ in seeds]

    assert len(seeds) >= 4
    assert "France" in scopes
    assert "Physical Geography" in names
    assert "Economy and Resources" in names


def test_normalize_concept_parts_splits_redundant_prefix() -> None:
    name, topic, scope = normalize_concept_parts("Mixed - Core Concepts", "Mixed")

    assert name == "Core Concepts"
    assert topic == "mixed"
    assert scope == "Mixed"
