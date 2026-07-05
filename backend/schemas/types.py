"""
Classic Room request/response models.

These models mirror the Classic Room frontend contract in frontend/src/types.ts.
Keep concrete runtime types here; the current Pydantic/Python combination does
not need postponed annotations for these simple models.
"""

from typing import Literal, List, Dict
from pydantic import BaseModel, Field


# Topic labels accepted by Classic Room and dashboard payloads.
TopicType = Literal["History", "Geography", "Mixed"]


# Question payload returned to the Classic Room UI.
class QuestionOut(BaseModel):
    id: str
    text: str
    options: List[str]
    correctAnswer: str
    explanation: str

    model_config = {"populate_by_name": True}


# POST /api/rooms/classic/questions
class GenerateQuestionRequest(BaseModel):
    topic: Literal["History", "Geography", "Mixed"]
    difficulty: int = Field(default=2, ge=1, le=5)
    user_id: str
    session_id: str


# POST /api/rooms/classic/hints
class GenerateHintRequest(BaseModel):
    questionText: str
    correctAnswer: str


class HintOut(BaseModel):
    hint: str


# POST /api/rooms/classic/answers
class SubmitAnswerRequest(BaseModel):
    user_id: str
    session_id: str
    question_id: str
    selected_answer: str
    time_taken: int
    used_hint: bool


class SubmitAnswerOut(BaseModel):
    success: bool = True
    updated_difficulty: int


# Internal Classic Room session snapshot.
class QuizSessionState(BaseModel):
    topic: Literal["History", "Geography", "Mixed"]
    questions: List[QuestionOut] = []
    currentIndex: int = 0
    score: int = 0
    pointsEarned: int = 0
    hintsUsed: int = 0
    startTime: int = 0
    isFinished: bool = False
    current_difficulty: int = 2


# Detailed health payload shape for admin-only diagnostics.
class HealthOut(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
    services: Dict[str, str] = {}


# Resolve Pydantic models eagerly during import.
QuestionOut.model_rebuild()
GenerateQuestionRequest.model_rebuild()
GenerateHintRequest.model_rebuild()
HintOut.model_rebuild()
SubmitAnswerRequest.model_rebuild()
SubmitAnswerOut.model_rebuild()
QuizSessionState.model_rebuild()
HealthOut.model_rebuild()
