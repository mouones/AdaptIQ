from services.question_sources import (
    NON_CLASSIC_SOURCE_PREFIXES,
    NON_CLASSIC_SOURCE_VALUES,
    categorize_question_source,
    is_generated_question_source,
    is_non_classic_question_source,
    summarize_source_counts,
)


def test_question_source_categories_cover_live_generators() -> None:
    assert categorize_question_source("seed") == "seeded"
    assert categorize_question_source("admin") == "admin"
    assert categorize_question_source("llm") == "classic_generated"
    assert categorize_question_source("classic_llm") == "classic_generated"
    assert categorize_question_source("challenge_llm") == "challenge_generated"
    assert categorize_question_source("custom_llm_simple") == "custom_generated"
    assert categorize_question_source("custom_template") == "custom_template"
    assert categorize_question_source("custom_rag_wikipedia") == "custom_rag"
    assert categorize_question_source(None) == "unknown"


def test_source_helpers_distinguish_generated_and_classic_reuse() -> None:
    assert is_generated_question_source("custom_template_simple")
    assert is_generated_question_source("classic_llm")
    assert not is_generated_question_source("seed")
    assert is_non_classic_question_source("custom_rag_wikipedia")
    assert is_non_classic_question_source("challenge_llm")
    assert not is_non_classic_question_source("classic_llm")
    assert "custom_llm_simple" in NON_CLASSIC_SOURCE_VALUES
    assert "custom_rag" in NON_CLASSIC_SOURCE_PREFIXES


def test_summarize_source_counts_preserves_raw_and_grouped_counts() -> None:
    summary = summarize_source_counts(
        {
            "seed": 2,
            "llm": 3,
            "custom_llm_simple": 5,
            "custom_template": 7,
            "challenge_llm": 11,
            "mystery": 13,
        }
    )

    assert summary["seeded"] == 2
    assert summary["generated"] == 26
    assert summary["unknown"] == 13
    assert summary["by_category"]["classic_generated"] == 3
    assert summary["by_category"]["custom_generated"] == 5
    assert summary["by_category"]["custom_template"] == 7
    assert summary["by_source"]["mystery"] == 13
