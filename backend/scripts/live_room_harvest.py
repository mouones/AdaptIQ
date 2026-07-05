"""
High-volume live room harvester for regression diagnostics.

This script:
1) Logs in seeded users.
2) Harvests at least N questions per room flow.
3) Always answers with the first option when options exist.
4) Requests hints where the API supports hints (Classic, Custom).
5) Writes raw samples + repetition/diversity metrics to generated reports.

Run from backend folder:
    python scripts/live_room_harvest.py --target 100
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx


DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TARGET = 100

SEED_USERS = {
    "classic": ("classic.novice@example.com", "TestPass123!"),
    "challenge": ("challenge.alllevels@example.com", "TestPass123!"),
    "custom": ("custom.complete@example.com", "TestPass123!"),
    "pvp_a": ("pvp.grinder@example.com", "TestPass123!"),
    "pvp_b": ("classic.expert@example.com", "TestPass123!"),
}

FLOW_CONFIG = [
    {"name": "custom_geography_china", "room": "custom", "topic": "Geography - China", "user_key": "custom"},
    {"name": "classic_history", "room": "classic", "topic": "history", "user_key": "classic"},
    {"name": "challenge_mixed", "room": "challenge", "topic": "Mixed", "user_key": "challenge"},
    {"name": "pvp_mixed", "room": "pvp", "topic": "Mixed", "user_key": "pvp_a", "opponent_key": "pvp_b"},
]

STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are", "was", "were",
    "with", "by", "from", "as", "at", "that", "this", "which", "what", "who", "when", "where",
    "did", "does", "do", "has", "have", "had", "into", "about", "during", "after", "before",
}

CHINA_SCOPE = {
    "primary": ["china", "chinese", "beijing", "yangtze", "tibet"],
    "broader": ["east asia", "asia", "asian"],
}


class APIError(RuntimeError):
    """Raised when an API call fails and cannot be retried."""


@dataclass
class AuthSession:
    token: str
    user_id: str
    email: str


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_text(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def question_signature(text: str, words: int = 12) -> str:
    tokens = normalize_text(text).split()
    return " ".join(tokens[:words])


def extract_keywords(records: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for record in records:
        for token in normalize_text(record.get("text", "")).split():
            if len(token) < 3:
                continue
            if token in STOPWORDS:
                continue
            counter[token] += 1
    return [{"keyword": key, "count": value} for key, value in counter.most_common(limit)]


def build_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    if total == 0:
        return {
            "total_questions": 0,
            "unique_questions": 0,
            "duplicate_questions": 0,
            "duplicate_rate": 0.0,
            "hint_count": 0,
            "hint_rate": 0.0,
            "correct_count": 0,
            "correct_rate": None,
            "top_repeated_questions": [],
            "top_repeated_signatures": [],
            "top_keywords": [],
        }

    normalized = [normalize_text(r.get("text", "")) for r in records]
    normalized_counter = Counter(n for n in normalized if n)
    unique_questions = len(normalized_counter)
    duplicate_questions = total - unique_questions

    signature_counter = Counter(question_signature(r.get("text", "")) for r in records if r.get("text"))

    top_repeated_questions: list[dict[str, Any]] = []
    for norm, count in normalized_counter.most_common(10):
        if count <= 1:
            continue
        sample = next((r.get("text", "") for r in records if normalize_text(r.get("text", "")) == norm), "")
        top_repeated_questions.append({"question": sample, "count": count})

    top_repeated_signatures: list[dict[str, Any]] = []
    for sig, count in signature_counter.most_common(10):
        if count <= 1:
            continue
        top_repeated_signatures.append({"signature": sig, "count": count})

    hinted = sum(1 for r in records if bool(r.get("hint")))
    correct_known = [r for r in records if r.get("is_correct") is not None]
    correct_count = sum(1 for r in correct_known if r.get("is_correct") is True)

    return {
        "total_questions": total,
        "unique_questions": unique_questions,
        "duplicate_questions": duplicate_questions,
        "duplicate_rate": round(duplicate_questions / total, 4),
        "hint_count": hinted,
        "hint_rate": round(hinted / total, 4),
        "correct_count": correct_count,
        "correct_rate": round(correct_count / len(correct_known), 4) if correct_known else None,
        "top_repeated_questions": top_repeated_questions,
        "top_repeated_signatures": top_repeated_signatures,
        "top_keywords": extract_keywords(records),
    }


def classify_china_scope(record: dict[str, Any]) -> str:
    blob = " ".join(
        [
            str(record.get("text", "")),
            str(record.get("explanation", "")),
            " ".join(str(o) for o in record.get("options", [])),
        ]
    ).lower()
    if any(k in blob for k in CHINA_SCOPE["primary"]):
        return "primary"
    if any(k in blob for k in CHINA_SCOPE["broader"]):
        return "broader"
    return "unrelated"


def custom_china_diagnostics(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {
            "scope": {"primary": 0, "broader": 0, "unrelated": 0},
            "silk_road_mentions": 0,
            "silk_road_rate": 0.0,
        }

    scope_counter = Counter(classify_china_scope(r) for r in records)
    silk_road_mentions = 0
    for record in records:
        blob = " ".join(
            [
                str(record.get("text", "")),
                str(record.get("explanation", "")),
                " ".join(str(o) for o in record.get("options", [])),
            ]
        ).lower()
        if "silk road" in blob:
            silk_road_mentions += 1

    return {
        "scope": {
            "primary": int(scope_counter.get("primary", 0)),
            "broader": int(scope_counter.get("broader", 0)),
            "unrelated": int(scope_counter.get("unrelated", 0)),
        },
        "silk_road_mentions": silk_road_mentions,
        "silk_road_rate": round(silk_road_mentions / len(records), 4),
    }


def make_markdown_summary(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Live Room Harvest Report")
    lines.append("")
    lines.append(f"Generated: {report['generated_at']}")
    lines.append(f"Base URL: {report['base_url']}")
    lines.append(f"Target per flow: {report['target_per_flow']}")
    lines.append("")
    lines.append("| Flow | Room | Questions | Unique | Duplicate Rate | Hints | Correct Rate |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")

    for flow in report.get("flows", []):
        if flow.get("status") != "ok":
            lines.append(
                f"| {flow.get('name')} | {flow.get('room')} | 0 | 0 | n/a | 0 | n/a |"
            )
            continue
        m = flow.get("metrics", {})
        lines.append(
            "| {name} | {room} | {total} | {unique} | {dup} | {hints} | {correct} |".format(
                name=flow.get("name"),
                room=flow.get("room"),
                total=m.get("total_questions", 0),
                unique=m.get("unique_questions", 0),
                dup=m.get("duplicate_rate", 0.0),
                hints=m.get("hint_count", 0),
                correct=m.get("correct_rate", "n/a"),
            )
        )

    for flow in report.get("flows", []):
        if flow.get("name") != "custom_geography_china" or flow.get("status") != "ok":
            continue
        lines.append("")
        lines.append("## Custom China Diagnostics")
        diag = flow.get("china_diagnostics", {})
        scope = diag.get("scope", {})
        lines.append(f"- Primary scoped: {scope.get('primary', 0)}")
        lines.append(f"- Broader scoped: {scope.get('broader', 0)}")
        lines.append(f"- Unrelated: {scope.get('unrelated', 0)}")
        lines.append(f"- Silk Road mentions: {diag.get('silk_road_mentions', 0)}")
        lines.append(f"- Silk Road rate: {diag.get('silk_road_rate', 0.0)}")

        repeated = flow.get("metrics", {}).get("top_repeated_questions", [])[:10]
        if repeated:
            lines.append("")
            lines.append("### Top Repeated Questions")
            for item in repeated:
                lines.append(f"- ({item.get('count', 0)}x) {item.get('question', '')}")

    if report.get("errors"):
        lines.append("")
        lines.append("## Errors")
        for err in report["errors"]:
            lines.append(f"- {err}")

    lines.append("")
    return "\n".join(lines)


class RoomHarvester:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=timeout_seconds)

    async def close(self) -> None:
        await self.client.aclose()

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        token: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        expected: tuple[int, ...] = (200,),
        retries: int = 2,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else None
        url = f"{self.base_url}{path}"

        for attempt in range(retries + 1):
            try:
                response = await self.client.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=payload,
                )
            except httpx.RequestError as exc:
                if attempt < retries:
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue
                raise APIError(f"{method} {path} request error: {exc}") from exc

            if response.status_code in expected:
                if not response.text.strip():
                    return {}
                try:
                    return response.json()
                except ValueError:
                    return {}

            if response.status_code >= 500 and attempt < retries:
                await asyncio.sleep(0.4 * (attempt + 1))
                continue

            raise APIError(
                f"{method} {path} failed with {response.status_code}: {response.text[:300]}"
            )

        raise APIError(f"{method} {path} failed after retries")

    async def login(self, email: str, password: str) -> AuthSession:
        payload = {"email": email, "password": password}
        data = await self.request_json("POST", "/api/auth/login", payload=payload)
        token = data.get("access_token")
        user = data.get("user", {})
        user_id = user.get("id")
        if not token or not user_id:
            raise APIError(f"Invalid auth response for {email}")
        return AuthSession(token=token, user_id=user_id, email=email)

    async def run_classic(self, auth: AuthSession, topic: str, target: int, flow_name: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        session_id: Optional[str] = None
        current_question: Optional[dict[str, Any]] = None

        while len(records) < target:
            if current_question is None:
                q_body: dict[str, Any] = {"topic": topic, "difficulty": 2}
                if session_id:
                    q_body["session_id"] = session_id
                q = await self.request_json(
                    "POST",
                    "/api/rooms/classic/questions",
                    token=auth.token,
                    payload=q_body,
                )
                session_id = q.get("session_id")
                current_question = {
                    "id": str(q.get("id", "")),
                    "text": str(q.get("text", "")),
                    "options": list(q.get("options") or []),
                }

            qid = str(current_question.get("id", ""))
            qtext = str(current_question.get("text", ""))
            options = list(current_question.get("options") or [])

            hint_text: Optional[str] = None
            try:
                hint_payload = {"question_id": qid, "question_text": qtext}
                hint_data = await self.request_json(
                    "POST",
                    "/api/rooms/classic/hints",
                    token=auth.token,
                    payload=hint_payload,
                    expected=(200, 503),
                    retries=0,
                )
                hint_text = hint_data.get("hint")
            except APIError:
                hint_text = None

            selected = options[0] if options else "first_option_fallback"
            answer_payload = {
                "session_id": session_id,
                "question_id": qid,
                "selected_answer": selected,
                "time_taken": 1,
                "used_hint": bool(hint_text),
            }
            answer = await self.request_json(
                "POST",
                "/api/rooms/classic/answers",
                token=auth.token,
                payload=answer_payload,
            )

            records.append(
                {
                    "flow": flow_name,
                    "room": "classic",
                    "topic": topic,
                    "session_id": session_id,
                    "question_id": qid,
                    "text": qtext,
                    "options": options,
                    "selected_answer": selected,
                    "is_correct": answer.get("is_correct"),
                    "correct_answer": answer.get("correct_answer"),
                    "explanation": answer.get("explanation"),
                    "hint": hint_text,
                }
            )

            next_question = answer.get("next_question")
            if isinstance(next_question, dict) and next_question.get("id"):
                current_question = {
                    "id": str(next_question.get("id", "")),
                    "text": str(next_question.get("text", "")),
                    "options": list(next_question.get("options") or []),
                }
            else:
                current_question = None
                session_id = None

            if len(records) % 10 == 0:
                print(f"[{flow_name}] collected {len(records)}/{target}")

        return records

    async def run_challenge(self, auth: AuthSession, topic: str, target: int, flow_name: str) -> list[dict[str, Any]]:
        rank = await self.request_json(
            "GET",
            f"/api/challenge/user/{auth.user_id}/rank",
            token=auth.token,
        )
        available_levels = list(rank.get("available_levels") or [1])
        start_level = min(int(level) for level in available_levels) if available_levels else 1

        async def _start_new_session() -> tuple[str, int, set[str]]:
            started = await self.request_json(
                "POST",
                "/api/challenge/start-session",
                token=auth.token,
                payload={
                    "user_id": auth.user_id,
                    "topic": topic,
                    "starting_level": start_level,
                },
            )
            sid = str(started.get("session_id"))
            lvl = int(started.get("current_level") or start_level)
            return sid, lvl, set()

        session_id, current_level, answered_ids = await _start_new_session()
        records: list[dict[str, Any]] = []

        while len(records) < target:
            question = await self.request_json(
                "POST",
                "/api/challenge/generate-question",
                token=auth.token,
                payload={
                    "session_id": session_id,
                    "user_id": auth.user_id,
                    "topic": topic,
                    "level": max(1, min(5, current_level)),
                },
            )

            qid = str(question.get("id", ""))
            if qid in answered_ids:
                # Session-local answer uniqueness blocks repeated question IDs.
                # Rotate session to keep the high-volume harvest moving.
                session_id, current_level, answered_ids = await _start_new_session()
                continue

            qtext = str(question.get("text", ""))
            options = list(question.get("options") or [])
            selected = options[0] if options else "first_option_fallback"

            try:
                answer = await self.request_json(
                    "POST",
                    "/api/challenge/submit-answer",
                    token=auth.token,
                    payload={
                        "session_id": session_id,
                        "question_id": qid,
                        "user_id": auth.user_id,
                        "answer": selected,
                        "time_taken": 1.0,
                    },
                )
            except APIError as exc:
                if "already been answered" in str(exc).lower():
                    session_id, current_level, answered_ids = await _start_new_session()
                    continue
                raise

            answered_ids.add(qid)
            current_level = int(answer.get("new_level") or current_level)
            records.append(
                {
                    "flow": flow_name,
                    "room": "challenge",
                    "topic": topic,
                    "session_id": session_id,
                    "level": question.get("level"),
                    "question_id": qid,
                    "text": qtext,
                    "options": options,
                    "selected_answer": selected,
                    "is_correct": answer.get("is_correct"),
                    "correct_answer": answer.get("correct_answer"),
                    "explanation": answer.get("explanation"),
                    "hint": None,
                    "is_free_text": bool(question.get("is_free_text", False)),
                }
            )

            if len(records) % 10 == 0:
                print(f"[{flow_name}] collected {len(records)}/{target}")

        try:
            await self.request_json(
                "POST",
                f"/api/challenge/session/{session_id}/end",
                token=auth.token,
                expected=(200, 400),
                retries=0,
            )
        except APIError:
            pass

        return records

    async def run_custom(self, auth: AuthSession, topic: str, target: int, flow_name: str) -> list[dict[str, Any]]:
        started = await self.request_json(
            "POST",
            "/api/custom/start-session",
            token=auth.token,
            payload={"user_id": auth.user_id, "topic": topic},
        )
        session_id = str(started.get("session_id"))

        records: list[dict[str, Any]] = []

        while len(records) < target:
            question = await self.request_json(
                "POST",
                "/api/custom/generate-question",
                token=auth.token,
                payload={"session_id": session_id, "topic": topic},
            )

            qid = str(question.get("id", ""))
            qtext = str(question.get("text", ""))
            options = list(question.get("options") or [])

            hint_text: Optional[str] = None
            try:
                hint = await self.request_json(
                    "POST",
                    "/api/custom/generate-hint",
                    token=auth.token,
                    payload={"question_id": qid, "question_text": qtext},
                    expected=(200, 503),
                    retries=0,
                )
                hint_text = hint.get("hint")
            except APIError:
                hint_text = None

            selected = options[0] if options else "first_option_fallback"

            answer = await self.request_json(
                "POST",
                "/api/custom/submit-answer",
                token=auth.token,
                payload={
                    "session_id": session_id,
                    "question_id": qid,
                    "answer": selected,
                },
            )

            records.append(
                {
                    "flow": flow_name,
                    "room": "custom",
                    "topic": topic,
                    "session_id": session_id,
                    "question_id": qid,
                    "text": qtext,
                    "options": options,
                    "selected_answer": selected,
                    "is_correct": answer.get("is_correct"),
                    "correct_answer": answer.get("correct_answer"),
                    "explanation": answer.get("explanation"),
                    "hint": hint_text,
                    "concept_id": question.get("concept_id"),
                }
            )

            if len(records) % 10 == 0:
                print(f"[{flow_name}] collected {len(records)}/{target}")

        return records

    async def _leave_queue_if_present(self, auth: AuthSession) -> None:
        try:
            await self.request_json(
                "DELETE",
                "/api/pvp/leave-queue",
                token=auth.token,
                payload={"user_id": auth.user_id},
                expected=(200, 400),
                retries=0,
            )
        except APIError:
            return

    async def _wait_for_match(
        self,
        user_a: AuthSession,
        user_b: AuthSession,
        *,
        max_attempts: int = 50,
        poll_seconds: float = 0.3,
    ) -> str:
        for _ in range(max_attempts):
            status_a = await self.request_json(
                "GET",
                "/api/pvp/queue-status",
                token=user_a.token,
                params={"user_id": user_a.user_id},
            )
            if status_a.get("status") == "matched" and status_a.get("match_id"):
                return str(status_a["match_id"])

            status_b = await self.request_json(
                "GET",
                "/api/pvp/queue-status",
                token=user_b.token,
                params={"user_id": user_b.user_id},
            )
            if status_b.get("status") == "matched" and status_b.get("match_id"):
                return str(status_b["match_id"])

            await asyncio.sleep(poll_seconds)

        raise APIError("PvP matchmaking timed out")

    async def run_pvp(
        self,
        user_a: AuthSession,
        user_b: AuthSession,
        topic: str,
        target: int,
        flow_name: str,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        match_no = 0

        while len(records) < target:
            match_no += 1
            await self._leave_queue_if_present(user_a)
            await self._leave_queue_if_present(user_b)

            await self.request_json(
                "POST",
                "/api/pvp/join-queue",
                token=user_a.token,
                payload={"user_id": user_a.user_id, "topic": topic},
            )
            await self.request_json(
                "POST",
                "/api/pvp/join-queue",
                token=user_b.token,
                payload={"user_id": user_b.user_id, "topic": topic},
            )

            match_id = await self._wait_for_match(user_a, user_b)

            match = await self.request_json(
                "GET",
                f"/api/pvp/match/{match_id}",
                token=user_a.token,
            )
            questions = list(match.get("questions") or [])
            if not questions:
                raise APIError(f"PvP match {match_id} returned no questions")

            for q in questions:
                qid = str(q.get("id", ""))
                qtext = str(q.get("text", ""))
                qindex = int(q.get("index", 0))
                options = list(q.get("options") or [])
                selected = options[0] if options else "first_option_fallback"

                result_a = await self.request_json(
                    "POST",
                    f"/api/pvp/match/{match_id}/answer",
                    token=user_a.token,
                    payload={
                        "user_id": user_a.user_id,
                        "question_id": qid,
                        "question_index": qindex,
                        "answer": selected,
                        "time_taken": 1.0,
                    },
                )

                await self.request_json(
                    "POST",
                    f"/api/pvp/match/{match_id}/answer",
                    token=user_b.token,
                    payload={
                        "user_id": user_b.user_id,
                        "question_id": qid,
                        "question_index": qindex,
                        "answer": selected,
                        "time_taken": 1.0,
                    },
                )

                records.append(
                    {
                        "flow": flow_name,
                        "room": "pvp",
                        "topic": topic,
                        "match_id": match_id,
                        "match_number": match_no,
                        "question_id": qid,
                        "text": qtext,
                        "options": options,
                        "selected_answer": selected,
                        "is_correct": result_a.get("is_correct"),
                        "correct_answer": result_a.get("correct_answer"),
                        "explanation": result_a.get("explanation"),
                        "hint": None,
                    }
                )

            try:
                await self.request_json("POST", f"/api/pvp/match/{match_id}/end", token=user_a.token)
            except APIError:
                pass

            if len(records) % 10 == 0:
                print(f"[{flow_name}] collected {len(records)}/{target}")

        return records[:target]


async def run_harvest(base_url: str, target: int, timeout_seconds: float) -> dict[str, Any]:
    harvester = RoomHarvester(base_url=base_url, timeout_seconds=timeout_seconds)
    errors: list[str] = []
    flows_report: list[dict[str, Any]] = []
    all_records: list[dict[str, Any]] = []

    try:
        auth_cache: dict[str, AuthSession] = {}
        for key, (email, password) in SEED_USERS.items():
            auth_cache[key] = await harvester.login(email=email, password=password)
            print(f"[auth] logged in {email}")

        for flow in FLOW_CONFIG:
            flow_name = flow["name"]
            room = flow["room"]
            topic = flow["topic"]
            print(f"[start] {flow_name} target={target}")

            try:
                if room == "classic":
                    records = await harvester.run_classic(
                        auth=auth_cache[flow["user_key"]],
                        topic=topic,
                        target=target,
                        flow_name=flow_name,
                    )
                elif room == "challenge":
                    records = await harvester.run_challenge(
                        auth=auth_cache[flow["user_key"]],
                        topic=topic,
                        target=target,
                        flow_name=flow_name,
                    )
                elif room == "custom":
                    records = await harvester.run_custom(
                        auth=auth_cache[flow["user_key"]],
                        topic=topic,
                        target=target,
                        flow_name=flow_name,
                    )
                elif room == "pvp":
                    records = await harvester.run_pvp(
                        user_a=auth_cache[flow["user_key"]],
                        user_b=auth_cache[flow["opponent_key"]],
                        topic=topic,
                        target=target,
                        flow_name=flow_name,
                    )
                else:
                    raise APIError(f"Unsupported room: {room}")

                metrics = build_metrics(records)
                flow_report: dict[str, Any] = {
                    "name": flow_name,
                    "room": room,
                    "topic": topic,
                    "status": "ok",
                    "metrics": metrics,
                    "record_count": len(records),
                }
                if flow_name == "custom_geography_china":
                    flow_report["china_diagnostics"] = custom_china_diagnostics(records)

                flows_report.append(flow_report)
                all_records.extend(records)
                print(f"[done] {flow_name} collected={len(records)} unique={metrics['unique_questions']}")

            except Exception as exc:
                error_text = f"{flow_name}: {exc}"
                errors.append(error_text)
                flows_report.append(
                    {
                        "name": flow_name,
                        "room": room,
                        "topic": topic,
                        "status": "error",
                        "error": str(exc),
                    }
                )
                print(f"[error] {error_text}")

        report = {
            "generated_at": now_iso(),
            "base_url": base_url,
            "target_per_flow": target,
            "flows": flows_report,
            "errors": errors,
        }
        return {"report": report, "records": all_records}

    finally:
        await harvester.close()


async def run_harvest_selected(
    base_url: str,
    target: int,
    timeout_seconds: float,
    selected_flow_names: Optional[set[str]] = None,
) -> dict[str, Any]:
    if not selected_flow_names:
        return await run_harvest(base_url=base_url, target=target, timeout_seconds=timeout_seconds)

    selected = [flow for flow in FLOW_CONFIG if flow["name"] in selected_flow_names]
    if not selected:
        raise RuntimeError(f"No matching flows found for: {sorted(selected_flow_names)}")

    harvester = RoomHarvester(base_url=base_url, timeout_seconds=timeout_seconds)
    errors: list[str] = []
    flows_report: list[dict[str, Any]] = []
    all_records: list[dict[str, Any]] = []

    try:
        auth_cache: dict[str, AuthSession] = {}
        required_user_keys: set[str] = set()
        for flow in selected:
            required_user_keys.add(flow["user_key"])
            if flow.get("opponent_key"):
                required_user_keys.add(flow["opponent_key"])

        for user_key in required_user_keys:
            email, password = SEED_USERS[user_key]
            auth_cache[user_key] = await harvester.login(email=email, password=password)
            print(f"[auth] logged in {email}")

        for flow in selected:
            flow_name = flow["name"]
            room = flow["room"]
            topic = flow["topic"]
            print(f"[start] {flow_name} target={target}")

            try:
                if room == "classic":
                    records = await harvester.run_classic(
                        auth=auth_cache[flow["user_key"]],
                        topic=topic,
                        target=target,
                        flow_name=flow_name,
                    )
                elif room == "challenge":
                    records = await harvester.run_challenge(
                        auth=auth_cache[flow["user_key"]],
                        topic=topic,
                        target=target,
                        flow_name=flow_name,
                    )
                elif room == "custom":
                    records = await harvester.run_custom(
                        auth=auth_cache[flow["user_key"]],
                        topic=topic,
                        target=target,
                        flow_name=flow_name,
                    )
                elif room == "pvp":
                    records = await harvester.run_pvp(
                        user_a=auth_cache[flow["user_key"]],
                        user_b=auth_cache[flow["opponent_key"]],
                        topic=topic,
                        target=target,
                        flow_name=flow_name,
                    )
                else:
                    raise APIError(f"Unsupported room: {room}")

                metrics = build_metrics(records)
                flow_report: dict[str, Any] = {
                    "name": flow_name,
                    "room": room,
                    "topic": topic,
                    "status": "ok",
                    "metrics": metrics,
                    "record_count": len(records),
                }
                if flow_name == "custom_geography_china":
                    flow_report["china_diagnostics"] = custom_china_diagnostics(records)

                flows_report.append(flow_report)
                all_records.extend(records)
                print(f"[done] {flow_name} collected={len(records)} unique={metrics['unique_questions']}")

            except Exception as exc:
                error_text = f"{flow_name}: {exc}"
                errors.append(error_text)
                flows_report.append(
                    {
                        "name": flow_name,
                        "room": room,
                        "topic": topic,
                        "status": "error",
                        "error": str(exc),
                    }
                )
                print(f"[error] {error_text}")

        report = {
            "generated_at": now_iso(),
            "base_url": base_url,
            "target_per_flow": target,
            "flows": flows_report,
            "errors": errors,
        }
        return {"report": report, "records": all_records}

    finally:
        await harvester.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Harvest 100+ live questions per room flow")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Backend base URL")
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET, help="Questions per flow")
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout seconds")
    parser.add_argument(
        "--output-dir",
        default="",
        help="Output directory (defaults to backend/generated)",
    )
    parser.add_argument(
        "--flows",
        default="",
        help="Comma-separated flow names to run (default: all flows)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir) if args.output_dir else (Path(__file__).resolve().parents[1] / "generated")
    output_dir.mkdir(parents=True, exist_ok=True)

    selected_flows = {
        name.strip()
        for name in str(args.flows or "").split(",")
        if name.strip()
    }

    result = asyncio.run(
        run_harvest_selected(
            base_url=args.base_url,
            target=max(1, int(args.target)),
            timeout_seconds=max(5.0, float(args.timeout)),
            selected_flow_names=selected_flows,
        )
    )

    report = result["report"]
    records = result["records"]

    report_json = output_dir / "live_room_harvest_report.json"
    questions_json = output_dir / "live_room_harvest_questions.json"
    report_md = output_dir / "live_room_harvest_report.md"

    report_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    questions_json.write_text(json.dumps(records, indent=2), encoding="utf-8")
    report_md.write_text(make_markdown_summary(report), encoding="utf-8")

    print(f"[output] {report_json}")
    print(f"[output] {questions_json}")
    print(f"[output] {report_md}")

    return 1 if report.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
