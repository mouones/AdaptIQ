"""Redis-backed ready queues and refill requests for room question delivery."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from config import (
    PROVIDER_429_BACKOFF_SECONDS,
    QUESTION_PREWARM_BATCH_SIZE,
    QUESTION_PREWARM_LOW_WATERMARK,
    QUESTION_READY_DIFFICULTY_BUCKETS,
    QUESTION_READY_QUEUE_TTL_SECONDS,
)
from services.monitoring import get_monitoring

REFILL_REQUESTS_KEY = "prewarm:requests"
PENDING_REFILL_KEY_PREFIX = "prewarm:pending:"
PROVIDER_BACKOFF_KEY_PREFIX = "provider_backoff:"


def _slug_token(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower())
    cleaned = cleaned.strip("-")
    return cleaned or "all"


def difficulty_bucket_for_value(value: float | int | None) -> int:
    try:
        numeric = float(value if value is not None else 3)
    except Exception:
        numeric = 3.0
    buckets = list(QUESTION_READY_DIFFICULTY_BUCKETS) or [1, 2, 3, 4, 5]
    return min(buckets, key=lambda bucket: abs(bucket - numeric))


def classic_ready_queue_key(topic: str, sub_topic: Optional[str], difficulty_value: float | int | None) -> str:
    return f"classic:ready:{_slug_token(topic)}:{_slug_token(sub_topic or 'all')}:{difficulty_bucket_for_value(difficulty_value)}"


def custom_ready_queue_key(topic_label: str, concept_id: Optional[str], difficulty_value: float | int | None) -> str:
    return f"custom:ready:{_slug_token(topic_label)}:{_slug_token(concept_id or 'none')}:{difficulty_bucket_for_value(difficulty_value)}"


def challenge_ready_queue_key(topic: str, level: int | None) -> str:
    return f"challenge:ready:{_slug_token(topic)}:{difficulty_bucket_for_value(level)}"


def prewarm_lease_key(queue_key: str) -> str:
    return f"prewarm:lease:{queue_key}"


def _pending_refill_key(queue_key: str) -> str:
    return f"{PENDING_REFILL_KEY_PREFIX}{queue_key}"


def _provider_backoff_key(provider: str) -> str:
    return f"{PROVIDER_BACKOFF_KEY_PREFIX}{_slug_token(provider)}"


@dataclass(slots=True)
class RefillRequest:
    room: str
    queue_key: str
    topic: str
    difficulty_bucket: int
    topic_family: Optional[str] = None
    sub_topic: Optional[str] = None
    concept_id: Optional[str] = None
    fact_id: Optional[str] = None
    batch_size: int = QUESTION_PREWARM_BATCH_SIZE
    min_depth: int = QUESTION_PREWARM_LOW_WATERMARK
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str | bytes | None) -> Optional["RefillRequest"]:
        if raw is None:
            return None
        try:
            payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
            return cls(
                room=str(payload.get("room") or "").strip(),
                queue_key=str(payload.get("queue_key") or "").strip(),
                topic=str(payload.get("topic") or "").strip(),
                difficulty_bucket=int(payload.get("difficulty_bucket") or 3),
                topic_family=str(payload.get("topic_family") or "").strip() or None,
                sub_topic=str(payload.get("sub_topic") or "").strip() or None,
                concept_id=str(payload.get("concept_id") or "").strip() or None,
                fact_id=str(payload.get("fact_id") or "").strip() or None,
                batch_size=max(1, int(payload.get("batch_size") or QUESTION_PREWARM_BATCH_SIZE)),
                min_depth=max(1, int(payload.get("min_depth") or QUESTION_PREWARM_LOW_WATERMARK)),
                metadata=dict(payload.get("metadata") or {}),
            )
        except Exception:
            return None


async def observe_queue_depth(redis_client, queue_key: str, *, room: Optional[str] = None) -> int:
    if redis_client is None:
        return 0
    try:
        depth = int(await redis_client.llen(queue_key))
    except Exception:
        return 0
    get_monitoring().record_queue_depth(room or "unknown", queue_key, depth)
    return depth


async def push_ready_question_id(redis_client, queue_key: str, question_id: str) -> bool:
    if redis_client is None:
        return False
    try:
        qid = str(question_id)
        await redis_client.lrem(queue_key, 0, qid)
        await redis_client.rpush(queue_key, qid)
        await redis_client.expire(queue_key, QUESTION_READY_QUEUE_TTL_SECONDS)
        return True
    except Exception:
        return False


async def pop_ready_question_id(redis_client, queue_key: str) -> Optional[str]:
    if redis_client is None:
        return None
    try:
        value = await redis_client.lpop(queue_key)
    except Exception:
        return None
    if value is None:
        return None
    return str(value)


async def request_refill(redis_client, refill: RefillRequest, *, force: bool = False) -> bool:
    if redis_client is None:
        return False
    try:
        depth = int(await redis_client.llen(refill.queue_key))
        get_monitoring().record_queue_depth(refill.room, refill.queue_key, depth)
        if not force and depth >= int(refill.min_depth):
            return False
        pending_key = _pending_refill_key(refill.queue_key)
        if not force:
            created = await redis_client.set(pending_key, "1", ex=60, nx=True)
            if not created:
                return False
        else:
            await redis_client.set(pending_key, "1", ex=60)
        await redis_client.rpush(REFILL_REQUESTS_KEY, refill.to_json())
        await redis_client.expire(REFILL_REQUESTS_KEY, QUESTION_READY_QUEUE_TTL_SECONDS)
        return True
    except Exception:
        return False


async def pop_refill_request(redis_client, *, timeout_seconds: int = 5) -> Optional[RefillRequest]:
    if redis_client is None:
        return None
    try:
        result = await redis_client.blpop(REFILL_REQUESTS_KEY, timeout=timeout_seconds)
    except Exception:
        return None
    if not result or len(result) != 2:
        return None
    request = RefillRequest.from_json(result[1])
    if request is None:
        return None
    try:
        await redis_client.delete(_pending_refill_key(request.queue_key))
    except Exception:
        pass
    return request


async def mark_provider_backoff(redis_client, provider: str, *, seconds: int = PROVIDER_429_BACKOFF_SECONDS) -> None:
    if redis_client is None:
        return
    try:
        await redis_client.setex(_provider_backoff_key(provider), max(1, int(seconds)), "1")
    except Exception:
        return


async def provider_backoff_active(redis_client, provider: str) -> bool:
    if redis_client is None:
        return False
    try:
        return bool(await redis_client.exists(_provider_backoff_key(provider)))
    except Exception:
        return False
