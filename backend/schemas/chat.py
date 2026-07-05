"""
schemas/chat.py — Pydantic request/response models for the Scholar Chat endpoint.
"""
from __future__ import annotations
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class ChatAskRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="The user's natural language question",
    )
    topic_hint: Optional[Literal["history", "geography", "mixed"]] = None


class ChatAskResponse(BaseModel):
    answer: str
    sources: List[str]       # e.g. ["wikipedia", "wikidata"]
    topic: str               # detected topic: "history"|"geography"|"mixed"
    grounded: bool           # True if RAG context was successfully retrieved
    confidence: str          # "high"|"medium"|"low" based on source quality
    response_time_ms: int    # for monitoring/debugging


class ChatErrorResponse(BaseModel):
    detail: str
    error_code: str          # "OUT_OF_SCOPE"|"SOURCES_UNAVAILABLE"|"RATE_LIMITED"


# Force Pydantic to fully resolve all models now
ChatAskRequest.model_rebuild()
ChatAskResponse.model_rebuild()
ChatErrorResponse.model_rebuild()
