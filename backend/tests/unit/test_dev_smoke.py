"""Unit tests for the read-only developer smoke helper."""

from scripts.dev.smoke_test import context_mentions_question, mask_url


def test_mask_url_hides_credentials() -> None:
    masked = mask_url("postgresql+asyncpg://user:secret@localhost:5433/adaptiq_db")

    assert masked == "postgresql+asyncpg://***@localhost:5433/adaptiq_db"
    assert "secret" not in masked


def test_context_mentions_question_detects_relevant_terms() -> None:
    assert context_mentions_question(
        "The Roman Empire shaped law, engineering, and administration.",
        "What made the Roman Empire influential?",
    )


def test_context_mentions_question_rejects_unrelated_context() -> None:
    assert not context_mentions_question(
        "The Meiji Restoration transformed nineteenth-century Japan.",
        "What made the Roman Empire influential?",
    )
