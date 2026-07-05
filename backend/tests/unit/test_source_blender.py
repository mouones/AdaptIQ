"""Regression tests for source blender behavior."""

from types import SimpleNamespace

import pytest

import services.source_blender as source_blender
from services.source_blender import SourceBlender, SourceBundle, _fetch_dbpedia, _fetch_worldbank

def test_source_bundle_validity():
    # Empty bundle
    b1 = SourceBundle()
    assert not b1.is_valid
    
    # Only structured
    b2 = SourceBundle(structured_facts=["Fact 1"])
    assert not b2.is_valid
    
    # Only narrative
    b3 = SourceBundle(narrative="This is a very long narrative that should definitely be over forty characters long to pass the check.")
    assert not b3.is_valid
    
    # Both
    b4 = SourceBundle(
        structured_facts=["Fact 1"],
        narrative="This is a very long narrative that should definitely be over forty characters long to pass the check."
    )
    assert b4.is_valid

@pytest.mark.asyncio
async def test_fetch_dbpedia_success():
    class MockClient:
        async def get(self, *args, **kwargs):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"results": {"bindings": [
                    {"name": {"value": "France"}, "abstract": {"value": "France is a country. It is in Europe."}}
                ]}}
            )
    
    client = MockClient()
    facts = await _fetch_dbpedia("Geography", client)
    assert len(facts) == 1
    assert facts[0] == "France: France is a country." # Only first sentence is kept

@pytest.mark.asyncio
async def test_fetch_dbpedia_failure():
    class MockClient:
        async def get(self, *args, **kwargs):
            return SimpleNamespace(status_code=500)
    
    client = MockClient()
    facts = await _fetch_dbpedia("Geography", client)
    assert facts == [] # Graceful fallback


@pytest.mark.asyncio
async def test_fetch_worldbank_skips_stat_fallback_for_mixed_and_history():
    class MockClient:
        async def get(self, *args, **kwargs):
            raise AssertionError("World Bank should not be called for Mixed/History fallback")

    client = MockClient()

    assert await _fetch_worldbank("Mixed", client) == []
    assert await _fetch_worldbank("History", client) == []


@pytest.mark.asyncio
async def test_source_blender_autofills_missing_context(monkeypatch):
    async def fake_wikidata(topic, difficulty, client):
        assert difficulty == 4
        return ["Paris is the capital of France.", "The Seine flows through Paris."]

    async def fake_dbpedia(topic, client):
        return ["Paris is the capital of France."]

    async def fake_wikipedia(topic, difficulty, client):
        return (
            "Paris has long been a political and cultural center of France. "
            "Its role in European history makes it a strong geography question anchor."
        )

    async def fake_hf(topic, difficulty):
        return {"id": "hf-1", "question": "Which city is the capital of France?"}

    monkeypatch.setattr(source_blender, "_fetch_wikidata_structured_facts", fake_wikidata)
    monkeypatch.setattr(source_blender, "_fetch_dbpedia", fake_dbpedia)
    monkeypatch.setattr(source_blender, "_fetch_wikipedia_narrative", fake_wikipedia)
    monkeypatch.setattr(source_blender, "async_get_hf_question", fake_hf)

    bundle = await SourceBlender().blend(
        topic="Mixed",
        difficulty=4,
        http_client=object(),
        wikidata_facts=None,
        wiki_narrative=None,
        hf_question=None,
    )

    assert bundle.is_valid
    assert bundle.structured_facts == [
        "Paris is the capital of France.",
        "The Seine flows through Paris.",
    ]
    assert bundle.hf_pattern == "Which city is the capital of France?"
    assert bundle.has_narrative is True
    assert "wiki:narrative" in bundle.sources
