"""AdaptIQ backend module for rate limits behavior."""

import time
from typing import Any

from fastapi import HTTPException, Request

_LOCAL_USER_QUOTA_STORE: dict[str, tuple[float, int]] = {}


def _request_state(request: Request):
    app = getattr(request, "app", None)
    return getattr(app, "state", None)


async def enforce_user_quota(
    request: Request,
    user_id: Any,
    action: str,
    *,
    limit: int,
    window_seconds: int,
) -> None:
    """Enforce a fixed-window per-user quota, Redis first with in-memory fallback."""
    if limit <= 0 or window_seconds <= 0:
        return

    uid = str(user_id)
    bucket = int(time.time() // window_seconds)
    key = f"user_quota:{action}:{uid}:{bucket}"

    state = _request_state(request)
    redis_client = getattr(state, "redis", None)
    if redis_client is not None:
        try:
            count = await redis_client.incr(key)
            if int(count) == 1:
                await redis_client.expire(key, window_seconds)
            if int(count) > limit:
                raise HTTPException(status_code=429, detail="Too many requests for this user")
            return
        except HTTPException:
            raise
        except Exception:
            # Fall through to local protection if Redis is unavailable.
            pass

    if state is not None:
        store = getattr(state, "_user_quota_store", None)
        if store is None:
            store = {}
            setattr(state, "_user_quota_store", store)
    else:
        store = _LOCAL_USER_QUOTA_STORE

    now = time.time()
    expires_at, count = store.get(key, (now + window_seconds, 0))
    if now >= expires_at:
        expires_at, count = now + window_seconds, 0
    count += 1
    store[key] = (expires_at, count)
    if count > limit:
        raise HTTPException(status_code=429, detail="Too many requests for this user")
