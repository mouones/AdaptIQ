"""
routers/pvp.py — PvP Room endpoints.

Endpoints:
  - POST   /api/pvp/join-queue
  - DELETE /api/pvp/leave-queue
  - GET    /api/pvp/queue-status
  - GET    /api/pvp/match/{match_id}
  - GET    /api/pvp/match/{match_id}/state
  - POST   /api/pvp/match/{match_id}/answer
  - POST   /api/pvp/match/{match_id}/end
  - POST   /api/pvp/match/{match_id}/forfeit
  - GET    /api/pvp/user/{user_id}/rating
  - GET    /api/pvp/leaderboard
"""

import json
import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dependencies import limiter
from routers.auth import get_current_user, get_db, get_redis
from database.pvp_models import PvPMatchAnswer
from schemas.pvp import (
    JoinQueueRequest,
    JoinQueueResponse,
    LeaveQueueRequest,
    LeaveQueueResponse,
    QueueStatusResponse,
    PvPMatchOut,
    PvPMatchStateOut,
    PvPQuestionOut,
    PvPSubmitAnswerRequest,
    PvPSubmitAnswerResponse,
    PvPEndMatchResponse,
    PvPRatingOut,
    LeaderboardResponse,
    LeaderboardEntry,
)
from services.pvp_service import (
    join_queue,
    leave_queue,
    get_queue_status,
    get_match,
    get_match_state,
    submit_answer,
    end_match,
    forfeit_match,
    get_user_rating,
    get_leaderboard,
)

logger = logging.getLogger(__name__)
pvp_router = APIRouter(prefix="/api/pvp", tags=["PvP Room"])


def _uuid_or_422(raw: str, label: str = "ID") -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        raise HTTPException(422, f"Invalid {label}")


# ═══════════════════════════════════════════════════════════════════════════
# Matchmaking
# ═══════════════════════════════════════════════════════════════════════════


@pvp_router.post("/join-queue", response_model=JoinQueueResponse)
@limiter.limit("10/minute")
async def join_queue_endpoint(
    request: Request,
    body: JoinQueueRequest,
    db: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis),
    current=Depends(get_current_user),
):
    user, _ = current
    if str(user.id) != body.user_id:
        raise HTTPException(403, "User ID mismatch")

    try:
        entry = await join_queue(db, user.id, body.topic, redis_client=redis_client)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return JoinQueueResponse(
        queue_id=str(entry.id),
        status=entry.status,
        message="Matched!" if entry.status == "matched" else "Searching for an opponent...",
    )


@pvp_router.delete("/leave-queue", response_model=LeaveQueueResponse)
@limiter.limit("20/minute")
async def leave_queue_endpoint(
    request: Request,
    body: LeaveQueueRequest,
    db: AsyncSession = Depends(get_db),
    current=Depends(get_current_user),
):
    user, _ = current
    if str(user.id) != body.user_id:
        raise HTTPException(403, "User ID mismatch")

    removed = await leave_queue(db, user.id)
    return LeaveQueueResponse(
        success=removed,
        message="Left the queue" if removed else "Not in queue",
    )


@pvp_router.get("/queue-status", response_model=QueueStatusResponse)
@limiter.limit("60/minute")
async def queue_status_endpoint(
    request: Request,
    user_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis),
    current=Depends(get_current_user),
):
    user, _ = current
    if str(user.id) != user_id:
        raise HTTPException(403, "User ID mismatch")

    status = await get_queue_status(db, user.id, redis_client=redis_client)
    return QueueStatusResponse(**status)


# ═══════════════════════════════════════════════════════════════════════════
# Match gameplay
# ═══════════════════════════════════════════════════════════════════════════


@pvp_router.get("/match/{match_id}", response_model=PvPMatchOut)
@limiter.limit("60/minute")
async def get_match_endpoint(
    request: Request,
    match_id: str,
    db: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis),
    current=Depends(get_current_user),
):
    user, _ = current
    mid = _uuid_or_422(match_id, "match ID")

    match = await get_match(db, mid)
    if not match:
        raise HTTPException(404, "Match not found")
    if user.id not in (match.user1_id, match.user2_id):
        raise HTTPException(403, "You are not in this match")

    # Get live Redis state and only reveal this user's next unanswered question.
    live = await get_match_state(db, mid, user.id, redis_client=redis_client)

    raw_questions = json.loads(match.questions_json or "[]")
    answered_count = await db.scalar(
        select(func.count()).select_from(PvPMatchAnswer).where(
            PvPMatchAnswer.match_id == mid,
            PvPMatchAnswer.user_id == user.id,
        )
    )
    answered_count = int(answered_count or 0)

    questions: list[PvPQuestionOut] = []
    if answered_count < len(raw_questions):
        current_question = raw_questions[answered_count]
        current_options = current_question.get("options") if isinstance(current_question.get("options"), list) else []
        questions = [
            PvPQuestionOut(
                id=str(current_question.get("id", "")),
                text=str(current_question.get("text", "")),
                options=[str(opt) for opt in current_options],
                index=int(current_question.get("index", answered_count)),
            )
        ]

    return PvPMatchOut(
        match_id=str(match.id),
        user1_id=str(match.user1_id),
        user2_id=str(match.user2_id),
        topic=match.topic,
        status=live["status"],
        total_questions=match.total_questions,
        questions=questions,
        user1_score=live["user1_score"],
        user2_score=live["user2_score"],
        user1_finished=live["user1_finished"],
        user2_finished=live["user2_finished"],
    )


@pvp_router.get("/match/{match_id}/state", response_model=PvPMatchStateOut)
@limiter.limit("120/minute")
async def get_match_state_endpoint(
    request: Request,
    match_id: str,
    db: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis),
    current=Depends(get_current_user),
):
    user, _ = current
    mid = _uuid_or_422(match_id, "match ID")

    try:
        state = await get_match_state(db, mid, user.id, redis_client=redis_client)
    except ValueError as e:
        message = str(e)
        if "not found" in message.lower():
            raise HTTPException(404, message)
        if "not in this match" in message.lower():
            raise HTTPException(403, message)
        raise HTTPException(400, message)

    return PvPMatchStateOut(**state)


@pvp_router.post("/match/{match_id}/answer", response_model=PvPSubmitAnswerResponse)
@limiter.limit("30/minute")
async def submit_answer_endpoint(
    request: Request,
    match_id: str,
    body: PvPSubmitAnswerRequest,
    db: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis),
    current=Depends(get_current_user),
):
    user, _ = current
    if str(user.id) != body.user_id:
        raise HTTPException(403, "User ID mismatch")

    mid = _uuid_or_422(match_id, "match ID")

    try:
        result = await submit_answer(
            db,
            match_id=mid,
            user_id=user.id,
            question_id=body.question_id,
            question_index=body.question_index,
            answer=body.answer,
            time_taken=body.time_taken,
            redis_client=redis_client,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    return PvPSubmitAnswerResponse(**result)


@pvp_router.post("/match/{match_id}/end", response_model=PvPEndMatchResponse)
@limiter.limit("10/minute")
async def end_match_endpoint(
    request: Request,
    match_id: str,
    db: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis),
    current=Depends(get_current_user),
):
    user, _ = current
    mid = _uuid_or_422(match_id, "match ID")

    try:
        result = await end_match(db, mid, user.id, redis_client=redis_client)
    except ValueError as e:
        message = str(e)
        if "not in this match" in message.lower():
            raise HTTPException(403, message)
        if "not found" in message.lower():
            raise HTTPException(404, message)
        raise HTTPException(400, message)

    return PvPEndMatchResponse(**result)


@pvp_router.post("/match/{match_id}/forfeit", response_model=PvPEndMatchResponse)
@limiter.limit("10/minute")
async def forfeit_match_endpoint(
    request: Request,
    match_id: str,
    db: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis),
    current=Depends(get_current_user),
):
    user, _ = current
    mid = _uuid_or_422(match_id, "match ID")

    try:
        result = await forfeit_match(db, mid, user.id, redis_client=redis_client)
    except ValueError as e:
        message = str(e)
        if "not in this match" in message.lower():
            raise HTTPException(403, message)
        if "not found" in message.lower():
            raise HTTPException(404, message)
        raise HTTPException(400, message)

    return PvPEndMatchResponse(**result)


# ═══════════════════════════════════════════════════════════════════════════
# Rating / leaderboard
# ═══════════════════════════════════════════════════════════════════════════


@pvp_router.get("/user/{user_id}/rating", response_model=PvPRatingOut)
@limiter.limit("30/minute")
async def get_rating_endpoint(
    request: Request,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current=Depends(get_current_user),
):
    uid = _uuid_or_422(user_id, "user ID")

    try:
        result = await get_user_rating(db, uid)
    except ValueError as e:
        if str(e) == "User not found":
            raise HTTPException(404, "User not found")
        raise HTTPException(400, str(e))

    return PvPRatingOut(**result)


@pvp_router.get("/leaderboard", response_model=LeaderboardResponse)
@limiter.limit("20/minute")
async def get_leaderboard_endpoint(
    request: Request,
    limit: int = Query(default=20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current=Depends(get_current_user),
):
    result = await get_leaderboard(db, limit)
    return LeaderboardResponse(
        entries=[LeaderboardEntry(**e) for e in result["entries"]],
        total_players=result["total_players"],
    )
