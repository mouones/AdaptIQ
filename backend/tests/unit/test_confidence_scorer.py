"""Regression tests for test confidence scorer behavior."""

import pytest
from services.confidence_scorer import score_fact_trust, score_narrative_quality, _heuristic_narrative_score
from services.source_blender import SourceBundle

def test_score_fact_trust_no_facts():
    bundle = SourceBundle(structured_facts=[], sources=["wikipedia:123"])
    assert score_fact_trust(bundle, "What is the capital of France?") == 0.0

def test_score_fact_trust_source_diversity():
    # 1 family
    bundle1 = SourceBundle(structured_facts=["Fact 1"], sources=["wikidata:Q123"])
    assert score_fact_trust(bundle1, "General question") == 0.70  # base 0.70 + 0 bonus
    
    # 2 families
    bundle2 = SourceBundle(structured_facts=["Fact 1"], sources=["wikidata:Q123", "dbpedia:456"])
    assert score_fact_trust(bundle2, "General question") == 0.80
    
    # 3 families
    bundle3 = SourceBundle(structured_facts=["Fact 1"], sources=["wikidata:Q123", "dbpedia:456", "worldbank:789"])
    assert score_fact_trust(bundle3, "General question") == 0.90

def test_score_fact_trust_verifiable_bonus():
    bundle = SourceBundle(structured_facts=["Fact 1"], sources=["wikidata:Q123"])
    # "1945" (year) and "World War" (proper noun)
    question = "In what year did World War II end, 1945 or 1946?"
    
    score = score_fact_trust(bundle, question)
    # base 0.70 + bonus for 1945, 1946, World War
    assert score > 0.70
    assert score <= 1.0

def test_heuristic_narrative_score():
    # Too short
    assert _heuristic_narrative_score("What?", "Short") < 0.5
    
    # Good length, engaging words
    q = "Why did the intriguing Roman Empire finally fall in 476 AD?"
    exp = "The fall was caused by a mix of fascinating economic troubles, military overspending, and relentless barbarian invasions that weakened the core."
    assert _heuristic_narrative_score(q, exp) >= 0.6

@pytest.mark.asyncio
async def test_score_narrative_quality_fallback():
    # Test that when LLM fails, it falls back to heuristic
    class FailingLLM:
        async def simple_completion(self, prompt):
            raise ValueError("API Down")
            
    score = await score_narrative_quality("Valid question?", "Valid explanation with good length.", FailingLLM())
    assert score > 0.0  # Heuristic should return a non-zero score
