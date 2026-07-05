"""Regression tests for test custom hint behavior."""

import inspect
import uuid
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_custom_generate_hint_monkeypatched(monkeypatch):
    """Call the custom.generate_hint flow with mocked LLM/DB/Governance.

    This test deliberately avoids touching the real database or external LLM.
    """
    # Import inside test so monkeypatch targets resolve correctly.
    from routers import custom
    from schemas.custom import GenerateCustomHintRequest

    # Fake LLM that returns a deterministic hint
    class FakeLLM:
        async def generate_hint(self, question_text: str, correct_answer: str):
            return "Fake hint for testing"

    fake_llm = FakeLLM()

    async def fake_get_llm(request):
        return fake_llm

    # Fake DB that returns a simple object for QuestionBank.get
    class FakeDB:
        async def get(self, model, qid):
            return SimpleNamespace(
                question_text="Which country is known as the Land of the Rising Sun?",
                correct_answer="Japan",
                topic="Geography - Countries",
            )

    async def fake_db_gen(request):
        yield FakeDB()

    # Disable governance checks to avoid DB writes / rule lookups
    import services.governance_service as gov
    monkeypatch.setattr(gov.GovernanceService, "enabled", staticmethod(lambda: False))

    # Patch module helpers
    monkeypatch.setattr(custom, "_get_llm", fake_get_llm)
    monkeypatch.setattr(custom, "_get_db", fake_db_gen)

    # Build request body
    body = GenerateCustomHintRequest(question_id=str(uuid.uuid4()), question_text=None)

    # Create a minimal fake request and a dummy current user
    fake_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    dummy_current = (SimpleNamespace(id=uuid.uuid4()), None)

    # Call the handler directly (bypass FastAPI dependency injection)
    hint_out = await inspect.unwrap(custom.generate_hint)(body=body, request=fake_request, current=dummy_current)

    assert hint_out.hint == "Fake hint for testing"
