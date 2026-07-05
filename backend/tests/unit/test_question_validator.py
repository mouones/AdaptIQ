"""Regression tests for test question validator behavior."""

import pytest
from services.question_validator import (
    _check_length, _check_narrative_score, _check_structured_facts,
    _check_options, _check_correct_answer_in_options, validate_question
)
from services.source_blender import SourceBundle

def test_check_length():
    # Too short (under 15 words)
    short_q = "What is the capital of France?"
    res = _check_length(short_q)
    assert res is not None
    assert res.rejection_code == "TOO_SHORT"
    
    # Too long (over 50 words)
    long_q = " ".join(["word"] * 55)
    res = _check_length(long_q)
    assert res is not None
    assert res.rejection_code == "TOO_LONG"
    
    # Just right (20 words)
    good_q = " ".join(["word"] * 20)
    assert _check_length(good_q) is None

def test_check_narrative_score():
    assert _check_narrative_score(0.65) is not None
    assert _check_narrative_score(0.65).rejection_code == "LOW_NARRATIVE"
    assert _check_narrative_score(0.75) is None

def test_check_structured_facts():
    bundle_empty = SourceBundle(structured_facts=[])
    res = _check_structured_facts(bundle_empty)
    assert res is not None
    assert res.rejection_code == "NO_STRUCTURED_FACTS"
    
    bundle_ok = SourceBundle(structured_facts=["Fact 1"])
    assert _check_structured_facts(bundle_ok) is None

def test_check_options():
    # Too few
    assert _check_options(["A"]).rejection_code == "TOO_FEW_OPTIONS"
    
    # Duplicates
    assert _check_options(["A", "B", "A", "C"]).rejection_code == "DUPLICATE_OPTIONS"
    
    # Empty strings are skipped, so this is valid (A, B, C)
    assert _check_options(["A", "B", " ", "C"]) is None
    
    # Good
    assert _check_options(["A", "B", "C", "D"]) is None

def test_check_correct_answer_in_options():
    options = ["Paris", "London", "Berlin", "Madrid"]
    assert _check_correct_answer_in_options("Paris", options) is None
    assert _check_correct_answer_in_options("paris", options) is None  # Case insensitive
    assert _check_correct_answer_in_options("Rome", options).rejection_code == "CORRECT_NOT_IN_OPTIONS"

def test_validate_question_full_pass():
    question = " ".join(["word"] * 20)
    options = ["A", "B", "C", "D"]
    correct = "A"
    bundle = SourceBundle(structured_facts=["Fact 1"])
    
    res = validate_question(question, options, correct, "Math", 0.8, bundle)
    assert res.passed is True
