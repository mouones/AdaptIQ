"""
routers/chat.py
Scholar Chat — contextual learning assistant endpoint.

Endpoints:
  POST /api/chat/ask  → ChatAskResponse

The router is intentionally thin:
  - Auth via get_current_user (JWT required)
  - Rate limiting via @limiter.limit()
  - All business logic delegated to services/chat_service.py
  - App-level services read from request.app.state (same pattern
    as routers/visual_room.py)
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from dependencies import limiter
from routers.auth import get_current_user
from schemas.chat import ChatAskRequest, ChatAskResponse
from services.chat_service import handle_ask
from services.rate_limits import enforce_user_quota

logger = structlog.get_logger(__name__)

chat_router = APIRouter(prefix="/api/chat", tags=["Scholar Chat"])



# ═══════════════════════════════════════════════════════════════════════════
# POST /api/chat/ask
# ═══════════════════════════════════════════════════════════════════════════


@chat_router.post("/ask", response_model=ChatAskResponse)
@limiter.limit("20/minute")
async def ask_scholar(
    body: ChatAskRequest,
    request: Request,
    current=Depends(get_current_user),
):
    """
    Ask The Scholar a history or geography question.

    The endpoint:
      1. Authenticates the caller via JWT (401 if missing/invalid)
      2. Rate-limits to 20 requests/minute (429 if exceeded)
      3. Validates Pydantic schema (422 if malformed)
      4. Delegates to chat_service.handle_ask() which handles:
           - Topic detection
           - Scope validation  → 400 OUT_OF_SCOPE
           - RAG context retrieval (Wikipedia + Wikidata, concurrent)
           - LLM answer synthesis
      5. Returns ChatAskResponse on success

    Out-of-scope questions return HTTP 400.
    LLM/RAG unavailability returns HTTP 503.
    """
    user, _ = current
    await enforce_user_quota(request, user.id, "chat_ask", limit=120, window_seconds=3600)

    # Read app-level singletons from app.state (never create new connections)
    llm_client = getattr(request.app.state, "llm_client", None)
    rag_pipeline = getattr(request.app.state, "rag_pipeline", None)
    http_client = getattr(request.app.state, "http_client", None)

    logger.info(
        "chat.ask_scholar: user=%s topic_hint=%s question_len=%d",
        str(user.id)[:8],
        body.topic_hint,
        len(body.question),
    )

    try:
        result = await handle_ask(
            question=body.question,
            topic_hint=body.topic_hint,
            user_id=str(user.id),
            llm_client=llm_client,
            rag_pipeline=rag_pipeline,
            http_client=http_client,
        )
    except HTTPException:
        # Re-raise HTTPExceptions from the service layer as-is
        raise
    except Exception as exc:
        # Unexpected errors — log with type only (no question content at ERROR)
        logger.error(
            "chat.ask_scholar unhandled error: user=%s error_type=%s",
            str(user.id)[:8],
            type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="Internal server error")

    return result
