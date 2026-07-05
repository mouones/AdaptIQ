"""
test_challenge.py â€” Challenge Room backend test script.

Run from inside your backend folder:
    python scripts/live_validation/challenge_room_live.py

What it does:
  1. Health check
  2. Get user rank
  3. Start a challenge session
  4. Get session state
  5. Generate a question
  6. Submit the correct answer
  7. Force a level change (up)
  8. End the session

No browser needed. No CORS issues. Runs in ~30 seconds.
Prints pass/fail for each step and a summary at the end.
"""

import asyncio
import httpx
import uuid
import json
import time
import sys
import os

# â”€â”€ Config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
BASE_URL  = os.getenv("ADAPTIQ_BASE_URL", "http://localhost:8000")
TOPIC     = "Mixed"
LEVEL     = 1                   # start at level 1 (always available for rank E)
TIMEOUT   = 60.0                # generous timeout for LLM calls

# â”€â”€ Shared state passed between tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
ctx = {
    "user_id"     : None,
    "auth_headers": None,
    "session_id"  : None,
    "question_id" : None,
    "correct_ans" : None,
    "options"     : None,
}

# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

results = []

def pprint(data):
    """Print JSON response, truncated if very long."""
    s = json.dumps(data, indent=2)
    lines = s.split("\n")
    if len(lines) > 30:
        print("\n".join(lines[:30]))
        print(f"  ... ({len(lines) - 30} more lines)")
    else:
        print(s)

async def run_test(name: str, coro):
    """Run one test coroutine, catch errors, print result."""
    print(f"\n{CYAN}{'â”€'*55}{RESET}")
    print(f"{BOLD}TEST: {name}{RESET}")
    t0 = time.perf_counter()
    try:
        result = await coro
        ms = int((time.perf_counter() - t0) * 1000)
        print(f"{GREEN}âœ“ PASS{RESET}  ({ms}ms)")
        pprint(result)
        results.append((name, True, ms, None))
        return result
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        print(f"{RED}âœ— FAIL{RESET}  ({ms}ms)")
        print(f"  {RED}Error: {e}{RESET}")
        results.append((name, False, ms, str(e)))
        return None


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# TEST FUNCTIONS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

async def test_health(client: httpx.AsyncClient):
    r = await client.get(f"{BASE_URL}/health")
    r.raise_for_status()
    data = r.json()
    assert data["status"] == "ok", f"Expected status=ok, got {data['status']}"
    assert set(data.keys()) == {"status"}, f"Unexpected public health payload keys: {sorted(data.keys())}"
    return data


async def test_auth_bootstrap(client: httpx.AsyncClient):
    run_id = uuid.uuid4().hex[:8]
    email = f"challenge_{run_id}@example.com"
    username = f"challenge_{run_id}"
    password = "TestPass123!"

    signup = await client.post(
        f"{BASE_URL}/api/auth/signup",
        json={"email": email, "username": username, "password": password},
    )
    signup.raise_for_status()

    login = await client.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": password},
    )
    login.raise_for_status()
    login_data = login.json()

    token = login_data.get("access_token", "")
    user = login_data.get("user", {}) or {}
    user_id = user.get("id")
    if not token or not user_id:
        raise ValueError("Auth bootstrap did not return token/user id")

    ctx["user_id"] = str(user_id)
    ctx["auth_headers"] = {"Authorization": f"Bearer {token}"}

    return {
        "email": email,
        "user_id": ctx["user_id"],
    }


async def test_get_rank(client: httpx.AsyncClient):
    if not ctx["user_id"]:
        raise ValueError("Missing user_id; auth bootstrap must run first")
    r = await client.get(
        f"{BASE_URL}/api/challenge/user/{ctx['user_id']}/rank",
        headers=ctx["auth_headers"],
    )
    r.raise_for_status()
    data = r.json()
    assert "current_rank"     in data, "Missing current_rank"
    assert "rank_points"      in data, "Missing rank_points"
    assert "available_levels" in data, "Missing available_levels"
    assert data["current_rank"] == "E",         f"New user should be rank E, got {data['current_rank']}"
    assert 1 in data["available_levels"],        "Level 1 should be available for rank E"
    assert 2 in data["available_levels"],        "Level 2 should be available for rank E"
    assert 5 not in data["available_levels"],    "Level 5 should NOT be available for rank E (anti-farming check)"
    print(f"  Rank: {data['current_rank']} | Available levels: {data['available_levels']}")
    return data


async def test_rank_gate_blocked(client: httpx.AsyncClient):
    """Trying to start at level 5 as rank E must be rejected (403)."""
    r = await client.post(
        f"{BASE_URL}/api/challenge/start-session",
        json={"user_id": ctx["user_id"], "topic": TOPIC, "starting_level": 5},
        headers=ctx["auth_headers"],
    )
    assert r.status_code == 403, f"Expected 403 for level 5 with rank E, got {r.status_code}"
    print(f"  Correctly rejected level 5 for rank E â†’ 403")
    return {"blocked": True, "status_code": r.status_code}


async def test_start_session(client: httpx.AsyncClient):
    r = await client.post(
        f"{BASE_URL}/api/challenge/start-session",
        json={"user_id": ctx["user_id"], "topic": TOPIC, "starting_level": LEVEL},
        headers=ctx["auth_headers"],
    )
    r.raise_for_status()
    data = r.json()
    assert "session_id"   in data, "Missing session_id"
    assert "current_level" in data, "Missing current_level"
    assert data["current_level"] == LEVEL, f"Expected level {LEVEL}, got {data['current_level']}"
    assert data["rank_points"] == 0, "Session should start with 0 rank_points"
    ctx["session_id"] = data["session_id"]
    print(f"  Session: {ctx['session_id'][:16]}... | Level: {data['current_level']} | Rank: {data['current_rank']}")
    return data


async def test_get_session(client: httpx.AsyncClient):
    assert ctx["session_id"], "No session â€” start_session must run first"
    r = await client.get(
        f"{BASE_URL}/api/challenge/session/{ctx['session_id']}",
        headers=ctx["auth_headers"],
    )
    r.raise_for_status()
    data = r.json()
    assert data["is_completed"] == False,            "Session should not be completed yet"
    assert data["current_level"] == LEVEL,           f"Level mismatch: expected {LEVEL}"
    assert data["streak_correct"] == 0,              "Correct streak should be 0 at start"
    assert data["streak_wrong"]   == 0,              "Wrong streak should be 0 at start"
    print(f"  State: level={data['current_level']} streak_c={data['streak_correct']} streak_w={data['streak_wrong']}")
    return data


async def test_generate_question(client: httpx.AsyncClient):
    """This is the slow test â€” LLM takes 5-20 seconds."""
    assert ctx["session_id"], "No session â€” start_session must run first"
    print(f"  {YELLOW}Calling LLM... (may take 10-20s){RESET}")
    r = await client.post(
        f"{BASE_URL}/api/challenge/generate-question",
        json={
            "session_id": ctx["session_id"],
            "user_id"   : ctx["user_id"],
            "topic"     : TOPIC,
            "level"     : LEVEL,
        }
        ,
        headers=ctx["auth_headers"],
    )
    r.raise_for_status()
    data = r.json()
    assert "id"           in data, "Missing id"
    assert "text"         in data, "Missing text"
    assert "options"      in data, "Missing options"
    assert "correctAnswer" not in data, "correctAnswer must not be exposed before submit"
    assert len(data["options"]) >= 2,                 "Question must have at least 2 options"
    assert "points_value" in data,                    "Missing points_value (challenge-specific field)"
    assert data["level"] == LEVEL,                    f"Level mismatch: expected {LEVEL}"
    assert data.get("explanation", "") == "",         "Explanation should be hidden before submit"

    ctx["question_id"] = data["id"]
    # Anti-cheat: backend does not expose correct answer pre-submit.
    # Submit a valid option to exercise answer validation and scoring.
    ctx["correct_ans"] = data["options"][0]
    ctx["options"]     = data["options"]

    print(f"  Q: {data['text'][:70]}...")
    print(f"  Options: {data['options']}")
    print(f"  Selected for submit: {ctx['correct_ans']} | Points value: {data['points_value']}")
    return data


async def test_submit_correct_answer(client: httpx.AsyncClient):
    assert ctx["session_id"],  "No session"
    assert ctx["question_id"], "No question â€” generate_question must run first"
    r = await client.post(
        f"{BASE_URL}/api/challenge/submit-answer",
        json={
            "session_id" : ctx["session_id"],
            "question_id": ctx["question_id"],
            "user_id"    : ctx["user_id"],
            "answer"     : ctx["correct_ans"],
            "time_taken" : 7.3,
        }
        ,
        headers=ctx["auth_headers"],
    )
    r.raise_for_status()
    data = r.json()
    assert "is_correct" in data, "Missing is_correct"
    assert "points_change" in data, "Missing points_change"
    assert "new_rank_points" in data, "Missing new_rank_points"
    assert "correct_answer" in data and data["correct_answer"], "Missing revealed correct_answer"
    assert "explanation" in data and data["explanation"], "Missing revealed explanation"
    status = "Correct" if data["is_correct"] else "Incorrect"
    print(f"  {status} | Points: {data['points_change']} | Running total: {data['new_rank_points']}")
    print(f"  Streaks: correct={data['streak_correct']} wrong={data['streak_wrong']} | Level: {data['new_level']}")
    if data.get("force_level_change"):
        print(f"  {YELLOW}Level change triggered: {data['force_level_change']}{RESET}")
    return data


async def test_duplicate_answer_blocked(client: httpx.AsyncClient):
    """Submitting the same question twice in the same session must return 409."""
    assert ctx["session_id"],  "No session"
    assert ctx["question_id"], "No question"
    r = await client.post(
        f"{BASE_URL}/api/challenge/submit-answer",
        json={
            "session_id" : ctx["session_id"],
            "question_id": ctx["question_id"],
            "user_id"    : ctx["user_id"],
            "answer"     : ctx["correct_ans"],
            "time_taken" : 1.0,
        }
        ,
        headers=ctx["auth_headers"],
    )
    assert r.status_code == 409, f"Expected 409 for duplicate answer, got {r.status_code}"
    print(f"  Correctly blocked duplicate submission â†’ 409")
    return {"blocked": True, "status_code": r.status_code}


async def test_change_level(client: httpx.AsyncClient):
    assert ctx["session_id"], "No session"
    r = await client.patch(
        f"{BASE_URL}/api/challenge/session/{ctx['session_id']}/change-level",
        json={"direction": "up", "reason": "manual test trigger"},
        headers=ctx["auth_headers"],
    )
    r.raise_for_status()
    data = r.json()
    assert "new_level" in data,          "Missing new_level in response"
    assert data["direction"] == "up",    "Direction should be 'up'"
    assert data["new_level"] <= 5,       "Level cannot exceed 5"
    print(f"  Level changed â†’ {data['new_level']} ({data['reason']})")
    return data


async def test_end_session(client: httpx.AsyncClient):
    assert ctx["session_id"], "No session"
    r = await client.post(
        f"{BASE_URL}/api/challenge/session/{ctx['session_id']}/end"
        ,
        headers=ctx["auth_headers"],
    )
    r.raise_for_status()
    data = r.json()
    assert "total_questions"      in data, "Missing total_questions"
    assert "total_points_earned"  in data, "Missing total_points_earned"
    assert "new_rank"             in data, "Missing new_rank"
    assert "new_rank_points"      in data, "Missing new_rank_points"
    assert data["new_rank"] in ("E","D","C","B","A"), f"Invalid rank: {data['new_rank']}"
    print(f"  Session ended âœ“")
    print(f"  Questions: {data['total_questions']} | Points earned: {data['total_points_earned']}")
    print(f"  Rank: {data['new_rank']} | Global rank_points: {data['new_rank_points']}")
    if data.get("rank_changed"):
        print(f"  {GREEN}RANK PROMOTION!{RESET}")
    return data


async def test_idempotent_end(client: httpx.AsyncClient):
    """Ending an already-completed session must not crash â€” it's idempotent."""
    assert ctx["session_id"], "No session"
    r = await client.post(
        f"{BASE_URL}/api/challenge/session/{ctx['session_id']}/end"
        ,
        headers=ctx["auth_headers"],
    )
    assert r.status_code == 200, f"Second end call should return 200 (idempotent), got {r.status_code}"
    print(f"  Second end call returned 200 (idempotent âœ“)")
    return r.json()


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MAIN
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

async def main():
    print(f"\n{BOLD}{'â•'*55}")
    print(f"  AdaptIQ Challenge Room â€” Backend Test Suite")
    print(f"{'â•'*55}{RESET}")
    print(f"  Backend : {BASE_URL}")
    print(f"  User ID : (created during auth bootstrap)")
    print(f"  Topic   : {TOPIC}  |  Starting level: {LEVEL}")
    print(f"{'â”€'*55}")

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:

        # â”€â”€ Group 1: Health â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        print(f"\n{BOLD}[ HEALTH ]{RESET}")
        await run_test("Health check",  test_health(client))

        # â”€â”€ Group 1.5: Auth bootstrap â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        print(f"\n{BOLD}[ AUTH ]{RESET}")
        await run_test("Signup + login test user", test_auth_bootstrap(client))

        # â”€â”€ Group 2: Rank + anti-farming gate â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        print(f"\n{BOLD}[ RANK & ANTI-FARMING ]{RESET}")
        await run_test("Get user rank (new user = E)",  test_get_rank(client))
        await run_test("Level gate: rank E cannot start at level 5",  test_rank_gate_blocked(client))

        # â”€â”€ Group 3: Session lifecycle â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        print(f"\n{BOLD}[ SESSION LIFECYCLE ]{RESET}")
        await run_test("Start session at level 1",   test_start_session(client))
        await run_test("Get session state",          test_get_session(client))

        # â”€â”€ Group 4: Question + answer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        print(f"\n{BOLD}[ QUESTION & ANSWER ]{RESET}")
        await run_test("Generate question (LLM call)", test_generate_question(client))
        await run_test("Submit answer",                test_submit_correct_answer(client))
        await run_test("Duplicate answer blocked",     test_duplicate_answer_blocked(client))

        # â”€â”€ Group 5: Level change â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        print(f"\n{BOLD}[ LEVEL CHANGE ]{RESET}")
        await run_test("Force level change (up)",  test_change_level(client))

        # â”€â”€ Group 6: Session end â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        print(f"\n{BOLD}[ SESSION END ]{RESET}")
        await run_test("End session",              test_end_session(client))
        await run_test("End session (idempotent)", test_idempotent_end(client))

    # â”€â”€ Summary â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    print(f"\n{BOLD}{'â•'*55}")
    print(f"  RESULTS")
    print(f"{'â•'*55}{RESET}")

    passed = sum(1 for _, ok, _, _ in results if ok)
    failed = sum(1 for _, ok, _, _ in results if not ok)

    for name, ok, ms, err in results:
        icon  = f"{GREEN}âœ“{RESET}" if ok else f"{RED}âœ—{RESET}"
        color = GREEN if ok else RED
        print(f"  {icon} {color}{name}{RESET}  ({ms}ms)")
        if err:
            print(f"      {RED}â””â”€â”€ {err}{RESET}")

    total = len(results)
    print(f"\n  {BOLD}{passed}/{total} passed{RESET}", end="  ")
    if failed == 0:
        print(f"{GREEN}All tests passed âœ“{RESET}")
    else:
        print(f"{RED}{failed} failed{RESET}")
        print(f"\n  {YELLOW}Check the errors above â€” common causes:{RESET}")
        print(f"  â€¢ Backend not running â†’ run: uvicorn main:app --reload")
        print(f"  â€¢ DB not running      â†’ run: docker-compose up -d postgres redis")
        print(f"  â€¢ GROQ_API_KEY missing â†’ check your .env file")
        sys.exit(1)

    print()


if __name__ == "__main__":
    asyncio.run(main())

