"""Regression tests for test hint anti leak behavior."""

import pytest
from services.llm import LLMClient

@pytest.mark.asyncio
async def test_hint_anti_leak(monkeypatch):
    class FakeClient:
        async def post(self, *args, **kwargs):
            # We mock the chat completion directly instead of http
            pass
            
    llm = LLMClient("dummy")
    
    # Monkeypatch the chat completion to return a leaking hint
    async def fake_chat(*args, **kwargs):
        return "The answer is definitely Napoleon Bonaparte."
        
    monkeypatch.setattr(llm, "_chat_completion", fake_chat)
    
    question = "Who was the French emperor defeated at Waterloo?"
    correct_answer = "Napoleon"
    
    hint = await llm.generate_hint(question, correct_answer)
    
    # Should fallback because "Napoleon" is in the hint (leak detected)
    assert hint == "Think about the broader historical and geographical context of this topic."
    assert correct_answer.lower() not in hint.lower()

@pytest.mark.asyncio
async def test_hint_no_leak(monkeypatch):
    llm = LLMClient("dummy")
    
    async def fake_chat(*args, **kwargs):
        return "Think about the early 19th century."
        
    monkeypatch.setattr(llm, "_chat_completion", fake_chat)
    
    hint = await llm.generate_hint("When?", "1815")
    
    # Hint is safe, no fallback
    assert hint == "Think about the early 19th century."

@pytest.mark.asyncio
async def test_hint_short_answer_leak(monkeypatch):
    llm = LLMClient("dummy")
    
    async def fake_chat(*args, **kwargs):
        return "Think about the UK."
        
    monkeypatch.setattr(llm, "_chat_completion", fake_chat)
    
    hint = await llm.generate_hint("Where?", "UK")
    
    # The anti-leak uses first 8 chars, so UK matches exactly and falls back
    assert hint == "Think about the broader historical and geographical context of this topic."
