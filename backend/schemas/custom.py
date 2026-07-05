"""
Custom Room request/response models.

User and session identifiers are UUID strings at the API boundary.
"""
from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field


class StartSessionRequest(BaseModel):
    user_id: str
    topic:   str   = Field(..., json_schema_extra={"example": "History - World War II"})
    concept_id: Optional[str] = None


class GenerateQuestionRequest(BaseModel):
    session_id: str
    topic:      str
    concept_id: Optional[str] = None
    level: int = Field(default=3, ge=1, le=5)


class SubmitAnswerRequest(BaseModel):
    session_id:    str
    question_id:   str
    answer:        str
    used_hint:     bool = False
    time_taken:    int = Field(default=0, ge=0, le=300)
    # Deprecated client fields (ignored by server for integrity).
    correct_answer: Optional[str] = None
    explanation:   Optional[str] = None


class TopicOut(BaseModel):
    type:        str
    slug:        str
    name:        str
    description: str
    total_facts: int


class TopicsResponse(BaseModel):
    topics: List[TopicOut]


class StartSessionResponse(BaseModel):
    session_id:               str
    topic:                    str
    concept_id:               Optional[str] = None
    progress_percentage:      float
    total_questions_estimate: int


class CustomQuestionResponse(BaseModel):
    id:             str
    text:           str
    options:        List[str]
    explanation:    str
    fact_id:        Optional[str] = None
    concept_id:     Optional[str] = None
    level:          int = Field(default=1, ge=1, le=5)
    is_free_text:   bool = False


class SubmitAnswerResponse(BaseModel):
    is_correct:                   bool
    correct_answer:               str
    explanation:                  str
    new_progress_percentage:      float
    total_questions_this_session: int


class EndSessionResponse(BaseModel):
    session_id:                   str
    topic:                        str
    questions_answered:           int
    correct_count:                int
    completion_percentage_after:  float


# POST /api/custom/generate-hint

class GenerateCustomHintRequest(BaseModel):
    question_id: str
    question_text: Optional[str] = None
    # Deprecated client field (ignored by server).
    correct_answer: Optional[str] = None


class HintOut(BaseModel):
    hint: str


class ConceptOut(BaseModel):
    id: str
    name: str
    topic: str
    scope: str = "general"
    description: Optional[str] = None


class ConceptsResponse(BaseModel):
    concepts: List[ConceptOut]


class ConceptMasteryItem(BaseModel):
    concept_id: str
    concept: str
    topic: str
    scope: str = "general"
    theta: float
    response_count: int
    mastery_level: str
    exposure_count: int


class ConceptMasteryResponse(BaseModel):
    user_id: str
    concepts: List[ConceptMasteryItem]
