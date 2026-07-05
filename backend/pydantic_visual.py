"""
Visual Room request/response models.

This module is still imported from the backend root by routers/visual_room.py.
It mirrors the frontend visual types in frontend/src/types/visual.ts.
"""

from __future__ import annotations
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


# Question payload served to the frontend.

class VisualQuestionOut(BaseModel):
    """
    Matches the frontend VisualQuestion type.
    NOTE: correctAnswer is intentionally NOT included.
    The frontend must NOT know the correct answer before submission.
    Hints are fetched via a separate endpoint using question_id only.
    """
    id:           str
    image_url:    str
    text:         str           # the question string
    options:      List[str]     # empty list for Level 5 (text input)
    topic:        str
    level:        int
    question_type:str           # "M" or "T"
    options_count:int
    shape_svg:    Optional[str] = None   # Deprecated; kept null to avoid raw HTML injection
    shape_path:   Optional[str] = None   # Safe SVG path data only
    shape_view_box: Optional[str] = None
    show_flag:    bool = True            # True when the frontend should show the flag image.
    show_shape:   bool = False
    model_config = {"populate_by_name": True}


# POST /api/visual/start-session

class StartVisualSessionRequest(BaseModel):
    user_id: Optional[str] = None
    topic:   Literal["History", "Geography", "Mixed"]
    level:   int = Field(ge=1, le=5)


class StartVisualSessionResponse(BaseModel):
    session_id:      str
    topic:           str
    level:           int
    total_questions: int


# POST /api/visual/submit

class SubmitVisualAnswerRequest(BaseModel):
    session_id:    str
    question_id:   str
    user_id:       Optional[str] = None
    chosen_answer: str
    user_time_ms:  Optional[int] = None


class SubmitVisualAnswerResponse(BaseModel):
    is_correct:     bool
    correct_answer: str
    explanation:    str
    # next_question is null when the session is complete
    next_question:  Optional[VisualQuestionOut] = None
    current_level:  int = 1


# GET /api/visual/hint

class VisualHintResponse(BaseModel):
    hint: str


# GET /api/visual/explanation

class VisualExplanationResponse(BaseModel):
    question_id: str
    explanation: str


# Resolve Pydantic models eagerly during import.
VisualQuestionOut.model_rebuild()
StartVisualSessionRequest.model_rebuild()
StartVisualSessionResponse.model_rebuild()
SubmitVisualAnswerRequest.model_rebuild()
SubmitVisualAnswerResponse.model_rebuild()
VisualHintResponse.model_rebuild()
VisualExplanationResponse.model_rebuild()
