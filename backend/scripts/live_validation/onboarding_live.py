"""
test_onboarding.py
Run from backend folder:
    python scripts/live_validation/onboarding_live.py

Creates a fresh test user, then tests all onboarding endpoints in order.
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
SKIP_UUID = ""
SKIP_AUTH_TOKEN = ""


def ok(label, detail=""):
    global passed; passed += 1
    print(f"{GREEN}âœ“{RESET} {label}" + (f"  â†’  {detail}" if detail else ""))

def fail(label, reason=""):
    global failed; failed += 1
    print(f"{RED}âœ—{RESET} {label}")
    if reason: print(f"  {YELLOW}â†³ {reason}{RESET}")

def check(label, condition, detail="", reason=""):
    ok(label, detail) if condition else fail(label, reason)

def _request(method, path, body=None, token="", timeout=15):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

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
        try:    return e.code, json.loads(body)
        except: return e.code, {"detail": body.decode(errors="replace")}
    except Exception as e:
        return 0, {"error": str(e)}


def get(path, token=""):
    return _request("GET", path, token=token, timeout=15)


def post(path, body=None, token=""):
    return _request("POST", path, body=body or {}, token=token, timeout=15)


def bootstrap_auth_user(label: str):
    run_id = uuid.uuid4().hex[:8]
    email = f"{label}_{run_id}@example.com"
    username = f"{label}_{run_id}"
    password = "TestPass123!"

    status, data = post(
        "/api/auth/signup",
        {"email": email, "username": username, "password": password},
    )
    if status not in (200, 201):
        print(f"{RED}âœ— Could not create {label} user: {status} {data}{RESET}")
        sys.exit(1)

    status, data = post(
        "/api/auth/login",
        {"email": email, "password": password},
    )
    if status != 200:
        print(f"{RED}âœ— Could not login {label} user: {status} {data}{RESET}")
        sys.exit(1)

    token = data.get("access_token", "")
    user_id = str((data.get("user") or {}).get("id", ""))
    if not token or not user_id:
        print(f"{RED}âœ— Incomplete auth payload for {label}: {data}{RESET}")
        sys.exit(1)

    return user_id, token


print("\n" + "â•"*60)
print("  AdaptIQ â€” Onboarding Backend Test Suite")
print("â•"*60 + "\n")

# 1. Health
status, data = get("/health")
check("Health check", status == 200 and data.get("status") == "ok",
      detail=str(data.get("services","")), reason=f"{status}: {data}")

# 1.5 Auth bootstrap
TEST_USER_UUID, AUTH_TOKEN = bootstrap_auth_user("test_onboarding")
print(f"  {YELLOW}â„¹ Created onboarding test user: {TEST_USER_UUID}{RESET}")

# 2. Status â€” new user, needs onboarding
status, data = get(f"/api/onboarding/status?user_id={TEST_USER_UUID}", token=AUTH_TOKEN)
check("GET /api/onboarding/status (new user)",
      status == 200 and data.get("onboarding_needed") is True,
      detail=str(data), reason=f"{status}: {data}")
check("New user: first_login=True",        data.get("first_login") is True,        reason=str(data))
check("New user: onboarding_completed=False", data.get("onboarding_completed") is False, reason=str(data))
check("New user: tour_needed=False",       data.get("tour_needed") is False,       reason=str(data))

# 3. Submit survey
status, data = post("/api/onboarding/survey", {
    "user_id":              TEST_USER_UUID,
    "topics_confident":     ["History - World War II", "Geography - France"],
    "topics_want_to_learn": ["History - Cold War"],
}, token=AUTH_TOKEN)
check("POST /api/onboarding/survey",
      status == 200 and data.get("success") is True,
      detail=str(data), reason=f"{status}: {data}")

# 4. Status after survey â€” tour_needed=True
status, data = get(f"/api/onboarding/status?user_id={TEST_USER_UUID}", token=AUTH_TOKEN)
check("Status after survey: onboarding_completed=True",
      status == 200 and data.get("onboarding_completed") is True,
      detail=str(data), reason=f"{status}: {data}")
check("Status after survey: tour_needed=True",
      data.get("tour_needed") is True,
      reason=str(data))
check("Status after survey: first_login=False",
      data.get("first_login") is False,
      reason=str(data))

# 5. Survey idempotency â€” second call must return 409
status, data = post("/api/onboarding/survey", {
    "user_id":              TEST_USER_UUID,
    "topics_confident":     ["History - Ancient Rome"],
    "topics_want_to_learn": [],
}, token=AUTH_TOKEN)
check("Survey idempotent: 2nd call â†’ 409",
      status == 409, detail=f"Got {status}", reason=f"Expected 409, got {status}: {data}")

# 6. Mark tour seen
status, data = post("/api/onboarding/mark-tour-seen", {"user_id": TEST_USER_UUID}, token=AUTH_TOKEN)
check("POST /api/onboarding/mark-tour-seen",
      status == 200 and data.get("success") is True,
      detail=str(data), reason=f"{status}: {data}")

# 7. Status after tour seen â€” tour_needed=False
status, data = get(f"/api/onboarding/status?user_id={TEST_USER_UUID}", token=AUTH_TOKEN)
check("Status after tour: tour_needed=False",
      status == 200 and data.get("tour_needed") is False,
      detail=str(data), reason=f"{status}: {data}")

# 8. Skip flow â€” fresh user
SKIP_UUID, SKIP_AUTH_TOKEN = bootstrap_auth_user("test_skip")

status, data = post("/api/onboarding/skip", {"user_id": SKIP_UUID}, token=SKIP_AUTH_TOKEN)
check("POST /api/onboarding/skip",
    status == 200 and data.get("success") is True,
    detail=str(data), reason=f"{status}: {data}")

status, data = get(f"/api/onboarding/status?user_id={SKIP_UUID}", token=SKIP_AUTH_TOKEN)
check("Status after skip: onboarding_completed=True",
    data.get("onboarding_completed") is True, reason=str(data))
check("Status after skip: tour_needed=True",
    data.get("tour_needed") is True, reason=str(data))

# 9. Bad UUID â†’ 422
status, data = get("/api/onboarding/status?user_id=not-a-uuid", token=AUTH_TOKEN)
check(
    "Bad user_id rejected (403 or 422)",
    status in (403, 422),
    detail=f"Got {status}",
    reason=f"Expected 403/422, got {status}: {data}",
)

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
total = passed + failed
print("\n" + "â•"*60)
print(f"  Results: {GREEN}{passed} passed{RESET}  /  {RED}{failed} failed{RESET}  /  {total} total")
print("â•"*60 + "\n")
if failed:
    sys.exit(1)

