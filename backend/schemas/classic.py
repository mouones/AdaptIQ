"""
schemas.py — Canonical Pydantic models for request/response payloads.
These are the single source of truth for API contracts.
"""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field, EmailStr, field_validator


# ═══════════════════════════════════════════════════════════════════════════
# AUTH SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════


class RegisterRequest(BaseModel):
    """User registration request."""
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator('username')
    @classmethod
    def username_alphanumeric(cls, v: str) -> str:
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError('Username must be alphanumeric (_, - allowed)')
        return v


class LoginRequest(BaseModel):
    """User login request."""
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """JWT token response."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    """Current authenticated user."""
    id: UUID
    email: str
    username: str
    points: int
    level: str
    elo_global: float
    created_at: datetime
    last_login: Optional[datetime] = None
    is_admin: bool = False


class ForgotPasswordRequest(BaseModel):
    """Forgot password request — sends OTP."""
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Reset password with OTP verification."""
    email: EmailStr
    otp: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8, max_length=128)


# ═══════════════════════════════════════════════════════════════════════════
# CLASSIC ROOM SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════


class QuestionRequest(BaseModel):
    """Generate question request."""
    topic: str
    difficulty: int = Field(default=2, ge=1, le=5)
    session_id: Optional[UUID] = None  # For getting next question in session


class OptionSchema(BaseModel):
    """MCQ option."""
    text: str
    is_correct: bool = False


class QuestionResponse(BaseModel):
    """MCQ question response."""
    id: UUID
    text: str
    options: list[str]
    correctAnswer: Optional[str] = None  # Not revealed on generation
    explanation: Optional[str] = None  # Revealed after answer
    session_id: Optional[UUID] = None  # For frontend to track session


class HintRequest(BaseModel):
    """Hint request."""
    question_id: UUID
    question_text: str
    correct_answer: Optional[str] = None


class HintResponse(BaseModel):
    """Hint response."""
    hint: str


class SubmitAnswerRequest(BaseModel):
    """Submit answer request."""
    session_id: UUID
    question_id: UUID
    selected_answer: Optional[str] = None  # Answer text (will be converted to index)
    selected_index: Optional[int] = Field(default=None, ge=-1, le=3)  # Direct option index; -1 only for timeout
    time_taken: int = Field(default=0, ge=0, le=300)
    used_hint: bool = False


class SubmitAnswerResponse(BaseModel):
    """Answer submission response."""
    success: bool
    is_correct: Optional[bool] = None
    correct_answer: Optional[str] = None
    explanation: Optional[str] = None
    new_difficulty: int
    theta_updated: Optional[float] = None
    next_question: Optional[dict] = None  # Next question or None if session ended
    session_stats: Optional[dict] = None  # {questions_answered, correct_count, is_finished}


# ═══════════════════════════════════════════════════════════════════════════
# CHALLENGE ROOM SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════


class ChallengeStatusResponse(BaseModel):
    """Challenge room user status."""
    current_rank: str
    points: int
    level: int
    elo: float
    total_wins: int
    total_losses: int
    current_streak: int


class ChallengeStartRequest(BaseModel):
    """Start challenge match."""
    topic: str


class ChallengeStartResponse(BaseModel):
    """Challenge match started."""
    match_id: UUID
    session_id: UUID
    starting_level: int


# ═══════════════════════════════════════════════════════════════════════════
# SYSTEM SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════


class ServiceStatus(BaseModel):
    """Status of a single service."""
    name: str
    status: str  # "ok", "error", "unavailable"
    latency_ms: Optional[float] = None


class HealthResponse(BaseModel):
    """Health check response."""
    status: str  # "ok", "degraded", "error"
    timestamp: datetime
    version: str
    services: dict[str, str]  # service_name -> status


# ═══════════════════════════════════════════════════════════════════════════
# STATS SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════


class ConceptMasteryLevel(BaseModel):
    """Concept mastery representation."""
    concept_id: UUID
    concept_name: str
    theta: float  # User ability for this concept
    confidence: float  # Confidence in theta estimate (0-1)
    mastery_level: str  # "Novice", "Beginner", "Intermediate", "Advanced", "Expert"
    response_count: int
    last_attempted: Optional[datetime] = None


class UserStatsResponse(BaseModel):
    """User quiz statistics."""
    total_questions: int
    correct_answers: int
    accuracy: float
    avg_difficulty_sent: float
    total_time_spent_seconds: int
    topics_attempted: list[str]
    concept_masteries: list[ConceptMasteryLevel]


# ═══════════════════════════════════════════════════════════════════════════
# ERROR SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════


class ErrorResponse(BaseModel):
    """Standard error response."""
    detail: str
    error_code: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
