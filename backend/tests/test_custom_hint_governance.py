"""Regression tests for test custom hint governance behavior."""

import inspect
import uuid
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_custom_generate_hint_governance_rejects(monkeypatch):
    """Ensure custom.generate_hint falls back when governance rejects the hint."""
    from routers import custom
    from schemas.custom import GenerateCustomHintRequest

    # Fake LLM returns a specific hint that should be blocked
    class FakeLLM:
        async def generate_hint(self, question_text: str, correct_answer: str):
            return "This hint would be blocked"

    fake_llm = FakeLLM()

    async def fake_get_llm(request):
        return fake_llm

    # Fake DB
    class FakeDB:
        async def get(self, model, qid):
            return SimpleNamespace(
                question_text="When did the Battle of Hastings occur?",
                correct_answer="1066",
                topic="History - Medieval",
            )

    async def fake_db_gen(request):
        yield FakeDB()

    # Make governance enabled and return a rejecting decision
    import importlib
    # Patch both import paths to be safe: 'mhd.backend.services.governance_service' and 'services.governance_service'
    for mod_name in ("services.governance_service",):
        try:
            svc = importlib.import_module(mod_name)
        except Exception:
            svc = None
        if svc is not None:
            monkeypatch.setattr(svc.GovernanceService, "enabled", staticmethod(lambda: True))
            async def _fake_eval(*args, **kwargs):
                return SimpleNamespace(approved=False, reasons=["blocked:test"])
            monkeypatch.setattr(svc.GovernanceService, "evaluate_candidate", staticmethod(_fake_eval))

    # Patch module helpers
    monkeypatch.setattr(custom, "_get_llm", fake_get_llm)
    monkeypatch.setattr(custom, "_get_db", fake_db_gen)

    body = GenerateCustomHintRequest(question_id=str(uuid.uuid4()), question_text=None)
    fake_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    dummy_current = (SimpleNamespace(id=uuid.uuid4()), None)

    hint_out = await inspect.unwrap(custom.generate_hint)(body=body, request=fake_request, current=dummy_current)

    assert hint_out.hint == "Think about the broader historical and geographical context of this topic."
