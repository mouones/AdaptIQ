"""
Reset question caches and ensure baseline seed questions exist.

Usage:
    python scripts/reset_question_cache_and_seed.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import update, select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from config import DATABASE_URL, REDIS_URL
from database.models import QuestionBank
from seeds.seed import seed_all

logger = logging.getLogger(__name__)


async def _clear_redis_question_cache() -> int:
    deleted = 0
    try:
        import redis.asyncio as aioredis

        redis = await aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor=cursor, match="q_cache:*", count=500)
            if keys:
                deleted += await redis.delete(*keys)
            if cursor == 0:
                break
        await redis.aclose()
    except Exception as exc:
        logger.warning("Redis cache clear skipped: %s", exc)
    return deleted


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    engine = create_async_engine(DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        deleted = await _clear_redis_question_cache()

        async with session_factory() as session:
            await session.execute(
                update(QuestionBank).values(times_seen=0, last_served_at=None)
            )
            await session.commit()

        await seed_all(session_factory)

        async with session_factory() as session:
            q_count = (await session.execute(select(func.count(QuestionBank.id)))).scalar() or 0

        print("Question cache reset complete")
        print(f"Redis q_cache keys deleted: {deleted}")
        print(f"QuestionBank rows available: {q_count}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
