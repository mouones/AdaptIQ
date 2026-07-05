"""routers/governance.py - Admin governance endpoints.

Mounted under /api/admin/governance/*

Provides:
  - CRUD for DB-persisted blocked topics/keywords
  - Read-only access to governance audit logs + acceptance metrics

Internal helpers:
    - _as_iso: datetime serializer for API payloads
    - _require_admin: shared admin guard for every endpoint
"""



import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.governance_models import GovernanceBlockRule, QuestionAudit
from database.models import User, QuestionBank
from dependencies import limiter
from routers.auth import get_current_user, get_db


governance_router = APIRouter(prefix="/api/admin/governance", tags=["Admin Governance"])


# Convert datetimes to ISO8601 strings for JSON responses.
def _as_iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


# FastAPI dependency — ensures the current user has admin privileges.
# Using Depends() guarantees every endpoint that declares this parameter
# is automatically guarded, unlike a manual function call.
def require_admin(current=Depends(get_current_user)) -> User:
    user, _issued_at = current
    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


class BlockRuleCreate(BaseModel):
    kind: str = Field(..., min_length=1, max_length=20, description="topic|keyword")
    pattern: str = Field(..., min_length=1, max_length=500)
    is_active: bool = True


class BlockRuleUpdate(BaseModel):
    is_active: bool


@governance_router.get("/blocked-rules")
@limiter.limit("20/minute")
# Return blocked governance rules with optional filters.
async def list_blocked_rules(
    request: Request,
    kind: Optional[str] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):

    stmt = select(GovernanceBlockRule)
    if kind:
        stmt = stmt.where(func.lower(GovernanceBlockRule.kind) == kind.strip().lower())
    if is_active is not None:
        stmt = stmt.where(GovernanceBlockRule.is_active == bool(is_active))

    stmt = stmt.order_by(GovernanceBlockRule.kind.asc(), GovernanceBlockRule.pattern.asc())

    rules = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "id": str(r.id),
                "kind": r.kind,
                "pattern": r.pattern,
                "is_active": bool(r.is_active),
                "created_at": _as_iso(r.created_at),
                "updated_at": _as_iso(r.updated_at),
                "created_by": str(r.created_by) if r.created_by else None,
            }
            for r in rules
        ]
    }


@governance_router.post("/blocked-rules")
@limiter.limit("20/minute")
# Create a new blocked topic or keyword rule.
async def create_blocked_rule(
    request: Request,
    body: BlockRuleCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):

    kind = (body.kind or "").strip().lower()
    if kind not in {"topic", "keyword"}:
        raise HTTPException(status_code=422, detail="kind must be 'topic' or 'keyword'")

    pattern = (body.pattern or "").strip()
    if not pattern:
        raise HTTPException(status_code=422, detail="pattern must be non-empty")

    row = GovernanceBlockRule(
        id=uuid.uuid4(),
        kind=kind,
        pattern=pattern,
        is_active=bool(body.is_active),
        created_by=getattr(admin, "id", None),
    )
    db.add(row)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Rule already exists")

    await db.refresh(row)
    return {
        "id": str(row.id),
        "kind": row.kind,
        "pattern": row.pattern,
        "is_active": bool(row.is_active),
        "created_at": _as_iso(row.created_at),
    }


@governance_router.patch("/blocked-rules/{rule_id}")
@limiter.limit("20/minute")
# Update an existing blocked rule's active state.
async def update_blocked_rule(
    request: Request,
    rule_id: str,
    body: BlockRuleUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):

    try:
        rid = uuid.UUID(rule_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid rule_id")

    row = await db.get(GovernanceBlockRule, rid)
    if not row:
        raise HTTPException(status_code=404, detail="Rule not found")

    row.is_active = bool(body.is_active)
    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(row)
    await db.commit()

    return {
        "id": str(row.id),
        "kind": row.kind,
        "pattern": row.pattern,
        "is_active": bool(row.is_active),
        "updated_at": _as_iso(row.updated_at),
    }


@governance_router.delete("/blocked-rules/{rule_id}")
@limiter.limit("20/minute")
# Permanently delete a blocked rule.
async def delete_blocked_rule(
    request: Request,
    rule_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):

    try:
        rid = uuid.UUID(rule_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid rule_id")

    row = await db.get(GovernanceBlockRule, rid)
    if not row:
        raise HTTPException(status_code=404, detail="Rule not found")

    await db.delete(row)
    await db.commit()
    return {"success": True}


@governance_router.get("/audits")
@limiter.limit("20/minute")
# List governance audit records and aggregate acceptance metrics.
async def list_audits(
    request: Request,
    action: Optional[str] = Query(default=None),
    room: Optional[str] = Query(default=None),
    approved: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):

    filters = []
    if action:
        filters.append(func.lower(QuestionAudit.action) == action.strip().lower())
    if room:
        filters.append(func.lower(QuestionAudit.room) == room.strip().lower())
    if approved is not None:
        filters.append(QuestionAudit.approved == bool(approved))

    base_stmt = select(QuestionAudit)
    count_stmt = select(func.count()).select_from(QuestionAudit)

    for f in filters:
        base_stmt = base_stmt.where(f)
        count_stmt = count_stmt.where(f)

    total = await db.scalar(count_stmt) or 0

    rows = (
        await db.execute(
            base_stmt.order_by(QuestionAudit.created_at.desc()).offset(offset).limit(limit)
        )
    ).scalars().all()

    # Persist acceptance (all time)
    persist_total = await db.scalar(
        select(func.count()).select_from(QuestionAudit).where(func.lower(QuestionAudit.action) == "persist")
    ) or 0
    persist_approved = await db.scalar(
        select(func.count())
        .select_from(QuestionAudit)
        .where(func.lower(QuestionAudit.action) == "persist")
        .where(QuestionAudit.approved == True)  # noqa: E712
    ) or 0

    acceptance_rate = (float(persist_approved) / float(persist_total)) if persist_total else None

    items = []
    for r in rows:
        reasons = None
        try:
            parsed = json.loads(r.reasons_json or "")
            if isinstance(parsed, dict):
                reasons = parsed.get("reasons")
        except Exception:
            reasons = None

        items.append(
            {
                "id": str(r.id),
                "question_id": str(r.question_id) if r.question_id else None,
                "room": r.room,
                "topic": r.topic,
                "action": r.action,
                "approved": bool(r.approved),
                "confidence": r.confidence,
                "created_at": _as_iso(r.created_at),
                "reasons": reasons,
                "user_id": str(r.user_id) if getattr(r, "user_id", None) else None,
            }
        )

    return {
        "items": items,
        "total": int(total),
        "limit": int(limit),
        "offset": int(offset),
        "persist_acceptance": {
            "total": int(persist_total),
            "approved": int(persist_approved),
            "rate": acceptance_rate,
        },
    }


class MassApproveRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=50)
    sub_topic: Optional[str] = Field(default=None, max_length=50)


@governance_router.post("/mass-approve")
@limiter.limit("10/minute")
async def mass_approve_questions(
    request: Request,
    body: MassApproveRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):

    stmt = update(QuestionBank)

    if body.sub_topic:
        stmt = stmt.where(
            func.lower(QuestionBank.topic) == body.topic.strip().lower(),
            func.lower(QuestionBank.sub_topic) == body.sub_topic.strip().lower()
        )
    else:
        stmt = stmt.where(
            func.lower(QuestionBank.topic) == body.topic.strip().lower()
        )

    stmt = stmt.values(gov_approved=True, gov_safe=True)

    result = await db.execute(stmt)
    await db.commit()

    return {"success": True, "count": int(result.rowcount or 0)}


