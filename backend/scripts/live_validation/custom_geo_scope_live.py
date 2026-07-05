"""
Live integration regression for Custom geography country-scoping.

Run from backend folder:
    python scripts/live_validation/custom_geo_scope_live.py
"""

import asyncio
import math
import uuid

import httpx

BASE = "http://localhost:8000"
SAMPLES_PER_COUNTRY = 10

GEO_SCOPE = {
    "Geography - France": {
        "primary": ["france", "french", "paris", "seine", "lyon", "marseille", "pyrenees", "alps"],
        "broader": ["europe", "european", "eu", "european union"],
    },
    "Geography - United States": {
        "primary": ["united states", "united states of america", "u.s.", "usa", "american", "washington", "mississippi", "rocky"],
        "broader": ["north america", "north american", "americas"],
    },
}


def _ok(msg: str) -> None:
    print(f"[OK]   {msg}")


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}")


def _classify(question_payload: dict, primary: list[str], broader: list[str]) -> str:
    blob = " ".join(
        [
            str(question_payload.get("text", "")),
            str(question_payload.get("explanation", "")),
            " ".join(str(opt) for opt in question_payload.get("options", [])),
        ]
    ).lower()

    if any(k in blob for k in primary):
        return "primary"
    if any(k in blob for k in broader):
        return "broader"
    return "unrelated"


async def _signup_and_login(client: httpx.AsyncClient, tag: str) -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    email = f"geo_scope_{tag}_{suffix}@example.com"
    username = f"geo_scope_{tag}_{suffix}"
    password = "SecurePass123!"

    r = await client.post(
        f"{BASE}/api/auth/signup",
        json={"email": email, "username": username, "password": password},
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"signup failed: {r.status_code} {r.text[:200]}")

    r = await client.post(
        f"{BASE}/api/auth/login",
        json={"email": email, "password": password},
    )
    if r.status_code != 200:
        raise RuntimeError(f"login failed: {r.status_code} {r.text[:200]}")

    data = r.json()
    token = data.get("access_token", "")
    user_id = data.get("user", {}).get("id", "")
    if not token or not user_id:
        raise RuntimeError(f"invalid auth response: {data}")
    return token, user_id


async def _run_country_scope_check(
    client: httpx.AsyncClient,
    token: str,
    user_id: str,
    topic: str,
    primary: list[str],
    broader: list[str],
) -> None:
    headers = {"Authorization": f"Bearer {token}"}

    r = await client.post(
        f"{BASE}/api/custom/start-session",
        headers=headers,
        json={"user_id": user_id, "topic": topic},
    )
    if r.status_code != 200:
        raise RuntimeError(f"start-session failed for {topic}: {r.status_code} {r.text[:200]}")
    session_id = r.json()["session_id"]

    primary_hits = 0
    broader_hits = 0
    unrelated_hits = 0

    for _ in range(SAMPLES_PER_COUNTRY):
        r = await client.post(
            f"{BASE}/api/custom/generate-question",
            headers=headers,
            json={"session_id": session_id, "topic": topic},
        )
        if r.status_code != 200:
            raise RuntimeError(f"generate-question failed for {topic}: {r.status_code} {r.text[:200]}")

        q = r.json()
        classification = _classify(q, primary, broader)
        if classification == "primary":
            primary_hits += 1
        elif classification == "broader":
            broader_hits += 1
        else:
            unrelated_hits += 1

    print(
        f"[{topic}] primary={primary_hits}/{SAMPLES_PER_COUNTRY} broader={broader_hits}/{SAMPLES_PER_COUNTRY} unrelated={unrelated_hits}/{SAMPLES_PER_COUNTRY}"
    )

    max_broader = max(1, math.ceil(SAMPLES_PER_COUNTRY * 0.2))
    max_unrelated = max(1, math.ceil(SAMPLES_PER_COUNTRY * 0.1))

    if primary_hits < math.floor(SAMPLES_PER_COUNTRY * 0.7):
        raise RuntimeError(f"{topic}: too few country-scoped questions ({primary_hits}/{SAMPLES_PER_COUNTRY})")
    if broader_hits > max_broader:
        raise RuntimeError(f"{topic}: too many broader-region questions ({broader_hits}/{SAMPLES_PER_COUNTRY})")
    if unrelated_hits > max_unrelated:
        raise RuntimeError(f"{topic}: unrelated questions are too frequent ({unrelated_hits}/{SAMPLES_PER_COUNTRY})")

    _ok(f"{topic} questions are mostly country-scoped with low broader-region leakage")


async def main() -> int:
    async with httpx.AsyncClient(timeout=60.0) as client:
        token, user_id = await _signup_and_login(client, "main")
        try:
            for topic, scope in GEO_SCOPE.items():
                await _run_country_scope_check(
                    client=client,
                    token=token,
                    user_id=user_id,
                    topic=topic,
                    primary=scope["primary"],
                    broader=scope["broader"],
                )
            return 0
        except Exception as exc:
            _fail(str(exc))
            return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

