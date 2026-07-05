"""
test_custom.py
Run from the backend folder:
    python scripts/live_validation/custom_room_live.py
"""

import json
import os
import sys
import uuid
import urllib.request
import urllib.error

BASE   = os.getenv("ADAPTIQ_BASE_URL", "http://localhost:8000")
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

passed = 0
failed = 0

TEST_USER_UUID = ""
AUTH_TOKEN = ""


def ok(label, detail=""):
    global passed
    passed += 1
    suffix = f"  â†’  {detail}" if detail else ""
    print(f"{GREEN}âœ“{RESET} {label}{suffix}")


def fail(label, reason=""):
    global failed
    failed += 1
    print(f"{RED}âœ—{RESET} {label}")
    if reason:
        print(f"  {YELLOW}â†³ {reason}{RESET}")


def check(label, condition, detail="", reason=""):
    if condition:
        ok(label, detail)
    else:
        fail(label, reason)


def _request(method, path, body=None, auth=False, timeout=60):
    headers = {"Content-Type": "application/json"}
    if auth and AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {AUTH_TOKEN}"

    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"detail": body.decode(errors="replace")}
    except Exception as e:
        return 0, {"error": str(e)}


def get(path, auth=False):
    return _request("GET", path, auth=auth, timeout=30)


def post(path, body=None, auth=False):
    return _request("POST", path, body=body or {}, auth=auth, timeout=60)


def bootstrap_auth_user():
    global TEST_USER_UUID, AUTH_TOKEN

    run_id = uuid.uuid4().hex[:8]
    email = f"test_custom_{run_id}@example.com"
    username = f"test_custom_{run_id}"
    password = "TestPass123!"

    status, data = post(
        "/api/auth/signup",
        {"email": email, "username": username, "password": password},
        auth=False,
    )
    if status not in (200, 201):
        print(f"{RED}âœ— Could not create custom test user: {status} {data}{RESET}")
        sys.exit(1)

    status, data = post(
        "/api/auth/login",
        {"email": email, "password": password},
        auth=False,
    )
    if status != 200:
        print(f"{RED}âœ— Could not login custom test user: {status} {data}{RESET}")
        sys.exit(1)

    AUTH_TOKEN = data.get("access_token", "")
    TEST_USER_UUID = str((data.get("user") or {}).get("id", ""))
    if not AUTH_TOKEN or not TEST_USER_UUID:
        print(f"{RED}âœ— Auth bootstrap returned incomplete payload: {data}{RESET}")
        sys.exit(1)

    print(f"  {YELLOW}â„¹ Created auth-backed test user: {TEST_USER_UUID}{RESET}")


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
print("\n" + "â•" * 60)
print("  AdaptIQ â€” Custom Room Backend Test Suite")
print("â•" * 60 + "\n")

# â”€â”€ 1. Health check â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
status, data = get("/health")
check("Health check",
      status == 200 and data.get("status") == "ok",
      detail=str(data.get("services", "")),
      reason=f"Got {status}: {data}")

# â”€â”€ 2. Auth bootstrap â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
bootstrap_auth_user()

# â”€â”€ 3. GET /api/custom/topics â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
status, data = get("/api/custom/topics", auth=True)
topics = data.get("topics", [])
check("GET /api/custom/topics â€” returns topic list",
      status == 200 and len(topics) >= 10,
      detail=f"{len(topics)} topics returned",
      reason=f"Got {status}: {data}")
history_topics   = [t for t in topics if t.get("type") == "History"]
geography_topics = [t for t in topics if t.get("type") == "Geography"]
check("Topics contain both History and Geography",
      len(history_topics) > 0 and len(geography_topics) > 0,
      detail=f"{len(history_topics)} History, {len(geography_topics)} Geography",
      reason="Missing History or Geography topics")

# â”€â”€ 4. Start session â€” History â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
HISTORY_TOPIC = "History - World War II"
status, data = post("/api/custom/start-session", {
    "user_id": TEST_USER_UUID,
    "topic":   HISTORY_TOPIC,
}, auth=True)
check("POST /api/custom/start-session (History - World War II)",
      status == 200 and "session_id" in data,
      detail=f"session_id={str(data.get('session_id','?'))[:8]}â€¦  progress={data.get('progress_percentage','?')}%",
      reason=f"Got {status}: {data}")
session_id_history = data.get("session_id", "")
progress_initial   = data.get("progress_percentage", -1)

check("Start session returns valid progress_percentage (0â€“100)",
      isinstance(progress_initial, (int, float)) and 0 <= progress_initial <= 100,
      detail=f"{progress_initial}%",
      reason=f"progress_percentage was {progress_initial!r}")

# â”€â”€ 5. Start session â€” Geography â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
GEO_TOPIC = "Geography - France"
status, data = post("/api/custom/start-session", {
    "user_id": TEST_USER_UUID,
    "topic":   GEO_TOPIC,
}, auth=True)
check("POST /api/custom/start-session (Geography - France)",
      status == 200 and "session_id" in data,
      detail=f"session_id={str(data.get('session_id','?'))[:8]}â€¦",
      reason=f"Got {status}: {data}")
session_id_geo = data.get("session_id", "")

# â”€â”€ 6. Generate question â€” History â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
print(f"\n  {YELLOW}[LLM call â€” may take a few secondsâ€¦]{RESET}")
status, data = post("/api/custom/generate-question", {
    "session_id": session_id_history,
    "topic":      HISTORY_TOPIC,
}, auth=True)
check("POST /api/custom/generate-question (History)",
      status == 200 and "text" in data and "options" in data,
      detail=f"Q: {str(data.get('text',''))[:60]}â€¦",
      reason=f"Got {status}: {data}")
question_id = data.get("id", "")
options     = data.get("options", [])
answer_choice = options[0] if options else ""
explanation = data.get("explanation", "")

check("Question has exactly 4 options",       len(options) == 4,  detail=str(options),          reason=f"Got {len(options)} options")
check(
    "Question payload hides correct_answer",
    "correct_answer" not in data and "correctAnswer" not in data,
    detail="hidden",
    reason=f"Found leaked correct answer field in payload: {data}",
)
check(
    "Question payload hides explanation pre-answer",
    explanation == "",
    detail="hidden",
    reason=f"Expected empty explanation pre-answer, got: {explanation!r}",
)

# â”€â”€ 7. Generate question â€” Geography â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
print(f"\n  {YELLOW}[LLM call â€” may take a few secondsâ€¦]{RESET}")
status, data = post("/api/custom/generate-question", {
    "session_id": session_id_geo,
    "topic":      GEO_TOPIC,
}, auth=True)
check("POST /api/custom/generate-question (Geography)",
      status == 200 and "text" in data,
      detail=f"Q: {str(data.get('text',''))[:60]}â€¦",
      reason=f"Got {status}: {data}")

# â”€â”€ 8. Submit CORRECT answer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
status, data = post("/api/custom/submit-answer", {
    "session_id":    session_id_history,
    "question_id":   question_id,
    "answer":        answer_choice,
}, auth=True)
check("POST /api/custom/submit-answer",
    status == 200 and isinstance(data.get("is_correct"), bool),
      detail=f"is_correct={data.get('is_correct')}  new_progress={data.get('new_progress_percentage')}%",
      reason=f"Got {status}: {data}")
new_progress = data.get("new_progress_percentage", -1)
check("Progress percentage remains valid",
    isinstance(new_progress, (int, float)) and 0 <= new_progress <= 100,
    detail=f"{new_progress}%",
    reason=f"Progress out of range: {new_progress}")
check("submit-answer returns correct_answer",  "correct_answer" in data,      detail=data.get("correct_answer",""),          reason="correct_answer missing")
check("submit-answer returns explanation",      bool(data.get("explanation")), detail=str(data.get("explanation",""))[:60],   reason="explanation empty")

# â”€â”€ 9. Submit WRONG answer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
print(f"\n  {YELLOW}[LLM call â€” generating Q2 for wrong-answer testâ€¦]{RESET}")
status, q2 = post("/api/custom/generate-question", {
    "session_id": session_id_history,
    "topic":      HISTORY_TOPIC,
}, auth=True)
if status == 200 and "id" in q2:
    options2 = q2.get("options", [])
    wrong_option = options2[1] if len(options2) > 1 else (options2[0] if options2 else "")
    if wrong_option:
        status, data = post("/api/custom/submit-answer", {
            "session_id":    session_id_history,
            "question_id":   q2["id"],
            "answer":        wrong_option,
        }, auth=True)
        check("POST /api/custom/submit-answer (second answer)",
              status == 200 and isinstance(data.get("is_correct"), bool),
              detail=f"is_correct={data.get('is_correct')}",
              reason=f"Got {status}: {data}")
        check("Second answer returns authoritative correct_answer",
              bool(data.get("correct_answer")),
              detail=f"server says '{data.get('correct_answer')}'",
              reason="correct_answer missing")
    else:
        fail("Submit wrong answer", "No wrong option found in Q2")
else:
    fail("Submit wrong answer (skipped â€” Q2 generation failed)", f"{status}: {q2}")

# â”€â”€ 10. Submit to non-existent question â†’ 404 â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
status, data = post("/api/custom/submit-answer", {
    "session_id":    session_id_history,
    "question_id":   "00000000-0000-0000-0000-000000000000",
    "answer":        "whatever",
}, auth=True)
check("Submit with bad question_id â†’ 404", status == 404,
      detail=f"Got {status}", reason=f"Expected 404, got {status}: {data}")

# â”€â”€ 11. End session â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
status, data = post(f"/api/custom/session/{session_id_history}/end", auth=True)
check("POST /api/custom/session/{id}/end",
      status == 200 and "completion_percentage_after" in data,
      detail=(f"answered={data.get('questions_answered')}  "
              f"correct={data.get('correct_count')}  "
              f"mastery={data.get('completion_percentage_after')}%"),
      reason=f"Got {status}: {data}")

# â”€â”€ 12. End session idempotent â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
status, data = post(f"/api/custom/session/{session_id_history}/end", auth=True)
check("End session idempotent (2nd call â†’ 200)", status == 200,
      detail=f"Got {status}", reason=f"Expected 200, got {status}: {data}")

# â”€â”€ 13. End session bad ID â†’ 404 â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
status, data = post("/api/custom/session/00000000-0000-0000-0000-000000000000/end", auth=True)
check("End session bad session_id â†’ 404", status == 404,
      detail=f"Got {status}", reason=f"Expected 404, got {status}: {data}")

# â”€â”€ 14. Progress persists across sessions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
status, data = post("/api/custom/start-session", {
    "user_id": TEST_USER_UUID,
    "topic":   HISTORY_TOPIC,
}, auth=True)
check("Progress persists: new session returns updated percentage",
      status == 200 and data.get("progress_percentage", 0) >= new_progress,
      detail=f"persisted = {data.get('progress_percentage')}%",
      reason=f"Expected â‰¥ {new_progress}%, got {data.get('progress_percentage')}%  (status {status})")

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
total = passed + failed
print("\n" + "â•" * 60)
print(f"  Results: {GREEN}{passed} passed{RESET}  /  {RED}{failed} failed{RESET}  /  {total} total")
print("â•" * 60 + "\n")

if failed:
    sys.exit(1)

