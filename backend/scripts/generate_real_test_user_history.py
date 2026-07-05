"""
Generate real API-backed gameplay history for all deterministic test users.

This script is intentionally non-destructive:
- It does NOT truncate/reset any database tables.
- It does NOT flush Redis or clear caches.
- It only appends normal room history via live API endpoints.

Usage (from backend/):
    python scripts/generate_real_test_user_history.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPORT_PATH = ROOT / "generated" / "real_test_user_history_report.json"
PROFILES_PATH = ROOT / "generated" / "test_users.json"


class APIError(RuntimeError):
    """Raised for unrecoverable API failures."""


@dataclass
class Profile:
    email: str
    password: str
    custom_topics: list[str]


@dataclass
class AuthSession:
    token: str
    user_id: str
    email: str


def _load_profiles() -> list[Profile]:
    if PROFILES_PATH.exists():
        raw = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
        profiles: list[Profile] = []
        for row in raw:
            topics = list((row.get("custom_topics") or {}).keys())
            profiles.append(
                Profile(
                    email=str(row.get("email", "")).strip(),
                    password=str(row.get("password", "")).strip(),
                    custom_topics=topics,
                )
            )
        return [p for p in profiles if p.email and p.password]

    # Fallback: import personas directly when generated export is absent.
    from scripts.setup_test_users import PERSONAS  # type: ignore

    fallback: list[Profile] = []
    for persona in PERSONAS:
        fallback.append(
            Profile(
                email=str(persona["email"]),
                password=str(persona["password"]),
                custom_topics=list((persona.get("custom_topics") or {}).keys()),
            )
        )
    return fallback


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    if not response.text.strip():
        return {}
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


class RealHistorySeeder:
    def __init__(self, base_url: str, timeout_seconds: float):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=timeout_seconds)

    async def close(self) -> None:
        await self.client.aclose()

    async def call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
        retries: int = 2,
    ) -> httpx.Response:
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        url = f"{self.base_url}{path}"

        for attempt in range(retries + 1):
            try:
                response = await self.client.request(
                    method=method,
                    url=url,
                    json=payload,
                    params=params,
                    headers=headers or None,
                )
            except httpx.RequestError as exc:
                if attempt < retries:
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue
                raise APIError(f"{method} {path} request error: {exc}") from exc

            if response.status_code in expected:
                return response

            if response.status_code == 429 and attempt < retries:
                retry_after = response.headers.get("Retry-After", "1")
                try:
                    wait_s = max(0.5, min(float(retry_after), 6.0))
                except ValueError:
                    wait_s = 1.0
                await asyncio.sleep(wait_s)
                continue

            if response.status_code >= 500 and attempt < retries:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue

            raise APIError(
                f"{method} {path} failed with {response.status_code}: {response.text[:300]}"
            )

        raise APIError(f"{method} {path} failed after retries")

    async def health_check(self) -> None:
        response = await self.call("GET", "/health", expected=(200,), retries=0)
        payload = _safe_json(response)
        if payload.get("status") != "ok":
            raise APIError(f"Health check returned non-ok payload: {payload}")

    async def login(self, profile: Profile) -> AuthSession:
        response = await self.call(
            "POST",
            "/api/auth/login",
            payload={"email": profile.email, "password": profile.password},
            expected=(200,),
            retries=1,
        )
        data = _safe_json(response)
        token = str(data.get("access_token", "")).strip()
        user_id = str((data.get("user") or {}).get("id", "")).strip()
        if not token or not user_id:
            raise APIError(f"Invalid login response for {profile.email}: {data}")
        return AuthSession(token=token, user_id=user_id, email=profile.email)

    async def run_classic(self, auth: AuthSession, *, questions: int, topic: str = "history") -> int:
        if questions <= 0:
            return 0

        answered = 0
        session_id: str | None = None
        current_question: dict[str, Any] | None = None
        attempts = 0
        max_attempts = max(questions * 8, 20)

        while answered < questions and attempts < max_attempts:
            attempts += 1

            if current_question is None:
                body: dict[str, Any] = {"topic": topic, "difficulty": 2}
                if session_id:
                    body["session_id"] = session_id

                q_resp = await self.call(
                    "POST",
                    "/api/rooms/classic/questions",
                    token=auth.token,
                    payload=body,
                    expected=(200, 400, 404),
                )

                if q_resp.status_code != 200:
                    session_id = None
                    current_question = None
                    continue

                q_data = _safe_json(q_resp)
                session_id = str(q_data.get("session_id", "") or "") or session_id
                q_text = str(q_data.get("text", "")).strip()
                q_options = list(q_data.get("options") or [])
                q_id = str(q_data.get("id", "")).strip()

                if not q_id:
                    continue

                # Skip placeholder fallback content; only keep real question history.
                if "quiz coming soon" in q_text.lower():
                    session_id = None
                    current_question = None
                    continue

                current_question = {
                    "id": q_id,
                    "text": q_text,
                    "options": q_options,
                }

            if current_question is None:
                continue

            selected_answer = ""
            options = list(current_question.get("options") or [])
            if options:
                selected_answer = str(options[0])

            a_resp = await self.call(
                "POST",
                "/api/rooms/classic/answers",
                token=auth.token,
                payload={
                    "session_id": session_id,
                    "question_id": current_question.get("id"),
                    "selected_answer": selected_answer,
                    "time_taken": 2,
                    "used_hint": False,
                },
                expected=(200, 400, 404, 409),
            )

            if a_resp.status_code != 200:
                session_id = None
                current_question = None
                continue

            answered += 1
            a_data = _safe_json(a_resp)
            next_q = a_data.get("next_question")
            if isinstance(next_q, dict) and next_q.get("id"):
                current_question = {
                    "id": str(next_q.get("id", "")).strip(),
                    "text": str(next_q.get("text", "")).strip(),
                    "options": list(next_q.get("options") or []),
                }
            else:
                session_id = None
                current_question = None

        return answered

    async def run_custom(self, auth: AuthSession, *, questions: int, topic: str) -> int:
        if questions <= 0:
            return 0

        start_resp = await self.call(
            "POST",
            "/api/custom/start-session",
            token=auth.token,
            payload={"user_id": auth.user_id, "topic": topic},
            expected=(200, 201),
        )
        start_data = _safe_json(start_resp)
        session_id = str(start_data.get("session_id", "")).strip()
        if not session_id:
            return 0

        answered = 0
        attempts = 0
        max_attempts = max(questions * 6, 20)

        while answered < questions and attempts < max_attempts:
            attempts += 1
            gen_resp = await self.call(
                "POST",
                "/api/custom/generate-question",
                token=auth.token,
                payload={"session_id": session_id, "topic": topic},
                expected=(200, 429, 503),
            )
            if gen_resp.status_code != 200:
                continue

            q_data = _safe_json(gen_resp)
            q_id = str(q_data.get("id", "")).strip()
            options = list(q_data.get("options") or [])
            if not q_id or not options:
                continue

            submit_resp = await self.call(
                "POST",
                "/api/custom/submit-answer",
                token=auth.token,
                payload={
                    "session_id": session_id,
                    "question_id": q_id,
                    "answer": str(options[0]),
                },
                expected=(200, 409),
            )
            if submit_resp.status_code == 200:
                answered += 1

        await self.call(
            "POST",
            f"/api/custom/session/{session_id}/end",
            token=auth.token,
            expected=(200, 404),
            retries=0,
        )

        return answered

    async def run_challenge(self, auth: AuthSession, *, questions: int, topic: str = "Mixed") -> int:
        if questions <= 0:
            return 0

        rank_resp = await self.call(
            "GET",
            f"/api/challenge/user/{auth.user_id}/rank",
            token=auth.token,
            expected=(200,),
        )
        rank_data = _safe_json(rank_resp)
        available_levels = list(rank_data.get("available_levels") or [1])
        start_level = int(available_levels[0]) if available_levels else 1

        start_resp = await self.call(
            "POST",
            "/api/challenge/start-session",
            token=auth.token,
            payload={
                "user_id": auth.user_id,
                "topic": topic,
                "starting_level": start_level,
            },
            expected=(200,),
        )
        start_data = _safe_json(start_resp)
        session_id = str(start_data.get("session_id", "")).strip()
        current_level = int(start_data.get("current_level", start_level))
        if not session_id:
            return 0

        answered = 0
        attempts = 0
        max_attempts = max(questions * 6, 20)

        while answered < questions and attempts < max_attempts:
            attempts += 1
            gen_resp = await self.call(
                "POST",
                "/api/challenge/generate-question",
                token=auth.token,
                payload={
                    "session_id": session_id,
                    "user_id": auth.user_id,
                    "topic": topic,
                    "level": current_level,
                },
                expected=(200, 400, 409, 429, 503),
            )
            if gen_resp.status_code != 200:
                if gen_resp.status_code == 400:
                    break
                continue

            q_data = _safe_json(gen_resp)
            q_id = str(q_data.get("id", "")).strip()
            options = list(q_data.get("options") or [])
            is_free_text = bool(q_data.get("is_free_text", False))

            if not q_id:
                continue

            if is_free_text:
                answer_value = "unknown"
            else:
                answer_value = str(options[0]) if options else "unknown"

            submit_resp = await self.call(
                "POST",
                "/api/challenge/submit-answer",
                token=auth.token,
                payload={
                    "session_id": session_id,
                    "question_id": q_id,
                    "user_id": auth.user_id,
                    "answer": answer_value,
                    "time_taken": 3,
                },
                expected=(200, 400, 409),
            )
            if submit_resp.status_code != 200:
                continue

            answered += 1
            submit_data = _safe_json(submit_resp)
            current_level = int(submit_data.get("new_level", current_level))

        await self.call(
            "POST",
            f"/api/challenge/session/{session_id}/end",
            token=auth.token,
            expected=(200, 400, 404),
            retries=0,
        )

        return answered


async def main() -> None:
    parser = argparse.ArgumentParser(description="Generate real room history for all deterministic test users")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Backend base URL")
    parser.add_argument("--timeout", type=float, default=25.0, help="HTTP timeout seconds")
    parser.add_argument("--classic-questions", type=int, default=2, help="Classic answers to submit per user")
    parser.add_argument("--custom-questions", type=int, default=2, help="Custom answers to submit per user")
    parser.add_argument("--challenge-questions", type=int, default=2, help="Challenge answers to submit per user")
    args = parser.parse_args()

    profiles = _load_profiles()
    if not profiles:
        raise SystemExit("No test user profiles found")

    seeder = RealHistorySeeder(base_url=args.base_url, timeout_seconds=args.timeout)
    report: dict[str, Any] = {
        "base_url": args.base_url,
        "classic_questions": args.classic_questions,
        "custom_questions": args.custom_questions,
        "challenge_questions": args.challenge_questions,
        "users": [],
        "errors": [],
    }

    try:
        await seeder.health_check()

        for profile in profiles:
            user_result: dict[str, Any] = {
                "email": profile.email,
                "classic_answered": 0,
                "custom_answered": 0,
                "challenge_answered": 0,
                "status": "ok",
            }
            try:
                auth = await seeder.login(profile)

                custom_topic = profile.custom_topics[0] if profile.custom_topics else "History - World War II"

                user_result["classic_answered"] = await seeder.run_classic(
                    auth,
                    questions=max(0, int(args.classic_questions)),
                    topic="history",
                )
                user_result["custom_answered"] = await seeder.run_custom(
                    auth,
                    questions=max(0, int(args.custom_questions)),
                    topic=custom_topic,
                )
                user_result["challenge_answered"] = await seeder.run_challenge(
                    auth,
                    questions=max(0, int(args.challenge_questions)),
                    topic="Mixed",
                )
            except Exception as exc:
                user_result["status"] = "error"
                user_result["error"] = str(exc)
                report["errors"].append(f"{profile.email}: {exc}")

            report["users"].append(user_result)
            print(
                f"[{user_result['status']}] {profile.email} "
                f"classic={user_result['classic_answered']} "
                f"custom={user_result['custom_answered']} "
                f"challenge={user_result['challenge_answered']}"
            )

    finally:
        await seeder.close()

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\nReal test-user history generation complete.")
    print(f"Report: {REPORT_PATH}")
    if report["errors"]:
        print("Errors encountered for some users; see report for details.")


if __name__ == "__main__":
    asyncio.run(main())
