"""
Live regression tests for security/concurrency hardening.

Run from backend folder:
    python scripts/live_validation/security_regressions_live.py
"""

import asyncio
import uuid

import httpx

BASE = "http://localhost:8000"


def _ok(msg: str) -> None:
    print(f"[OK]   {msg}")


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}")


async def signup_and_login(client: httpx.AsyncClient, tag: str) -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    email = f"sec_{tag}_{suffix}@example.com"
    username = f"sec_{tag}_{suffix}"
    password = "SecurePass123!"

    r = await client.post(
        f"{BASE}/api/auth/signup",
        json={"email": email, "username": username, "password": password},
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"signup failed for {tag}: {r.status_code} {r.text[:200]}")

    r = await client.post(
        f"{BASE}/api/auth/login",
        json={"email": email, "password": password},
    )
    if r.status_code != 200:
        raise RuntimeError(f"login failed for {tag}: {r.status_code} {r.text[:200]}")

    data = r.json()
    token = data.get("access_token", "")
    user_id = data.get("user", {}).get("id", "")
    if not token or not user_id:
        raise RuntimeError(f"invalid auth response for {tag}: {data}")

    return token, user_id


async def test_custom_tampered_submit(client: httpx.AsyncClient, token: str, user_id: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}

    r = await client.post(
        f"{BASE}/api/custom/start-session",
        headers=headers,
        json={"user_id": user_id, "topic": "History - World War II"},
    )
    if r.status_code != 200:
        raise RuntimeError(f"custom start-session failed: {r.status_code} {r.text[:200]}")
    session_id = r.json()["session_id"]

    r = await client.post(
        f"{BASE}/api/custom/generate-question",
        headers=headers,
        json={"session_id": session_id, "topic": "History - World War II"},
    )
    if r.status_code != 200:
        raise RuntimeError(f"custom generate-question failed: {r.status_code} {r.text[:200]}")

    q = r.json()
    qid = q["id"]
    options = q.get("options", [])
    if not options:
        raise RuntimeError("custom question did not include options")
    selected = options[0]
    forged = "__FORGED_CORRECT_ANSWER__"

    # Tampering attempt: claim the wrong option is the correct one.
    r = await client.post(
        f"{BASE}/api/custom/submit-answer",
        headers=headers,
        json={
            "session_id": session_id,
            "question_id": qid,
            "answer": selected,
            "correct_answer": forged,
            "explanation": "forged explanation",
        },
    )
    if r.status_code != 200:
        raise RuntimeError(f"custom submit-answer failed: {r.status_code} {r.text[:200]}")

    data = r.json()
    if data.get("correct_answer") == forged:
        raise RuntimeError("server trusted forged correct_answer payload")
    if data.get("explanation") == "forged explanation":
        raise RuntimeError("server trusted forged explanation payload")

    _ok("custom tampered submit payload is ignored by server verification")


async def test_custom_cross_session_question_binding(client: httpx.AsyncClient, token: str, user_id: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}

    r = await client.post(
        f"{BASE}/api/custom/start-session",
        headers=headers,
        json={"user_id": user_id, "topic": "History - World War II"},
    )
    if r.status_code != 200:
        raise RuntimeError(f"custom start-session A failed: {r.status_code} {r.text[:200]}")
    session_a = r.json()["session_id"]

    r = await client.post(
        f"{BASE}/api/custom/generate-question",
        headers=headers,
        json={"session_id": session_a, "topic": "History - World War II"},
    )
    if r.status_code != 200:
        raise RuntimeError(f"custom generate-question A failed: {r.status_code} {r.text[:200]}")
    q_a = r.json()

    r = await client.post(
        f"{BASE}/api/custom/start-session",
        headers=headers,
        json={"user_id": user_id, "topic": "History - French Revolution"},
    )
    if r.status_code != 200:
        raise RuntimeError(f"custom start-session B failed: {r.status_code} {r.text[:200]}")
    session_b = r.json()["session_id"]

    r = await client.post(
        f"{BASE}/api/custom/submit-answer",
        headers=headers,
        json={
            "session_id": session_b,
            "question_id": q_a["id"],
            "answer": (q_a.get("options") or [""])[0],
        },
    )
    if r.status_code != 409:
        raise RuntimeError(f"custom cross-session question binding failed: expected 409, got {r.status_code}")

    _ok("custom submit-answer rejects question IDs not issued to this session")


async def test_classic_cross_user_session_blocked(
    client: httpx.AsyncClient,
    token1: str,
    user1: str,
    token2: str,
    user2: str,
) -> None:
    h1 = {"Authorization": f"Bearer {token1}"}
    h2 = {"Authorization": f"Bearer {token2}"}

    r = await client.post(
        f"{BASE}/api/rooms/classic/questions",
        headers=h1,
        json={"topic": "history", "difficulty": 2},
    )
    if r.status_code != 200:
        raise RuntimeError(f"classic start session failed: {r.status_code} {r.text[:200]}")

    q = r.json()
    session_id = q["session_id"]
    qid = q["id"]
    selected = (q.get("options") or [""])[0]

    r = await client.post(
        f"{BASE}/api/rooms/classic/questions",
        headers=h2,
        json={"topic": "history", "difficulty": 2, "session_id": session_id},
    )
    if r.status_code != 403:
        raise RuntimeError(f"classic cross-user session continue should be 403, got {r.status_code}")

    r = await client.post(
        f"{BASE}/api/rooms/classic/answers",
        headers=h2,
        json={
            "session_id": session_id,
            "question_id": qid,
            "selected_answer": selected,
            "time_taken": 1,
            "used_hint": False,
        },
    )
    if r.status_code != 403:
        raise RuntimeError(f"classic cross-user submit should be 403, got {r.status_code}")

    _ok("classic session ownership is enforced for continue + submit endpoints")


async def test_challenge_duplicate_concurrency(client: httpx.AsyncClient, token: str, user_id: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}

    r = await client.post(
        f"{BASE}/api/challenge/start-session",
        headers=headers,
        json={"user_id": user_id, "topic": "History", "starting_level": 1},
    )
    if r.status_code != 200:
        raise RuntimeError(f"challenge start-session failed: {r.status_code} {r.text[:200]}")
    session_id = r.json()["session_id"]

    r = await client.post(
        f"{BASE}/api/challenge/generate-question",
        headers=headers,
        json={"session_id": session_id, "user_id": user_id, "topic": "History", "level": 1},
    )
    if r.status_code != 200:
        raise RuntimeError(f"challenge generate-question failed: {r.status_code} {r.text[:200]}")

    q = r.json()
    qid = q["id"]
    answer = q["options"][0]

    payload = {
        "user_id": user_id,
        "session_id": session_id,
        "question_id": qid,
        "answer": answer,
        "time_taken": 1.0,
    }

    async def _submit_once():
        return await client.post(f"{BASE}/api/challenge/submit-answer", headers=headers, json=payload)

    r1, r2 = await asyncio.gather(_submit_once(), _submit_once())
    codes = sorted([r1.status_code, r2.status_code])
    if codes != [200, 409]:
        raise RuntimeError(f"challenge duplicate race unexpected statuses: {codes}")

    _ok("challenge duplicate concurrent submit is blocked (200 + 409)")


async def _wait_for_match(client: httpx.AsyncClient, token: str, user_id: str) -> str:
    headers = {"Authorization": f"Bearer {token}"}
    for _ in range(20):
        r = await client.get(
            f"{BASE}/api/pvp/queue-status",
            headers=headers,
            params={"user_id": user_id},
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "matched" and data.get("match_id"):
                return data["match_id"]
        await asyncio.sleep(0.25)
    raise RuntimeError("timed out waiting for PvP match")


async def test_pvp_duplicate_concurrency(client: httpx.AsyncClient, token1: str, user1: str, token2: str, user2: str) -> None:
    h1 = {"Authorization": f"Bearer {token1}"}
    h2 = {"Authorization": f"Bearer {token2}"}

    # Ensure clean queue state.
    await client.request("DELETE", f"{BASE}/api/pvp/leave-queue", headers=h1, json={"user_id": user1})
    await client.request("DELETE", f"{BASE}/api/pvp/leave-queue", headers=h2, json={"user_id": user2})

    r = await client.post(f"{BASE}/api/pvp/join-queue", headers=h1, json={"user_id": user1, "topic": "History"})
    if r.status_code != 200:
        raise RuntimeError(f"pvp user1 join failed: {r.status_code} {r.text[:200]}")

    r = await client.post(f"{BASE}/api/pvp/join-queue", headers=h2, json={"user_id": user2, "topic": "History"})
    if r.status_code != 200:
        raise RuntimeError(f"pvp user2 join failed: {r.status_code} {r.text[:200]}")

    match_id = await _wait_for_match(client, token1, user1)

    r = await client.get(f"{BASE}/api/pvp/match/{match_id}", headers=h1)
    if r.status_code != 200:
        raise RuntimeError(f"pvp match details failed: {r.status_code} {r.text[:200]}")

    q = r.json().get("questions", [])[0]
    payload = {
        "user_id": user1,
        "question_id": q["id"],
        "question_index": 0,
        "answer": q["options"][0],
        "time_taken": 1.0,
    }

    async def _submit_once():
        return await client.post(f"{BASE}/api/pvp/match/{match_id}/answer", headers=h1, json=payload)

    r1, r2 = await asyncio.gather(_submit_once(), _submit_once())
    codes = sorted([r1.status_code, r2.status_code])
    if codes != [200, 400]:
        raise RuntimeError(f"pvp duplicate race unexpected statuses: {codes}")

    _ok("pvp duplicate concurrent submit is blocked (200 + 400)")


async def main() -> int:
    async with httpx.AsyncClient(timeout=60.0) as client:
        token1, user1 = await signup_and_login(client, "a")
        token2, user2 = await signup_and_login(client, "b")

        try:
            await test_custom_tampered_submit(client, token1, user1)
            await test_custom_cross_session_question_binding(client, token1, user1)
            await test_classic_cross_user_session_blocked(client, token1, user1, token2, user2)
            await test_challenge_duplicate_concurrency(client, token1, user1)
            await test_pvp_duplicate_concurrency(client, token1, user1, token2, user2)
            return 0
        except Exception as exc:
            _fail(str(exc))
            return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

