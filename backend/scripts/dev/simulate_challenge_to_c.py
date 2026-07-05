"""
Simulate normal Challenge Room play until target user reaches rank C.

Flow:
- Login with email/password
- Repeatedly start challenge sessions
- Generate questions and answer using the stored correct answer from DB
- End sessions and re-check rank until C or session cap

Usage:
    python scripts/dev/simulate_challenge_to_c.py
"""

from __future__ import annotations

import asyncio
import logging
import uuid
import sys
from pathlib import Path
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import DATABASE_URL
from database.models import QuestionBank

BASE_URL = "http://localhost:8000"
EMAIL = "challenge.d@example.com"
PASSWORD = "TestPass123!"
TARGET_RANK = "C"
MAX_SESSIONS = 20
QUESTIONS_PER_SESSION = 50

logger = logging.getLogger(__name__)


@dataclass
class AuthContext:
    token: str
    user_id: str


def _rank_value(rank: str) -> int:
    order = {"E": 0, "D": 1, "C": 2, "B": 3, "A": 4}
    return order.get(rank, 0)


async def _get_correct_answer(db_factory: async_sessionmaker, question_id: str) -> str:
    async with db_factory() as session:
        result = await session.execute(
            select(QuestionBank.correct_answer).where(QuestionBank.id == uuid.UUID(question_id))
        )
        answer = result.scalar_one_or_none()
        if not answer:
            raise RuntimeError(f"Question not found in DB: {question_id}")
        return str(answer)


async def _login(client: httpx.AsyncClient) -> AuthContext:
    response = await client.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
    )
    response.raise_for_status()
    data = response.json()
    token = data["access_token"]
    user_id = data["user"]["id"]
    return AuthContext(token=token, user_id=user_id)


async def _get_rank(client: httpx.AsyncClient, auth: AuthContext) -> tuple[str, int]:
    r = await client.get(
        f"{BASE_URL}/api/challenge/user/{auth.user_id}/rank",
        headers={"Authorization": f"Bearer {auth.token}"},
    )
    r.raise_for_status()
    data = r.json()
    return data["current_rank"], int(data.get("rank_points", 0))


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    db_engine = create_async_engine(DATABASE_URL, echo=False)
    db_factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with httpx.AsyncClient(timeout=30.0) as client:
        auth = await _login(client)
        rank, points = await _get_rank(client, auth)
        print(f"Start rank: {rank} ({points} pts)")

        if _rank_value(rank) >= _rank_value(TARGET_RANK):
            print("Target rank already reached")
            await db_engine.dispose()
            return

        for session_num in range(1, MAX_SESSIONS + 1):
            start = await client.post(
                f"{BASE_URL}/api/challenge/start-session",
                headers={"Authorization": f"Bearer {auth.token}"},
                json={
                    "user_id": auth.user_id,
                    "topic": "History",
                    "starting_level": 1,
                },
            )
            start.raise_for_status()
            session_id = start.json()["session_id"]

            for _ in range(QUESTIONS_PER_SESSION):
                gen = None
                for _retry in range(3):
                    gen = await client.post(
                        f"{BASE_URL}/api/challenge/generate-question",
                        headers={"Authorization": f"Bearer {auth.token}"},
                        json={
                            "user_id": auth.user_id,
                            "session_id": session_id,
                            "topic": "History",
                            "level": 5,
                        },
                    )
                    if gen.status_code == 200:
                        break
                if gen is None or gen.status_code != 200:
                    print("Question generation unavailable after retries; ending current session early")
                    break
                q_data = gen.json()
                question_id = q_data["id"]

                correct_answer = await _get_correct_answer(db_factory, question_id)

                submit = await client.post(
                    f"{BASE_URL}/api/challenge/submit-answer",
                    headers={"Authorization": f"Bearer {auth.token}"},
                    json={
                        "user_id": auth.user_id,
                        "session_id": session_id,
                        "question_id": question_id,
                        "answer": correct_answer,
                        "time_taken": 4,
                    },
                )
                if submit.status_code == 409:
                    # Duplicate question in same session; skip and continue.
                    continue
                submit.raise_for_status()

            end = await client.post(
                f"{BASE_URL}/api/challenge/session/{session_id}/end",
                headers={"Authorization": f"Bearer {auth.token}"},
            )
            end.raise_for_status()

            rank, points = await _get_rank(client, auth)
            print(f"After session {session_num}: rank={rank} points={points}")
            if _rank_value(rank) >= _rank_value(TARGET_RANK):
                print(f"Reached target rank {TARGET_RANK}")
                break

        final_rank, final_points = await _get_rank(client, auth)
        print(f"Final rank: {final_rank} ({final_points} pts)")

    await db_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
