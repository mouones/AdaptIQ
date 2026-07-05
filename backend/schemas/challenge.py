"""
Challenge Room request/response models.

These models mirror the Challenge frontend contract in
frontend/src/types/challenge.ts and stay separate from Classic Room schemas.
"""

from __future__ import annotations
from typing import Literal, List, Optional
from pydantic import BaseModel, Field


# Shared Challenge literals.

RankLiteral = Literal["E", "D", "C", "B", "A"]
TopicType   = Literal["History", "Geography", "Mixed"]


# GET /api/challenge/user/{user_id}/rank

class UserRankOut(BaseModel):
    """What the frontend reads to show rank badge + available levels."""
    current_rank    : RankLiteral
    rank_points     : int
    available_levels: List[int]          # e.g. [1, 2, 3] for rank D
    total_sessions  : int = 0
    total_questions : int = 0

    model_config = {"populate_by_name": True}


# POST /api/challenge/start-session

class StartSessionRequest(BaseModel):
    user_id       : str
    topic         : TopicType
    starting_level: int = Field(ge=1, le=5)


class StartSessionOut(BaseModel):
    session_id      : str
    current_level   : int
    rank_points     : int               # always 0 at start
    available_levels: List[int]
    current_rank    : RankLiteral
    topic           : TopicType


# GET /api/challenge/session/{session_id}

class ChallengeSessionOut(BaseModel):
    session_id      : str
    user_id         : str
    topic           : str
    starting_level  : int
    current_level   : int
    rank_points     : int
    streak_correct  : int
    streak_wrong    : int
    total_questions : int
    correct_answers : int
    is_completed    : bool


# PATCH /api/challenge/session/{session_id}/change-level

class ChangeLevelRequest(BaseModel):
    direction: Literal["up", "down"]
    reason   : str = ""


class ChangeLevelOut(BaseModel):
    session_id : str
    new_level  : int
    direction  : str
    reason     : str


# POST /api/challenge/generate-question

class GenerateChallengeQuestionRequest(BaseModel):
    session_id: str
    user_id   : str
    topic     : TopicType
    level     : int = Field(ge=1, le=5)


class ChallengeQuestionOut(BaseModel):
    """
    Extends the classic QuestionOut with challenge-specific fields.
    correctAnswer is NOT returned to the frontend to prevent cheating.
    The answer is verified server-side on submit.
    """
    id           : str
    text         : str
    options      : List[str]
    explanation  : str
    level        : int
    points_value : int          # points if answered correctly at this level
    is_free_text : bool = False  # always False for now (MCQ only)

    model_config = {"populate_by_name": True}


# POST /api/challenge/submit-answer

class SubmitChallengeAnswerRequest(BaseModel):
    session_id  : str
    question_id : str
    user_id     : str
    answer      : str
    time_taken  : Optional[float] = None


class ForceLevelChange(BaseModel):
    direction: Literal["up", "down"]
    reason   : str


class SubmitChallengeAnswerOut(BaseModel):
    id                : Optional[str] = None
    is_correct        : bool
    correct_answer    : str
    explanation       : str
    points_change     : int         # actual signed value applied (+ or -)
    new_rank_points   : int         # session running total
    new_level         : int
    streak_correct    : int
    streak_wrong      : int
    force_level_change: Optional[ForceLevelChange] = None


# POST /api/challenge/session/{session_id}/end

class EndSessionOut(BaseModel):
    session_id          : str
    total_questions     : int
    correct_answers     : int
    total_points_earned : int
    new_rank            : RankLiteral
    new_rank_points     : int       # global cumulative after this session
    rank_changed        : bool      # True if rank letter changed


# Resolve Pydantic models eagerly during import.
UserRankOut.model_rebuild()
StartSessionRequest.model_rebuild()
StartSessionOut.model_rebuild()
ChallengeSessionOut.model_rebuild()
ChangeLevelRequest.model_rebuild()
ChangeLevelOut.model_rebuild()
GenerateChallengeQuestionRequest.model_rebuild()
ChallengeQuestionOut.model_rebuild()
SubmitChallengeAnswerRequest.model_rebuild()
SubmitChallengeAnswerOut.model_rebuild()
EndSessionOut.model_rebuild()
