"""
services/session.py — Redis-backed session management.

Stores per-session state:
  - current_difficulty (int 1-5, updated after each answer)
  - theta (float, IRT user ability estimate)
  - seen_question_ids (set, for deduplication)
  - session metadata (topic, start_time, score, etc.)
  - session_state (concept tracking, theta snapshots)
  - current_question (shuffled options, correct answer)

Falls back to in-memory dict if Redis is unavailable (dev mode).

Provides:
    - Base session CRUD helpers (difficulty/theta/seen ids)
    - Extended session state helpers for concept-aware flows
    - Current-question storage for server-side answer verification
    - In-process async locking utilities for race prevention
"""

from __future__ import annotations
import json
import logging
import asyncio
import time
import uuid
from typing import Optional
from contextlib import asynccontextmanager

from config import (
    SESSION_TTL_SECONDS,
    SESSION_LOCK_TTL_SECONDS,
    SESSION_LOCK_TIMEOUT_SECONDS,
    ENABLE_REDIS_SESSION_LOCK,
)

logger = logging.getLogger(__name__)

# Atomic compare-and-delete so a lock is only released by its current owner.
_REDIS_UNLOCK_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)

# ── In-memory fallback (dev/test without Redis) ───────────────────────────
_memory_store: dict[str, str] = {}
_locks: dict[str, asyncio.Lock] = {}


class SessionService:
    """
    Manages quiz session data in Redis (or in-memory fallback).
    Keys are namespaced as: session:{session_id}
    """

    def __init__(self, redis=None):
        """
        redis: aioredis.Redis instance or None (falls back to in-memory).
        """
        self._redis = redis
        self._ttl = SESSION_TTL_SECONDS

    @property
    def redis(self):
        """The underlying Redis client (or None in in-memory fallback mode)."""
        return self._redis

    # Read a base session payload from Redis or in-memory fallback.
    async def get_session(self, session_id: str) -> Optional[dict]:
        key = f"session:{session_id}"
        try:
            if self._redis:
                raw = await self._redis.get(key)
            else:
                raw = _memory_store.get(key)

            if raw is None:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"Session get failed: {e}")
            return None

    # Persist a base session payload with TTL.
    async def set_session(self, session_id: str, data: dict) -> bool:
        key = f"session:{session_id}"
        try:
            serialized = json.dumps(data)
            if self._redis:
                await self._redis.setex(key, self._ttl, serialized)
            else:
                _memory_store[key] = serialized
            return True
        except Exception as e:
            logger.warning(f"Session set failed: {e}")
            return False

    async def update_session(self, session_id: str, updates: dict) -> bool:
        """Merge updates into existing session data."""
        data = await self.get_session(session_id) or {}
        data.update(updates)
        return await self.set_session(session_id, data)

    # Get current adaptive difficulty for a session.
    async def get_difficulty(self, session_id: str) -> int:
        data = await self.get_session(session_id)
        if data is None:
            return 2  # React starts at difficulty=2
        return data.get("current_difficulty", 2)

    # Update current adaptive difficulty for a session.
    async def update_difficulty(self, session_id: str, new_difficulty: int) -> None:
        await self.update_session(session_id, {"current_difficulty": new_difficulty})

    # Get current theta estimate for a session.
    async def get_theta(self, session_id: str) -> float:
        data = await self.get_session(session_id)
        if data is None:
            return 0.0
        return data.get("theta", 0.0)

    # Update theta estimate for a session.
    async def update_theta(self, session_id: str, theta: float) -> None:
        await self.update_session(session_id, {"theta": theta})

    # Return set of question ids already served in this session.
    async def get_seen_ids(self, session_id: str) -> set[str]:
        data = await self.get_session(session_id)
        if data is None:
            return set()
        return set(data.get("seen_question_ids", []))

    # Add one question id to the session's seen set.
    async def add_seen_id(self, session_id: str, question_id: str) -> None:
        seen = await self.get_seen_ids(session_id)
        seen.add(question_id)
        await self.update_session(
            session_id, {"seen_question_ids": list(seen)}
        )

    # Initialize a fresh base session payload.
    async def initialize_session(
        self,
        session_id: str,
        user_id: str,
        topic: str,
        difficulty: int = 2,
    ) -> None:
        """Create a fresh session record."""
        import time
        await self.set_session(session_id, {
            "session_id":        session_id,
            "user_id":           user_id,
            "topic":             topic,
            "current_difficulty": difficulty,
            "theta":             0.0,
            "seen_question_ids": [],
            "question_count":    0,
            "score":             0,
            "start_time":        int(time.time() * 1000),  # ms epoch (matches React)
        })

    # Increment and return total questions served in a session.
    async def increment_question_count(self, session_id: str) -> int:
        data = await self.get_session(session_id) or {}
        count = data.get("question_count", 0) + 1
        await self.update_session(session_id, {"question_count": count})
        return count

    # Delete base session payload from storage.
    async def delete_session(self, session_id: str) -> None:
        key = f"session:{session_id}"
        try:
            if self._redis:
                await self._redis.delete(key)
            else:
                _memory_store.pop(key, None)
        except Exception as e:
            logger.warning(f"Session delete failed: {e}")

    # ─────────────────────────────────────────────────────────────────────
    # NEW: Session state for concept tracking
    # ─────────────────────────────────────────────────────────────────────

    # Store extended session state (concept ids, theta snapshots, etc.).
    async def store_session_state(self, session_id: str, state: dict) -> bool:
        """Store extended session state (concepts, theta snapshots, etc.)."""
        key = f"session_state:{session_id}"
        try:
            serialized = json.dumps(state)
            if self._redis:
                await self._redis.setex(key, self._ttl, serialized)
            else:
                _memory_store[key] = serialized
            return True
        except Exception as e:
            logger.warning(f"Session state store failed: {e}")
            return False

    # Retrieve extended session state payload.
    async def get_session_state(self, session_id: str) -> Optional[dict]:
        """Get extended session state."""
        key = f"session_state:{session_id}"
        try:
            if self._redis:
                raw = await self._redis.get(key)
            else:
                raw = _memory_store.get(key)

            if raw is None:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"Session state get failed: {e}")
            return None

    # ─────────────────────────────────────────────────────────────────────
    # NEW: Current question tracking (for shuffled options)
    # ─────────────────────────────────────────────────────────────────────

    # Persist current question payload for server-side verification.
    async def set_current_question(self, session_id: str, question_data: dict) -> bool:
        """Store current question with shuffled options."""
        key = f"current_q:{session_id}"
        try:
            serialized = json.dumps(question_data)
            if self._redis:
                await self._redis.setex(key, self._ttl, serialized)
            else:
                _memory_store[key] = serialized
            return True
        except Exception as e:
            logger.warning(f"Current question set failed: {e}")
            return False

    # Read current question payload for the session.
    async def get_current_question(self, session_id: str) -> Optional[dict]:
        """Get current question data."""
        key = f"current_q:{session_id}"
        try:
            if self._redis:
                raw = await self._redis.get(key)
            else:
                raw = _memory_store.get(key)

            if raw is None:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"Current question get failed: {e}")
            return None

    # Remove stored current question payload for the session.
    async def delete_current_question(self, session_id: str) -> None:
        """Remove current question."""
        key = f"current_q:{session_id}"
        try:
            if self._redis:
                await self._redis.delete(key)
            else:
                _memory_store.pop(key, None)
        except Exception as e:
            logger.warning(f"Current question delete failed: {e}")

    # ─────────────────────────────────────────────────────────────────────
    # NEW: Session locking (prevent race conditions)
    # ─────────────────────────────────────────────────────────────────────

    @asynccontextmanager
    # Acquire an async per-session lock for critical sections.
    async def session_lock(self, session_id: str):
        """
        Acquire lock for session (prevent concurrent answer processing).

        Usage:
            async with session_service.session_lock(session_id):
                # Answer processing code

        Uses a cross-process Redis lock (SET NX PX) when ENABLE_REDIS_SESSION_LOCK
        is set and Redis is available; otherwise an in-process asyncio lock (safe
        for a single process / dev). See QUALITY_PERF_ROADMAP_2026-07-04.md item 6.
        """
        if ENABLE_REDIS_SESSION_LOCK and self._redis is not None:
            key = f"session_lock:{session_id}"
            token = uuid.uuid4().hex
            acquired = False
            deadline = time.monotonic() + SESSION_LOCK_TIMEOUT_SECONDS
            try:
                while True:
                    ok = await self._redis.set(
                        key, token, nx=True, px=SESSION_LOCK_TTL_SECONDS * 1000
                    )
                    if ok:
                        acquired = True
                        break
                    if time.monotonic() >= deadline:
                        logger.warning(
                            "Redis session lock timeout for %s; proceeding without lock",
                            session_id,
                        )
                        break
                    await asyncio.sleep(0.05)
                yield
            finally:
                if acquired:
                    try:
                        await self._redis.eval(_REDIS_UNLOCK_LUA, 1, key, token)
                    except Exception as e:
                        logger.warning("Redis session lock release failed: %s", e)
            return

        if session_id not in _locks:
            _locks[session_id] = asyncio.Lock()

        lock = _locks[session_id]
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()
