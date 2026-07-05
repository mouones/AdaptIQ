"""Deep challenge journey script (manual).

This file is intentionally NOT a pytest test module: it performs a long,
stateful end-to-end journey and assumes a running backend and seeded data.

Run from the backend folder:
    python scripts/live_validation/challenge_deep.py
"""

import atexit
import sys
import time
import uuid
from pathlib import Path

import requests

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

BASE = "http://localhost:8000"
T = 60

def p(msg):
    print(msg, flush=True)

def cleanup_generated_test_users() -> None:
    try:
        import asyncio

        from scripts.cleanup_test_users import cleanup

        result = asyncio.run(cleanup(apply=True, yes=True))
        p(f"  [CLEANUP] generated users removed: {result.get('users_deleted', 0)}")
    except Exception as exc:
        p(f"  [WARN] Generated test-user cleanup failed: {exc}")


atexit.register(cleanup_generated_test_users)

suffix = uuid.uuid4().hex[:8]
email = f"challenge_deep_{suffix}@example.com"
username = f"challenge_deep_{suffix}"
password = "TestPass123!"

r = requests.post(
    f"{BASE}/api/auth/signup",
    json={"email": email, "username": username, "password": password},
    timeout=T,
)
if r.status_code not in (200, 409):
    p(f"FAIL: generated signup returned {r.status_code}: {r.text[:200]}")
    sys.exit(1)

r = requests.post(f"{BASE}/api/auth/login", json={"email": email, "password": password}, timeout=T)
if r.status_code != 200:
    p(f"FAIL: generated login returned {r.status_code}: {r.text[:200]}")
    cleanup_generated_test_users()
    sys.exit(1)
TOKEN = r.json()["access_token"]
UID = r.json()["user"]["id"]
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

p("=" * 60)
p("DEEP CHALLENGE MODE TEST - ALL 5 LEVELS")
p("=" * 60)

# Get initial rank
r = requests.get(f"{BASE}/api/challenge/user/{UID}/rank", headers=H, timeout=T)
rank = r.json()
p(f"\nInitial: rank={rank['current_rank']} pts={rank['rank_points']} available_levels={rank['available_levels']}")

# Start session at level 1
r = requests.post(f"{BASE}/api/challenge/start-session", headers=H, timeout=T, json={
    "user_id": UID, "topic": "History", "starting_level": 1
})
if r.status_code != 200:
    p(f"FAIL: start-session returned {r.status_code}: {r.text[:200]}")
    sys.exit(1)
ch = r.json()
SID = ch["session_id"]
p(f"\nSession: {SID[:8]} level={ch.get('current_level',1)} rank={ch.get('current_rank','E')}")
p(f"Available levels: {ch.get('available_levels', [])}")

# Test all 5 levels: 2 questions per level = 10 questions
results = []
for level in range(1, 6):
    p(f"\n--- LEVEL {level} ---")
    for qi in range(2):
        # Generate question at this level
        r = requests.post(f"{BASE}/api/challenge/generate-question", headers=H, timeout=T, json={
            "session_id": SID, "user_id": UID, "topic": "History", "level": level
        })
        if r.status_code != 200:
            p(f"  GenQ ERROR: {r.status_code} {r.text[:150]}")
            results.append({"level": level, "q": qi+1, "gen": "FAIL", "ans": "SKIP"})
            continue
        
        cq = r.json()
        qid = cq["id"]
        qtext = cq["text"][:80]
        opts = cq["options"]
        pts_value = cq.get("points_value", "?")
        p(f"  Q{qi+1}: [{pts_value}pts] {qtext}...")
        p(f"        Options: {' | '.join(opts[:4])}")
        
        # Answer correctly on first Q per level, wrong on second
        if qi == 0:
            # Try to answer correctly - pick first option
            ans = opts[0]
        else:
            # Deliberately pick last option (may be wrong)
            ans = opts[-1]
        
        r = requests.post(f"{BASE}/api/challenge/submit-answer", headers=H, timeout=T, json={
            "session_id": SID, "question_id": qid, "user_id": UID,
            "answer": ans, "time_taken": 5.0 + level
        })
        if r.status_code != 200:
            p(f"  Ans ERROR: {r.status_code} {r.text[:150]}")
            results.append({"level": level, "q": qi+1, "gen": "OK", "ans": "FAIL"})
            continue
        
        ca = r.json()
        p(f"        Result: correct={ca['is_correct']} pts={ca['points_change']} "
          f"new_level={ca.get('new_level','?')} streak_c={ca['streak_correct']} streak_w={ca['streak_wrong']}")
        
        if ca.get("force_level_change"):
            flc = ca["force_level_change"]
            p(f"        FORCE LEVEL CHANGE: {flc['direction']} -- {flc['reason']}")
        
        correct_ans = ca.get("correct_answer", "?")
        p(f"        Correct answer: {correct_ans}")
        p(f"        Explanation: {ca.get('explanation','')[:100]}")
        
        results.append({
            "level": level, "q": qi+1, "gen": "OK", "ans": "OK",
            "correct": ca["is_correct"], "pts": ca["points_change"],
            "new_level": ca.get("new_level")
        })

# Get session status
p(f"\n--- SESSION STATUS ---")
r = requests.get(f"{BASE}/api/challenge/session/{SID}", headers=H, timeout=T)
if r.status_code == 200:
    ss = r.json()
    p(f"  total={ss['total_questions']} correct={ss['correct_answers']} level={ss['current_level']} "
      f"rank_pts={ss['rank_points']} streak_c={ss['streak_correct']} streak_w={ss['streak_wrong']}")

# End session
r = requests.post(f"{BASE}/api/challenge/session/{SID}/end", headers=H, timeout=T, json={"user_id": UID})
if r.status_code == 200:
    ce = r.json()
    p(f"\n--- END SESSION ---")
    p(f"  total={ce['total_questions']} correct={ce['correct_answers']}")
    p(f"  total_points_earned={ce['total_points_earned']}")
    p(f"  new_rank={ce['new_rank']} new_rank_points={ce['new_rank_points']} rank_changed={ce['rank_changed']}")

# Final rank
r = requests.get(f"{BASE}/api/challenge/user/{UID}/rank", headers=H, timeout=T)
rank2 = r.json()
p(f"\nFinal: rank={rank2['current_rank']} pts={rank2['rank_points']} sessions={rank2['total_sessions']}")

# - EDGE CASES -
p(f"\n{'='*60}")
p("EDGE CASE TESTS")
p("=" * 60)

# 1. Try to start with invalid level
p("\n[EDGE] Start with level 6 (invalid)...")
r = requests.post(f"{BASE}/api/challenge/start-session", headers=H, timeout=T, json={
    "user_id": UID, "topic": "History", "starting_level": 6
})
p(f"  status={r.status_code} (expect 422)")

# 2. Try to start with level 0
p("[EDGE] Start with level 0 (invalid)...")
r = requests.post(f"{BASE}/api/challenge/start-session", headers=H, timeout=T, json={
    "user_id": UID, "topic": "History", "starting_level": 0
})
p(f"  status={r.status_code} (expect 422)")

# 3. Submit empty answer
p("[EDGE] Submit empty answer...")
r = requests.post(f"{BASE}/api/challenge/start-session", headers=H, timeout=T, json={
    "user_id": UID, "topic": "Geography", "starting_level": 1
})
edge_sid = r.json()["session_id"]
r = requests.post(f"{BASE}/api/challenge/generate-question", headers=H, timeout=T, json={
    "session_id": edge_sid, "user_id": UID, "topic": "Geography", "level": 1
})
edge_qid = r.json()["id"]
r = requests.post(f"{BASE}/api/challenge/submit-answer", headers=H, timeout=T, json={
    "session_id": edge_sid, "question_id": edge_qid, "user_id": UID,
    "answer": "", "time_taken": 5
})
p(f"  status={r.status_code} detail={r.text[:100]}")

# 4. Change level mid-session
p("[EDGE] Change level (up)...")
r = requests.patch(f"{BASE}/api/challenge/session/{edge_sid}/change-level", headers=H, timeout=T, json={
    "direction": "up", "reason": "test"
})
p(f"  status={r.status_code} body={r.text[:100]}")

# 5. Change level (down)
p("[EDGE] Change level (down)...")
r = requests.patch(f"{BASE}/api/challenge/session/{edge_sid}/change-level", headers=H, timeout=T, json={
    "direction": "down", "reason": "test"
})
p(f"  status={r.status_code} body={r.text[:100]}")

# End edge session
requests.post(f"{BASE}/api/challenge/session/{edge_sid}/end", headers=H, timeout=T, json={"user_id": UID})

# - DERANK OPTION COUNT TEST -
p(f"\n{'='*60}")
p("DERANK TEST: level 2 -> level 1 should yield 2 options")
p("=" * 60)

derank_failures = []

r = requests.post(f"{BASE}/api/challenge/start-session", headers=H, timeout=T, json={
    "user_id": UID, "topic": "History", "starting_level": 2
})
if r.status_code != 200:
    p(f"FAIL: derank start-session {r.status_code}: {r.text[:150]}")
    derank_failures.append("start-session")
else:
    derank_sid = r.json()["session_id"]
    current_level = r.json().get("current_level", 2)
    p(f"  Started session at level {current_level}")

    for attempt in range(3):
        r = requests.post(f"{BASE}/api/challenge/generate-question", headers=H, timeout=T, json={
            "session_id": derank_sid, "user_id": UID, "topic": "History", "level": current_level
        })
        if r.status_code != 200:
            p(f"  GenQ attempt {attempt + 1} ERROR: {r.status_code}")
            continue
        cq = r.json()
        wrong_answer = cq["options"][-1] if cq.get("options") else "definitely-wrong"
        if wrong_answer == cq.get("correct_answer"):
            wrong_answer = "definitely-wrong-answer"

        r = requests.post(f"{BASE}/api/challenge/submit-answer", headers=H, timeout=T, json={
            "session_id": derank_sid, "question_id": cq["id"], "user_id": UID,
            "answer": wrong_answer, "time_taken": 4.0
        })
        if r.status_code != 200:
            p(f"  Submit attempt {attempt + 1} ERROR: {r.status_code}")
            continue
        ca = r.json()
        current_level = ca.get("new_level", current_level)
        p(f"  Wrong answer {attempt + 1}: new_level={current_level} streak_w={ca.get('streak_wrong')}")
        if ca.get("force_level_change"):
            p(f"  Force level change: {ca['force_level_change']}")
        if current_level == 1:
            break

    r = requests.post(f"{BASE}/api/challenge/generate-question", headers=H, timeout=T, json={
        "session_id": derank_sid, "user_id": UID, "topic": "History", "level": current_level
    })
    if r.status_code != 200:
        p(f"FAIL: post-derank generate {r.status_code}: {r.text[:150]}")
        derank_failures.append("post-derank-generate")
    else:
        post_q = r.json()
        opt_count = len(post_q.get("options") or [])
        p(f"  Post-derank question options: {opt_count} -> {post_q.get('options')}")
        if current_level == 1 and opt_count != 2:
            p(f"FAIL: expected 2 options at level 1, got {opt_count}")
            derank_failures.append(f"option-count-{opt_count}")
        elif current_level == 1:
            p("  PASS: level 1 question has exactly 2 options")

    requests.post(f"{BASE}/api/challenge/session/{derank_sid}/end", headers=H, timeout=T, json={"user_id": UID})

if derank_failures:
    p(f"\nDERANK TEST FAILURES: {derank_failures}")
else:
    p("\nDERANK TEST: PASS")

p(f"\n{'='*60}")
p("DONE")


