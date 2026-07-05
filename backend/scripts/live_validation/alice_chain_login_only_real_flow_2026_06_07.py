"""
Alice/Chain login-only live API validation runner.

This script is designed for evidence collection.

It does NOT:
- create users
- bootstrap admin
- change email/password
- seed data
- truncate data
- write gameplay rows directly

It DOES:
- login existing Alice and Chain users
- run Classic, Challenge, Visual, Custom, and PvP through live API endpoints
- submit visible answers returned by the server
- save partial room progress even if a later endpoint fails
- refresh auth tokens before each room and after 401 failures
- record DB table deltas
- record user-related DB tracking deltas
- record observed question IDs and possible DB sources
- record Redis prefix deltas
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

try:
    import redis.asyncio as redis_async
except Exception:  # pragma: no cover
    redis_async = None

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None


BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if load_dotenv:
    load_dotenv(BACKEND_ROOT / ".env")

from config import DATABASE_URL, REDIS_URL  # noqa: E402
from database.models import Base  # noqa: E402

# Import all model modules so Base.metadata contains all SQLAlchemy tables.
import database.challenge_models  # noqa: F401,E402
import database.concept_models  # noqa: F401,E402
import database.custom_models  # noqa: F401,E402
import database.governance_models  # noqa: F401,E402
import database.onboarding_models  # noqa: F401,E402
import database.pvp_models  # noqa: F401,E402
import database.visual_models  # noqa: F401,E402


RUN_DATE = "2026-06-07"
JSON_REPORT_PATH = BACKEND_ROOT / "generated" / f"alice_chain_login_only_real_flow_{RUN_DATE}.json"
MD_REPORT_PATH = REPO_ROOT / "docs" / "reports" / f"ALICE_CHAIN_LOGIN_ONLY_REAL_FLOW_{RUN_DATE}.md"


class APIError(RuntimeError):
    """Raised for unrecoverable live API failures."""


@dataclass
class Account:
    role: str
    email: str
    password: str


@dataclass
class AuthSession:
    role: str
    token: str
    user_id: str
    email: str
    password: str


ACCOUNTS = {
    "alice": Account(
        role="alice",
        email="alice.realflow@adaptiq.dev",
        password="AliceRealFlow123!",
    ),
    "chain": Account(
        role="chain",
        email="chain.realflow@adaptiq.dev",
        password="ChainRealFlow123!",
    ),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_json(response: httpx.Response) -> dict[str, Any]:
    if not response.text.strip():
        return {}
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def serializable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serializable(v) for v in value]
    return str(value)


def choose_visible_answer(options: list[Any], step_index: int, fallback: str = "unknown") -> str:
    clean = [str(option) for option in options if str(option).strip()]
    if not clean:
        return fallback
    return clean[step_index % len(clean)]


def run_subprocess(args: list[str], cwd: Path) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        return {
            "args": args,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
    except Exception as exc:
        return {
            "args": args,
            "returncode": None,
            "error": str(exc),
            "duration_seconds": round(time.perf_counter() - started, 3),
        }


class DatabaseEvidence:
    def __init__(self) -> None:
        self.engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)

    async def close(self) -> None:
        await self.engine.dispose()

    async def snapshot(self) -> dict[str, Any]:
        async with self.engine.connect() as conn:
            tables_rows = await conn.execute(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                    """
                )
            )
            actual_tables = [str(row[0]) for row in tables_rows.fetchall()]

            column_rows = await conn.execute(
                text(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    ORDER BY table_name, ordinal_position
                    """
                )
            )

            actual_columns: dict[str, set[str]] = {}
            for table_name, column_name in column_rows.fetchall():
                actual_columns.setdefault(str(table_name), set()).add(str(column_name))

            counts: dict[str, int | None] = {}
            for table_name in actual_tables:
                quoted = '"' + table_name.replace('"', '""') + '"'
                try:
                    count_result = await conn.execute(text(f"SELECT COUNT(*) FROM {quoted}"))
                    counts[table_name] = int(count_result.scalar() or 0)
                except Exception:
                    counts[table_name] = None

            version = None
            if "alembic_version" in actual_tables:
                try:
                    version_result = await conn.execute(
                        text("SELECT version_num FROM alembic_version LIMIT 1")
                    )
                    version = version_result.scalar()
                except Exception:
                    version = None

        expected_tables = sorted(Base.metadata.tables.keys())
        expected_columns = {
            name: {column.name for column in table.columns}
            for name, table in Base.metadata.tables.items()
        }

        missing_tables = sorted(set(expected_tables) - set(actual_tables))
        extra_tables = sorted(set(actual_tables) - set(expected_tables) - {"alembic_version"})
        missing_columns = {
            table: sorted(columns - actual_columns.get(table, set()))
            for table, columns in expected_columns.items()
            if table in actual_columns and columns - actual_columns.get(table, set())
        }

        return {
            "captured_at": utc_now(),
            "database_url_shape": DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL,
            "alembic_version": str(version) if version else None,
            "expected_tables": expected_tables,
            "actual_tables": actual_tables,
            "missing_tables": missing_tables,
            "extra_tables": extra_tables,
            "missing_columns": missing_columns,
            "table_counts": counts,
        }

    async def tables_with_column(self, column_name: str) -> list[str]:
        async with self.engine.connect() as conn:
            rows = await conn.execute(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND column_name = :column_name
                    ORDER BY table_name
                    """
                ),
                {"column_name": column_name},
            )
            return [str(row[0]) for row in rows.fetchall()]

    async def columns_for_table(self, table_name: str) -> set[str]:
        async with self.engine.connect() as conn:
            rows = await conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = :table_name
                    ORDER BY ordinal_position
                    """
                ),
                {"table_name": table_name},
            )
            return {str(row[0]) for row in rows.fetchall()}

    async def question_sources(self, question_ids: list[str]) -> dict[str, Any]:
        ids = sorted({qid for qid in question_ids if qid})
        if not ids:
            return {}

        result: dict[str, Any] = {}
        async with self.engine.connect() as conn:
            tables = await self.tables_with_column("id")
            candidate_tables = [
                table
                for table in tables
                if any(keyword in table.lower() for keyword in ("question", "visual", "custom", "pvp"))
            ]

            for table_name in candidate_tables:
                columns = await self.columns_for_table(table_name)
                selected_columns = ["id"]
                for col in (
                    "topic",
                    "source",
                    "difficulty",
                    "level",
                    "question_type",
                    "created_at",
                    "updated_at",
                ):
                    if col in columns:
                        selected_columns.append(col)

                quoted_table = '"' + table_name.replace('"', '""') + '"'
                quoted_columns = ", ".join('"' + col.replace('"', '""') + '"' for col in selected_columns)
                params = {f"id{i}": qid for i, qid in enumerate(ids)}
                placeholders = ", ".join(f":id{i}" for i in range(len(ids)))

                try:
                    rows = await conn.execute(
                        text(
                            f"""
                            SELECT {quoted_columns}
                            FROM {quoted_table}
                            WHERE id::text IN ({placeholders})
                            """
                        ),
                        params,
                    )
                    for row in rows.fetchall():
                        row_dict = dict(zip(selected_columns, row))
                        qid = str(row_dict.get("id"))
                        result[qid] = {
                            "table": table_name,
                            **{
                                key: str(value)
                                for key, value in row_dict.items()
                                if key != "id" and value is not None
                            },
                        }
                except Exception:
                    continue

        return result

    async def user_related_counts(self, user_ids: list[str]) -> dict[str, Any]:
        clean_user_ids = sorted({str(user_id) for user_id in user_ids if str(user_id).strip()})
        if not clean_user_ids:
            return {}

        result: dict[str, Any] = {}
        async with self.engine.connect() as conn:
            table_rows = await conn.execute(
                text(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    ORDER BY table_name, ordinal_position
                    """
                )
            )

            columns_by_table: dict[str, list[str]] = {}
            for table_name, column_name in table_rows.fetchall():
                columns_by_table.setdefault(str(table_name), []).append(str(column_name))

            user_reference_column_names = {
                "user_id",
                "player_id",
                "player1_id",
                "player2_id",
                "winner_id",
                "loser_id",
                "created_by",
                "owner_id",
                "student_id",
                "candidate_id",
            }

            for table_name, columns in columns_by_table.items():
                matching_columns = [col for col in columns if col in user_reference_column_names]
                if not matching_columns:
                    continue

                table_result: dict[str, Any] = {}
                quoted_table = '"' + table_name.replace('"', '""') + '"'

                for column in matching_columns:
                    quoted_column = '"' + column.replace('"', '""') + '"'
                    params = {f"id{i}": user_id for i, user_id in enumerate(clean_user_ids)}
                    placeholders = ", ".join(f":id{i}" for i in range(len(clean_user_ids)))

                    try:
                        rows = await conn.execute(
                            text(
                                f"""
                                SELECT {quoted_column}::text AS user_ref, COUNT(*) AS count
                                FROM {quoted_table}
                                WHERE {quoted_column}::text IN ({placeholders})
                                GROUP BY {quoted_column}::text
                                """
                            ),
                            params,
                        )
                        column_counts = {str(user_ref): int(count) for user_ref, count in rows.fetchall()}
                        if column_counts:
                            table_result[column] = column_counts
                    except Exception:
                        continue

                if table_result:
                    result[table_name] = table_result

        return result


class RedisEvidence:
    def __init__(self) -> None:
        self.client = None

    async def connect(self) -> None:
        if redis_async is None:
            return
        try:
            self.client = redis_async.from_url(REDIS_URL, decode_responses=True)
            await self.client.ping()
        except Exception:
            self.client = None

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()

    async def prefix_snapshot(self) -> dict[str, Any]:
        if self.client is None:
            return {
                "available": False,
                "prefix_counts": {},
                "observed_at": utc_now(),
            }

        prefix_counts: dict[str, int] = {}
        total = 0
        try:
            async for key in self.client.scan_iter(match="*", count=500):
                total += 1
                prefix = self.prefix_for_key(str(key))
                prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
        except Exception as exc:
            return {
                "available": False,
                "error": str(exc),
                "prefix_counts": {},
                "observed_at": utc_now(),
            }

        return {
            "available": True,
            "observed_at": utc_now(),
            "total_keys": total,
            "prefix_counts": dict(sorted(prefix_counts.items())),
        }

    @staticmethod
    def prefix_for_key(key: str) -> str:
        parts = key.split(":")
        if len(parts) >= 2 and parts[0] in {"otp"}:
            return f"{parts[0]}:{parts[1]}"
        return parts[0] if parts else key


class RealFlowRunner:
    def __init__(self, base_url: str, timeout_seconds: float, db: DatabaseEvidence, redis: RedisEvidence) -> None:
        self.base_url = base_url.rstrip("/")
        self.request_timeout_seconds = timeout_seconds
        self.client = httpx.AsyncClient(timeout=timeout_seconds)
        self.db = db
        self.redis = redis
        self.report: dict[str, Any] = {
            "run_date": RUN_DATE,
            "started_at": utc_now(),
            "base_url": self.base_url,
            "mode": "login_only_real_api_flow_no_seed_no_direct_gameplay_writes",
            "planned_credentials": {
                role: {"email": account.email, "password": account.password}
                for role, account in ACCOUNTS.items()
            },
            "accounts": {},
            "schema_migration_status": {},
            "rooms": {},
            "pvp": {"matches": []},
            "question_tracking": {},
            "db": {},
            "redis": {},
            "errors": [],
        }

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
        timeout_note: str = "",
    ) -> httpx.Response:
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        url = f"{self.base_url}{path}"

        for attempt in range(retries + 1):
            try:
                response = await asyncio.wait_for(
                    self.client.request(
                        method,
                        url,
                        json=payload,
                        params=params,
                        headers=headers or None,
                    ),
                    timeout=self.request_timeout_seconds + 5.0,
                )
            except asyncio.TimeoutError as exc:
                if attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                detail = f"{method} {path} timed out after {self.request_timeout_seconds + 5.0:.1f}s"
                if timeout_note:
                    detail += f" ({timeout_note})"
                raise APIError(detail) from exc
            except httpx.RequestError as exc:
                if attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                detail = f"{method} {path} request error: {exc}"
                if timeout_note:
                    detail += f" ({timeout_note})"
                raise APIError(detail) from exc

            if response.status_code in expected:
                return response

            if response.status_code == 429 and attempt < retries:
                retry_after = response.headers.get("Retry-After", "1")
                try:
                    wait_s = max(0.8, min(float(retry_after), 12.0))
                except ValueError:
                    wait_s = 2.0
                await asyncio.sleep(wait_s)
                continue

            if response.status_code >= 500 and attempt < retries:
                await asyncio.sleep(1.0 * (attempt + 1))
                continue

            raise APIError(f"{method} {path} failed with {response.status_code}: {response.text[:500]}")

        raise APIError(f"{method} {path} failed after retries")

    async def call_with_refresh(
        self,
        auth: AuthSession,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
        retries: int = 2,
        refresh_retries: int = 1,
    ) -> httpx.Response:
        for refresh_attempt in range(refresh_retries + 1):
            try:
                return await self.call(
                    method,
                    path,
                    token=auth.token,
                    payload=payload,
                    params=params,
                    expected=expected,
                    retries=retries,
                )
            except APIError as exc:
                message = str(exc)
                if " 401:" in message or "Invalid token" in message or "Not authenticated" in message:
                    if refresh_attempt < refresh_retries:
                        await self.refresh_auth_in_place(auth)
                        continue
                raise
        raise APIError(f"{method} {path} failed after auth refresh retries")

    async def health_check(self) -> dict[str, Any]:
        response = await self.call("GET", "/health", expected=(200,), retries=0)
        return safe_json(response)

    async def login(self, account: Account) -> AuthSession:
        response = await self.call(
            "POST",
            "/api/auth/login",
            payload={"email": account.email, "password": account.password},
            expected=(200,),
            retries=1,
        )
        data = safe_json(response)
        token = str(data.get("access_token", "")).strip()
        user = data.get("user") or {}
        user_id = str(user.get("id", "")).strip()
        email = str(user.get("email", account.email)).strip().lower()

        if not token or not user_id:
            raise APIError(f"Login succeeded but token/user_id missing for {account.role}: {data}")

        self.report["accounts"][account.role] = {
            "email": email,
            "password": account.password,
            "user_id": user_id,
            "login_status": "logged_in_existing_user",
            "last_login_at": utc_now(),
        }

        return AuthSession(
            role=account.role,
            token=token,
            user_id=user_id,
            email=email,
            password=account.password,
        )

    async def refresh_auth(self, auth: AuthSession) -> AuthSession:
        return await self.login(ACCOUNTS[auth.role])

    async def refresh_auth_in_place(self, auth: AuthSession) -> None:
        fresh = await self.refresh_auth(auth)
        auth.token = fresh.token
        auth.user_id = fresh.user_id
        auth.email = fresh.email
        auth.password = fresh.password
        self.report.setdefault("auth_refreshes", []).append(
            {"role": auth.role, "at": utc_now(), "reason": "401_or_pre_room_refresh"}
        )

    async def fetch_custom_topics(self, auth: AuthSession) -> list[str]:
        response = await self.call_with_refresh(auth, "GET", "/api/custom/topics", expected=(200,))
        topics = safe_json(response).get("topics") or []
        selected: list[str] = []

        for category in ("history", "geography"):
            for item in topics:
                haystack = " ".join(
                    str(item.get(key, ""))
                    for key in ("type", "slug", "name", "description")
                ).lower()
                name = str(item.get("name", "")).strip()
                if category in haystack and name and name not in selected:
                    selected.append(name)
                    break

        return selected

    async def run_classic(self, auth: AuthSession, target_total: int) -> dict[str, Any]:
        allocations = [
            ("history", target_total // 3),
            ("geography", target_total // 3),
            ("mix", target_total - 2 * (target_total // 3)),
        ]
        return await self._run_classic_allocations(auth, allocations, target_total)

    async def _run_classic_allocations(
        self,
        auth: AuthSession,
        allocations: list[tuple[str, int]],
        target_total: int,
    ) -> dict[str, Any]:
        room: dict[str, Any] = {"target_total": target_total, "answered": 0, "steps": [], "errors": []}
        self.report["rooms"].setdefault(auth.role, {})["classic"] = room

        try:
            for topic, quota in allocations:
                session_id: str | None = None
                current_question: dict[str, Any] | None = None
                topic_answered = 0
                attempts = 0

                while topic_answered < quota and room["answered"] < target_total and attempts < max(quota * 8, 24):
                    attempts += 1

                    if current_question is None:
                        body: dict[str, Any] = {"topic": topic, "difficulty": 2}
                        if session_id:
                            body["session_id"] = session_id

                        q_resp = await self.call_with_refresh(
                            auth,
                            "POST",
                            "/api/rooms/classic/questions",
                            payload=body,
                            expected=(200, 400, 404),
                        )

                        if q_resp.status_code != 200:
                            room["errors"].append(
                                {"topic": topic, "status_code": q_resp.status_code, "response": safe_json(q_resp)}
                            )
                            session_id = None
                            current_question = None
                            continue

                        q_data = safe_json(q_resp)
                        if "quiz coming soon" in str(q_data.get("text", "")).lower():
                            session_id = None
                            current_question = None
                            continue

                        session_id = str(q_data.get("session_id") or session_id or "")
                        current_question = q_data

                    options = list(current_question.get("options") or [])
                    submitted = choose_visible_answer(options, int(room["answered"]))

                    a_resp = await self.call_with_refresh(
                        auth,
                        "POST",
                        "/api/rooms/classic/answers",
                        payload={
                            "session_id": session_id,
                            "question_id": current_question.get("id"),
                            "selected_answer": submitted,
                            "time_taken": 2,
                            "used_hint": False,
                        },
                        expected=(200, 400, 404, 409),
                    )

                    if a_resp.status_code != 200:
                        room["errors"].append(
                            {"topic": topic, "status_code": a_resp.status_code, "response": safe_json(a_resp)}
                        )
                        session_id = None
                        current_question = None
                        continue

                    a_data = safe_json(a_resp)
                    room["answered"] += 1
                    topic_answered += 1

                    room["steps"].append(
                        self.step_record(
                            "classic",
                            auth,
                            topic,
                            current_question,
                            submitted,
                            a_data,
                            extra={"session_id": session_id, "topic_answered": topic_answered},
                        )
                    )

                    if int(room["answered"]) % 5 == 0 or int(room["answered"]) == target_total:
                        print(f"[{auth.role}] classic {room['answered']}/{target_total}", flush=True)

                    next_q = a_data.get("next_question")
                    current_question = next_q if isinstance(next_q, dict) and next_q.get("id") else None
                    if current_question is None:
                        session_id = None

        except Exception as exc:
            room["errors"].append({"stage": "run_classic", "error": str(exc)})
            self.report["errors"].append(f"{auth.role} classic failed: {exc}")
        finally:
            self.report["rooms"].setdefault(auth.role, {})["classic"] = room

        return room

    async def run_challenge(self, auth: AuthSession, target_total: int) -> dict[str, Any]:
        room: dict[str, Any] = {"target_total": target_total, "answered": 0, "steps": [], "errors": []}
        self.report["rooms"].setdefault(auth.role, {})["challenge"] = room
        session_id = ""

        try:
            rank_resp = await self.call_with_refresh(
                auth,
                "GET",
                f"/api/challenge/user/{auth.user_id}/rank",
                expected=(200,),
            )
            rank_data = safe_json(rank_resp)
            start_level = int((rank_data.get("available_levels") or [1])[0] or 1)

            start_resp = await self.call_with_refresh(
                auth,
                "POST",
                "/api/challenge/start-session",
                payload={"user_id": auth.user_id, "topic": "Mixed", "starting_level": start_level},
                expected=(200,),
            )
            start_data = safe_json(start_resp)
            session_id = str(start_data.get("session_id", "")).strip()
            current_level = int(start_data.get("current_level", start_level) or start_level)
            attempts = 0

            while room["answered"] < target_total and attempts < max(target_total * 6, 60):
                attempts += 1

                q_resp = await self.call_with_refresh(
                    auth,
                    "POST",
                    "/api/challenge/generate-question",
                    payload={
                        "session_id": session_id,
                        "user_id": auth.user_id,
                        "topic": "Mixed",
                        "level": current_level,
                    },
                    expected=(200, 400, 409, 429, 503),
                )

                if q_resp.status_code != 200:
                    room["errors"].append({"status_code": q_resp.status_code, "response": safe_json(q_resp)})
                    if q_resp.status_code == 400:
                        break
                    continue

                q_data = safe_json(q_resp)
                options = list(q_data.get("options") or [])
                submitted = "unknown" if q_data.get("is_free_text") else choose_visible_answer(options, int(room["answered"]))

                a_resp = await self.call_with_refresh(
                    auth,
                    "POST",
                    "/api/challenge/submit-answer",
                    payload={
                        "session_id": session_id,
                        "question_id": q_data.get("id"),
                        "user_id": auth.user_id,
                        "answer": submitted,
                        "time_taken": 3,
                    },
                    expected=(200, 400, 409),
                )

                if a_resp.status_code != 200:
                    room["errors"].append({"status_code": a_resp.status_code, "response": safe_json(a_resp)})
                    continue

                a_data = safe_json(a_resp)
                room["answered"] += 1
                current_level = int(a_data.get("new_level", current_level) or current_level)

                room["steps"].append(
                    self.step_record(
                        "challenge",
                        auth,
                        "Mixed",
                        q_data,
                        submitted,
                        a_data,
                        extra={"session_id": session_id, "level": current_level},
                    )
                )

                if int(room["answered"]) % 5 == 0 or int(room["answered"]) == target_total:
                    print(f"[{auth.role}] challenge {room['answered']}/{target_total}", flush=True)

        except Exception as exc:
            room["errors"].append({"stage": "run_challenge", "error": str(exc)})
            self.report["errors"].append(f"{auth.role} challenge failed: {exc}")
        finally:
            if session_id:
                try:
                    await self.call_with_refresh(
                        auth,
                        "POST",
                        f"/api/challenge/session/{session_id}/end",
                        expected=(200, 400, 404),
                        retries=0,
                    )
                except Exception as exc:
                    room["errors"].append({"stage": "end_challenge", "error": str(exc)})
            self.report["rooms"].setdefault(auth.role, {})["challenge"] = room

        return room

    async def run_visual(self, auth: AuthSession, target_total: int) -> dict[str, Any]:
        room: dict[str, Any] = {"target_total": target_total, "answered": 0, "steps": [], "errors": []}
        self.report["rooms"].setdefault(auth.role, {})["visual"] = room
        session_specs = [
            ("History", 1),
            ("Geography", 2),
            ("Mixed", 3),
            ("History", 4),
            ("Geography", 5),
            ("Mixed", 1),
            ("History", 2),
            ("Geography", 3),
            ("Mixed", 4),
            ("History", 5),
            ("Geography", 1),
            ("Mixed", 2),
        ]
        spec_index = 0

        try:
            while room["answered"] < target_total and spec_index < len(session_specs):
                topic, level = session_specs[spec_index]
                spec_index += 1
                session_id = ""

                try:
                    start_resp = await self.call_with_refresh(
                        auth,
                        "POST",
                        "/api/visual/start-session",
                        payload={"user_id": auth.user_id, "topic": topic, "level": level},
                        expected=(200,),
                    )
                    session_id = str(safe_json(start_resp).get("session_id", "")).strip()

                    q_resp = await self.call_with_refresh(
                        auth,
                        "GET",
                        "/api/visual/next",
                        params={"session_id": session_id},
                        expected=(200, 400, 404, 503),
                    )

                    if q_resp.status_code != 200:
                        room["errors"].append(
                            {"topic": topic, "level": level, "status_code": q_resp.status_code, "response": safe_json(q_resp)}
                        )
                        continue

                    current_question: dict[str, Any] | None = safe_json(q_resp)

                    while current_question and room["answered"] < target_total:
                        options = list(current_question.get("options") or [])
                        submitted = choose_visible_answer(options, int(room["answered"]), fallback="unknown")

                        a_resp = await self.call_with_refresh(
                            auth,
                            "POST",
                            "/api/visual/submit",
                            payload={
                                "session_id": session_id,
                                "question_id": current_question.get("id"),
                                "user_id": auth.user_id,
                                "chosen_answer": submitted,
                                "user_time_ms": 1800,
                            },
                            expected=(200, 400, 404),
                        )

                        if a_resp.status_code != 200:
                            room["errors"].append(
                                {"topic": topic, "level": level, "status_code": a_resp.status_code, "response": safe_json(a_resp)}
                            )
                            break

                        a_data = safe_json(a_resp)
                        room["answered"] += 1
                        room["steps"].append(
                            self.step_record(
                                "visual",
                                auth,
                                topic,
                                current_question,
                                submitted,
                                a_data,
                                extra={
                                    "session_id": session_id,
                                    "level": level,
                                    "image_url": current_question.get("image_url"),
                                },
                            )
                        )

                        if int(room["answered"]) % 5 == 0 or int(room["answered"]) == target_total:
                            print(f"[{auth.role}] visual {room['answered']}/{target_total}", flush=True)

                        next_q = a_data.get("next_question")
                        current_question = next_q if isinstance(next_q, dict) and next_q.get("id") else None

                except Exception as exc:
                    room["errors"].append({"topic": topic, "level": level, "error": str(exc)})
                finally:
                    if session_id:
                        try:
                            await self.call_with_refresh(
                                auth,
                                "POST",
                                f"/api/visual/session/{session_id}/end",
                                expected=(200, 400, 404),
                                retries=0,
                            )
                        except Exception as exc:
                            room["errors"].append({"stage": "end_visual", "session_id": session_id, "error": str(exc)})

        except Exception as exc:
            room["errors"].append({"stage": "run_visual", "error": str(exc)})
            self.report["errors"].append(f"{auth.role} visual failed: {exc}")
        finally:
            self.report["rooms"].setdefault(auth.role, {})["visual"] = room

        return room

    async def run_custom(self, auth: AuthSession, topics: list[str], questions_per_topic: int) -> dict[str, Any]:
        room: dict[str, Any] = {
            "questions_per_topic": questions_per_topic,
            "topics": topics,
            "answered": 0,
            "steps": [],
            "errors": [],
        }
        self.report["rooms"].setdefault(auth.role, {})["custom"] = room

        try:
            for topic in topics:
                session_id = ""
                try:
                    start_resp = await self.call_with_refresh(
                        auth,
                        "POST",
                        "/api/custom/start-session",
                        payload={"user_id": auth.user_id, "topic": topic},
                        expected=(200, 201),
                    )
                    session_id = str(safe_json(start_resp).get("session_id", "")).strip()
                    answered_topic = 0
                    attempts = 0

                    while answered_topic < questions_per_topic and attempts < max(questions_per_topic * 6, 24):
                        attempts += 1
                        q_resp = await self.call_with_refresh(
                            auth,
                            "POST",
                            "/api/custom/generate-question",
                            payload={"session_id": session_id, "topic": topic},
                            expected=(200, 429, 503),
                        )

                        if q_resp.status_code != 200:
                            room["errors"].append(
                                {"topic": topic, "status_code": q_resp.status_code, "response": safe_json(q_resp)}
                            )
                            continue

                        q_data = safe_json(q_resp)
                        options = list(q_data.get("options") or [])
                        submitted = choose_visible_answer(options, int(room["answered"]))

                        a_resp = await self.call_with_refresh(
                            auth,
                            "POST",
                            "/api/custom/submit-answer",
                            payload={"session_id": session_id, "question_id": q_data.get("id"), "answer": submitted},
                            expected=(200, 409),
                        )

                        if a_resp.status_code != 200:
                            room["errors"].append(
                                {"topic": topic, "status_code": a_resp.status_code, "response": safe_json(a_resp)}
                            )
                            continue

                        a_data = safe_json(a_resp)
                        answered_topic += 1
                        room["answered"] += 1
                        room["steps"].append(
                            self.step_record(
                                "custom",
                                auth,
                                topic,
                                q_data,
                                submitted,
                                a_data,
                                extra={"session_id": session_id, "topic_answered": answered_topic},
                            )
                        )

                        if int(room["answered"]) % 4 == 0:
                            print(f"[{auth.role}] custom {room['answered']} answered", flush=True)

                except Exception as exc:
                    room["errors"].append({"topic": topic, "error": str(exc)})
                finally:
                    if session_id:
                        try:
                            await self.call_with_refresh(
                                auth,
                                "POST",
                                f"/api/custom/session/{session_id}/end",
                                expected=(200, 404),
                                retries=0,
                            )
                        except Exception as exc:
                            room["errors"].append({"stage": "end_custom", "session_id": session_id, "error": str(exc)})

        except Exception as exc:
            room["errors"].append({"stage": "run_custom", "error": str(exc)})
            self.report["errors"].append(f"{auth.role} custom failed: {exc}")
        finally:
            self.report["rooms"].setdefault(auth.role, {})["custom"] = room

        return room

    async def run_pvp_matches(self, alice: AuthSession, chain: AuthSession, matches: int) -> dict[str, Any]:
        pvp = self.report["pvp"]

        for match_number in range(1, matches + 1):
            topic = ["History", "Geography", "Mixed"][(match_number - 1) % 3]
            match_report: dict[str, Any] = {"match_number": match_number, "topic": topic, "answers": []}
            pvp["matches"].append(match_report)

            try:
                for player in (alice, chain):
                    try:
                        await self.call_with_refresh(
                            player,
                            "DELETE",
                            "/api/pvp/leave-queue",
                            payload={"user_id": player.user_id},
                            expected=(200, 404),
                            retries=0,
                        )
                    except Exception:
                        pass

                await self.call_with_refresh(
                    alice,
                    "POST",
                    "/api/pvp/join-queue",
                    payload={"user_id": alice.user_id, "topic": topic},
                    expected=(200,),
                )
                await self.call_with_refresh(
                    chain,
                    "POST",
                    "/api/pvp/join-queue",
                    payload={"user_id": chain.user_id, "topic": topic},
                    expected=(200,),
                )

                match_id = await self.wait_for_pvp_match(alice)
                match_report["match_id"] = match_id

                for player in (alice, chain):
                    await self.answer_pvp_player(player, match_id, match_report)

                alice_end = await self.call_with_refresh(
                    alice,
                    "POST",
                    f"/api/pvp/match/{match_id}/end",
                    expected=(200, 400),
                )
                chain_end = await self.call_with_refresh(
                    chain,
                    "POST",
                    f"/api/pvp/match/{match_id}/end",
                    expected=(200, 400),
                )

                match_report["alice_end"] = safe_json(alice_end)
                match_report["chain_end"] = safe_json(chain_end)
                match_report["winner_id"] = match_report["alice_end"].get("winner_id") or match_report["chain_end"].get("winner_id")
                match_report["scores"] = {
                    "alice": match_report["alice_end"].get("your_score"),
                    "chain": match_report["chain_end"].get("your_score"),
                }
                match_report["elo_deltas"] = {
                    "alice": match_report["alice_end"].get("elo_change"),
                    "chain": match_report["chain_end"].get("elo_change"),
                }

                print(f"[pvp] match {match_number}/{matches} completed: {match_id}", flush=True)
                await asyncio.sleep(1.5)

            except Exception as exc:
                match_report["error"] = str(exc)
                self.report["errors"].append(f"PvP match {match_number} failed: {exc}")

        return pvp

    async def wait_for_pvp_match(self, auth: AuthSession) -> str:
        for _ in range(30):
            response = await self.call_with_refresh(
                auth,
                "GET",
                "/api/pvp/queue-status",
                params={"user_id": auth.user_id},
                expected=(200,),
                retries=0,
            )
            data = safe_json(response)
            if data.get("status") == "matched" and data.get("match_id"):
                return str(data["match_id"])
            await asyncio.sleep(0.5)
        raise APIError("Timed out waiting for PvP match")

    async def answer_pvp_player(self, auth: AuthSession, match_id: str, match_report: dict[str, Any]) -> None:
        while True:
            match_resp = await self.call_with_refresh(auth, "GET", f"/api/pvp/match/{match_id}", expected=(200,))
            match_data = safe_json(match_resp)
            questions = list(match_data.get("questions") or [])
            if not questions:
                return

            q_data = questions[0]
            options = list(q_data.get("options") or [])
            index = int(q_data.get("index", 0) or 0)
            submitted = choose_visible_answer(options, index + (0 if auth.role == "alice" else 1))

            answer_resp = await self.call_with_refresh(
                auth,
                "POST",
                f"/api/pvp/match/{match_id}/answer",
                payload={
                    "user_id": auth.user_id,
                    "question_id": q_data.get("id"),
                    "question_index": index,
                    "answer": submitted,
                    "time_taken": 2.5,
                },
                expected=(200,),
            )
            answer_data = safe_json(answer_resp)
            match_report["answers"].append(
                self.step_record(
                    "pvp",
                    auth,
                    str(match_data.get("topic", "")),
                    q_data,
                    submitted,
                    answer_data,
                    extra={"match_id": match_id, "question_index": index},
                )
            )

            if answer_data.get("match_finished"):
                return
            await asyncio.sleep(0.2)

    def step_record(
        self,
        room: str,
        auth: AuthSession,
        topic: str,
        question: dict[str, Any],
        submitted: str,
        answer_response: dict[str, Any],
        *,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "timestamp": utc_now(),
            "room": room,
            "user_role": auth.role,
            "user_id": auth.user_id,
            "topic": topic,
            "question_id": str(question.get("id", "")),
            "question": str(question.get("text", "")),
            "options": list(question.get("options") or []),
            "submitted_answer": submitted,
            "server_result": {
                "is_correct": answer_response.get("is_correct"),
                "success": answer_response.get("success"),
                "correct_answer": answer_response.get("correct_answer") or answer_response.get("correctAnswer"),
                "explanation": answer_response.get("explanation"),
                "points_change": answer_response.get("points_change"),
                "new_level": answer_response.get("new_level"),
                "your_score": answer_response.get("your_score"),
                "opponent_score": answer_response.get("opponent_score"),
                "match_finished": answer_response.get("match_finished"),
            },
            "extra": extra or {},
        }

    def collect_all_question_ids(self) -> list[str]:
        question_ids: list[str] = []
        rooms = self.report.get("rooms", {})

        for role in ("alice", "chain"):
            role_rooms = rooms.get(role, {})
            for room_name in ("classic", "challenge", "visual", "custom"):
                room = role_rooms.get(room_name, {})
                for step in room.get("steps", []):
                    qid = str(step.get("question_id", "")).strip()
                    if qid:
                        question_ids.append(qid)

        for match in self.report.get("pvp", {}).get("matches", []):
            for step in match.get("answers", []):
                qid = str(step.get("question_id", "")).strip()
                if qid:
                    question_ids.append(qid)

        return sorted(set(question_ids))

    @staticmethod
    def table_deltas(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in sorted(set(before) | set(after)):
            b = before.get(key)
            a = after.get(key)
            delta = a - b if isinstance(b, int) and isinstance(a, int) else None
            result[key] = {"before": b, "after": a, "delta": delta}
        return result

    @staticmethod
    def nested_count_deltas(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for table in sorted(set(before) | set(after)):
            before_table = before.get(table, {})
            after_table = after.get(table, {})
            table_result: dict[str, Any] = {}

            for column in sorted(set(before_table) | set(after_table)):
                before_column = before_table.get(column, {})
                after_column = after_table.get(column, {})
                column_result: dict[str, Any] = {}

                for user_id in sorted(set(before_column) | set(after_column)):
                    b = before_column.get(user_id, 0)
                    a = after_column.get(user_id, 0)
                    delta = a - b if isinstance(b, int) and isinstance(a, int) else None
                    column_result[user_id] = {"before": b, "after": a, "delta": delta}

                if column_result:
                    table_result[column] = column_result

            if table_result:
                result[table] = table_result

        return result

    async def run(
        self,
        *,
        classic: int,
        challenge: int,
        visual: int,
        custom_per_topic: int,
        pvp_matches: int,
    ) -> dict[str, Any]:
        self.report["schema_migration_status"] = {
            "alembic_current": run_subprocess([sys.executable, "-m", "alembic", "current"], BACKEND_ROOT),
            "alembic_heads": run_subprocess([sys.executable, "-m", "alembic", "heads"], BACKEND_ROOT),
        }
        self.report["db"]["before"] = await self.db.snapshot()
        self.report["redis"]["before"] = await self.redis.prefix_snapshot()
        self.report["health"] = await self.health_check()

        alice = await self.login(ACCOUNTS["alice"])
        chain = await self.login(ACCOUNTS["chain"])
        user_ids = [alice.user_id, chain.user_id]
        self.report["db"]["user_related_before"] = await self.db.user_related_counts(user_ids)

        # Use Alice to read custom topics. Refresh if needed.
        await self.refresh_auth_in_place(alice)
        custom_topics = await self.fetch_custom_topics(alice)
        self.report["rooms"]["custom_topics_selected"] = custom_topics

        for auth in (alice, chain):
            await self.refresh_auth_in_place(auth)
            print(f"[{auth.role}] classic", flush=True)
            await self.run_classic(auth, classic)

            await self.refresh_auth_in_place(auth)
            print(f"[{auth.role}] challenge", flush=True)
            await self.run_challenge(auth, challenge)

            await self.refresh_auth_in_place(auth)
            print(f"[{auth.role}] visual", flush=True)
            await self.run_visual(auth, visual)

            await self.refresh_auth_in_place(auth)
            print(f"[{auth.role}] custom", flush=True)
            await self.run_custom(auth, custom_topics, custom_per_topic)

        await self.refresh_auth_in_place(alice)
        await self.refresh_auth_in_place(chain)
        print("[pvp] starting last", flush=True)
        await self.run_pvp_matches(alice, chain, pvp_matches)

        all_question_ids = self.collect_all_question_ids()
        self.report["question_tracking"] = {
            "total_unique_question_ids": len(all_question_ids),
            "question_ids": all_question_ids,
            "sources": await self.db.question_sources(all_question_ids),
        }

        self.report["db"]["after"] = await self.db.snapshot()
        self.report["redis"]["after"] = await self.redis.prefix_snapshot()
        self.report["db"]["user_related_after"] = await self.db.user_related_counts(user_ids)

        self.report["db"]["table_count_deltas"] = self.table_deltas(
            self.report["db"]["before"].get("table_counts", {}),
            self.report["db"]["after"].get("table_counts", {}),
        )
        self.report["db"]["user_related_deltas"] = self.nested_count_deltas(
            self.report["db"].get("user_related_before", {}),
            self.report["db"].get("user_related_after", {}),
        )
        self.report["redis"]["prefix_deltas"] = self.table_deltas(
            self.report["redis"]["before"].get("prefix_counts", {}),
            self.report["redis"]["after"].get("prefix_counts", {}),
        )
        self.report["finished_at"] = utc_now()
        return self.report


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Alice/Chain Login-Only Real Flow Evidence - {RUN_DATE}")
    lines.append("")

    lines.append("## Summary")
    lines.append(f"- Mode: `{report.get('mode')}`")
    lines.append(f"- Base URL: `{report.get('base_url')}`")
    lines.append(f"- Started: `{report.get('started_at')}`")
    lines.append(f"- Finished: `{report.get('finished_at')}`")
    lines.append("- User creation: `skipped`")
    lines.append("- Bootstrap admin: `skipped`")
    lines.append("- Email/password change: `skipped`")
    lines.append(f"- Auth refreshes: `{len(report.get('auth_refreshes', []))}`")
    lines.append("")

    lines.append("## Final Account Logins")
    for role, account in report.get("accounts", {}).items():
        lines.append(
            f"- {role}: `{account.get('email')}` / `{account.get('password')}` "
            f"(user_id `{account.get('user_id')}`)"
        )
    lines.append("")

    lines.append("## Schema And Migration Status")
    db_after = report.get("db", {}).get("after", {})
    lines.append(f"- Alembic version in DB: `{db_after.get('alembic_version')}`")
    lines.append(f"- Missing tables: `{db_after.get('missing_tables')}`")
    lines.append(f"- Missing columns: `{db_after.get('missing_columns')}`")
    current = report.get("schema_migration_status", {}).get("alembic_current", {})
    heads = report.get("schema_migration_status", {}).get("alembic_heads", {})
    lines.append(f"- `alembic current` return code: `{current.get('returncode')}`")
    lines.append(f"- `alembic heads` return code: `{heads.get('returncode')}`")
    if current.get("stdout"):
        lines.append(f"- Current output: `{current.get('stdout')}`")
    if heads.get("stdout"):
        lines.append(f"- Heads output: `{heads.get('stdout')}`")
    lines.append("")

    lines.append("## Health Check")
    lines.append(f"- Response: `{report.get('health')}`")
    lines.append("")

    lines.append("## Room Summary")
    rooms = report.get("rooms", {})
    lines.append(f"- Custom topics selected: `{rooms.get('custom_topics_selected')}`")
    for role in ("alice", "chain"):
        role_rooms = rooms.get(role, {})
        lines.append(f"### {role.title()}")
        for room_name in ("classic", "challenge", "visual", "custom"):
            room = role_rooms.get(room_name, {})
            lines.append(
                f"- {room_name}: answered `{room.get('answered', 0)}` "
                f"errors `{len(room.get('errors', []))}`"
            )
    lines.append("")

    lines.append("## Room Steps")
    for role in ("alice", "chain"):
        role_rooms = rooms.get(role, {})
        lines.append(f"### {role.title()}")
        for room_name in ("classic", "challenge", "visual", "custom"):
            room = role_rooms.get(room_name, {})
            lines.append(f"#### {room_name.title()} ({room.get('answered', 0)} answered)")
            for index, step in enumerate(room.get("steps", []), start=1):
                result = step.get("server_result", {})
                lines.append(
                    f"{index}. [{step.get('topic')}] q=`{step.get('question_id')}` "
                    f"submitted=`{step.get('submitted_answer')}` correct=`{result.get('is_correct')}` "
                    f"answer=`{result.get('correct_answer')}`"
                )
                lines.append(f"   - Question: {step.get('question')}")
                lines.append(f"   - Options: {json.dumps(step.get('options', []), ensure_ascii=False)}")
                if result.get("points_change") is not None:
                    lines.append(f"   - Points change: `{result.get('points_change')}`")
                if result.get("new_level") is not None:
                    lines.append(f"   - New level: `{result.get('new_level')}`")
                if result.get("explanation"):
                    lines.append(f"   - Explanation: {result.get('explanation')}")
            if room.get("errors"):
                lines.append(f"- Errors: `{room.get('errors')}`")
            lines.append("")

    lines.append("## PvP Last")
    pvp = report.get("pvp", {})
    lines.append(f"- Matches attempted: `{len(pvp.get('matches', []))}`")
    for match in pvp.get("matches", []):
        lines.append(
            f"### Match {match.get('match_number')} `{match.get('match_id')}` "
            f"topic `{match.get('topic')}` winner `{match.get('winner_id')}`"
        )
        if match.get("error"):
            lines.append(f"- Error: `{match.get('error')}`")
            lines.append("")
            continue
        lines.append(f"- Scores: `{match.get('scores')}`")
        lines.append(f"- Elo deltas: `{match.get('elo_deltas')}`")
        for index, step in enumerate(match.get("answers", []), start=1):
            result = step.get("server_result", {})
            lines.append(
                f"{index}. {step.get('user_role')} q=`{step.get('question_id')}` "
                f"idx=`{step.get('extra', {}).get('question_index')}` "
                f"submitted=`{step.get('submitted_answer')}` correct=`{result.get('is_correct')}` "
                f"answer=`{result.get('correct_answer')}`"
            )
            lines.append(f"   - Question: {step.get('question')}")
            lines.append(f"   - Options: {json.dumps(step.get('options', []), ensure_ascii=False)}")
            if result.get("explanation"):
                lines.append(f"   - Explanation: {result.get('explanation')}")
        lines.append("")

    lines.append("## Question Tracking Evidence")
    tracking = report.get("question_tracking", {})
    lines.append(f"- Total unique question IDs observed: `{tracking.get('total_unique_question_ids')}`")
    lines.append(f"- Question sources observed: `{tracking.get('sources')}`")
    lines.append("")

    lines.append("## DB Table Count Deltas")
    any_delta = False
    for table, delta in report.get("db", {}).get("table_count_deltas", {}).items():
        if delta.get("delta"):
            any_delta = True
            lines.append(
                f"- {table}: {delta.get('before')} -> {delta.get('after')} "
                f"(delta {delta.get('delta')})"
            )
    if not any_delta:
        lines.append("- No table count deltas observed.")
    lines.append("")

    lines.append("## User-Related DB Tracking Deltas")
    user_deltas = report.get("db", {}).get("user_related_deltas", {})
    if not user_deltas:
        lines.append("- No user-related DB deltas observed.")
    else:
        for table, columns in user_deltas.items():
            table_printed = False
            table_lines: list[str] = []
            for column, users in columns.items():
                column_lines: list[str] = []
                for user_id, delta in users.items():
                    if delta.get("delta"):
                        column_lines.append(
                            f"  - `{user_id}`: {delta.get('before')} -> {delta.get('after')} "
                            f"(delta {delta.get('delta')})"
                        )
                if column_lines:
                    table_lines.append(f"- Column `{column}`:")
                    table_lines.extend(column_lines)
            if table_lines:
                table_printed = True
                lines.append(f"### {table}")
                lines.extend(table_lines)
            if not table_printed:
                continue
    lines.append("")

    lines.append("## Redis Prefix Observations")
    lines.append(f"- Before: `{report.get('redis', {}).get('before', {}).get('prefix_counts')}`")
    lines.append(f"- After: `{report.get('redis', {}).get('after', {}).get('prefix_counts')}`")
    lines.append(f"- Deltas: `{report.get('redis', {}).get('prefix_deltas')}`")
    lines.append("")

    if report.get("errors"):
        lines.append("## Errors")
        for error in report["errors"]:
            lines.append(f"- {error}")
        lines.append("")

    return "\n".join(lines) + "\n"


async def async_main() -> int:
    parser = argparse.ArgumentParser(description="Run Alice/Chain login-only real-flow evidence scenario.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--classic", type=int, default=50)
    parser.add_argument("--challenge", type=int, default=50)
    parser.add_argument("--visual", type=int, default=50)
    parser.add_argument("--custom-per-topic", type=int, default=12)
    parser.add_argument("--pvp-matches", type=int, default=7)
    args = parser.parse_args()

    db = DatabaseEvidence()
    redis = RedisEvidence()
    await redis.connect()

    runner = RealFlowRunner(args.base_url, args.timeout, db, redis)
    report: dict[str, Any] | None = None
    exit_code = 0

    try:
        report = await runner.run(
            classic=max(0, args.classic),
            challenge=max(0, args.challenge),
            visual=max(0, args.visual),
            custom_per_topic=max(0, args.custom_per_topic),
            pvp_matches=max(0, args.pvp_matches),
        )
    except Exception as exc:
        exit_code = 1
        report = runner.report
        report.setdefault("errors", []).append(str(exc))
        report["finished_at"] = utc_now()

        try:
            if "before" not in report.get("db", {}):
                report.setdefault("db", {})["before"] = await db.snapshot()
            report.setdefault("db", {})["after"] = await db.snapshot()
            report.setdefault("redis", {})["after"] = await redis.prefix_snapshot()
            report["db"]["table_count_deltas"] = runner.table_deltas(
                report["db"].get("before", {}).get("table_counts", {}),
                report["db"].get("after", {}).get("table_counts", {}),
            )
        except Exception:
            pass
    finally:
        await runner.close()
        await redis.close()
        await db.close()

    JSON_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    MD_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    JSON_REPORT_PATH.write_text(
        json.dumps(serializable(report), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    MD_REPORT_PATH.write_text(render_markdown(serializable(report)), encoding="utf-8")

    print(f"JSON report: {JSON_REPORT_PATH}")
    print(f"Markdown report: {MD_REPORT_PATH}")
    if exit_code:
        print("Run completed with errors; see reports.")
    return exit_code


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
