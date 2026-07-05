"""
PvP Room request/response models.

Covers:
  - Join/leave queue
  - Queue status
  - Match details
  - Live Redis-backed match state
  - Answer submission
  - End/forfeit match
  - Rating/leaderboard
"""

from __future__ import annotations

from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class JoinQueueRequest(BaseModel):
    """Request to join the PvP matchmaking queue."""
    user_id: str
    topic: str = "Mixed"


class JoinQueueResponse(BaseModel):
    """Response after joining the queue."""
    queue_id: str
    status: str = "waiting"
    message: str = "Searching for an opponent..."


class LeaveQueueRequest(BaseModel):
    """Request to leave the matchmaking queue."""
    user_id: str


class LeaveQueueResponse(BaseModel):
    """Confirmation of leaving the matchmaking queue."""
    success: bool
    message: str = "Left the queue"


class QueueStatusResponse(BaseModel):
    """Poll response for queue status."""
    status: Literal["waiting", "matched", "not_in_queue", "expired"]
    match_id: Optional[str] = None
    opponent_username: Optional[str] = None
    topic: Optional[str] = None
    message: str = ""


class PvPQuestionOut(BaseModel):
    """Single visible question in a PvP match.

    Correct answer is never sent here. It is only revealed after a submitted
    answer has been validated by the backend.
    """
    id: str
    text: str
    options: List[str]
    index: int


class PvPMatchOut(BaseModel):
    """Match details returned to the frontend."""
    match_id: str
    user1_id: str
    user2_id: str
    topic: str
    status: str
    total_questions: int
    questions: List[PvPQuestionOut]
    user1_score: int = 0
    user2_score: int = 0
    user1_finished: bool = False
    user2_finished: bool = False


class PvPMatchStateOut(BaseModel):
    """Live match state read from Redis first, PostgreSQL fallback second."""
    match_id: str
    status: str
    user1_score: int = 0
    user2_score: int = 0
    user1_finished: bool = False
    user2_finished: bool = False
    winner_id: Optional[str] = None


class PvPSubmitAnswerRequest(BaseModel):
    """Submit an answer for one question in an active PvP match."""
    user_id: str
    question_id: str
    question_index: int = Field(ge=0)
    answer: str = ""
    time_taken: Optional[float] = Field(default=None, ge=0)


class PvPSubmitAnswerResponse(BaseModel):
    """Response after submitting one answer."""
    is_correct: bool
    correct_answer: str
    explanation: str = ""
    your_score: int
    opponent_score: int
    questions_answered: int
    match_finished: bool = False
    next_question: Optional[PvPQuestionOut] = None


class PvPEndMatchResponse(BaseModel):
    """Final match result with Elo effect."""
    match_id: str
    winner_id: Optional[str] = None
    result: Literal["win", "loss", "draw"]
    your_score: int
    opponent_score: int
    elo_change: float
    new_elo: float
    opponent_username: str = ""


class PvPRatingOut(BaseModel):
    """User PvP rating and aggregate stats."""
    user_id: str
    elo_rating: float
    total_matches: int
    total_wins: int
    total_losses: int
    total_draws: int
    win_streak: int
    best_streak: int
    win_rate: float = 0.0


class LeaderboardEntry(BaseModel):
    """Single leaderboard entry."""
    rank: int
    user_id: str
    username: str
    elo_rating: float
    total_wins: int
    total_matches: int
    win_rate: float


class LeaderboardResponse(BaseModel):
    """Leaderboard response."""
    entries: List[LeaderboardEntry]
    total_players: int
