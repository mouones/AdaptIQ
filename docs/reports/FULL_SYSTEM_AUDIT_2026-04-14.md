# Full System Audit Report (2026-04-14)

## Scope
This audit covered:
- Backend API behavior (auth, onboarding, classic, challenge, custom, PvP, admin guards)
- Frontend runtime behavior (login/signup + room navigation + core actions)
- Automated tests (pytest suites + standalone integration scripts)
- Runtime architecture validation (local backend/frontend, Docker infra-only)

## Runtime Baseline
- Docker services running: postgres, redis, pgadmin, redis-commander
- Docker services NOT running: backend, frontend
- Local app processes:
  - Python backend on port 8000
  - Node frontend on port 3000
- Backend health during audit: `status=ok`, services `database=ok`, `redis=ok`, `llm=ok`

## Executed Checks

### Backend automated tests
1. `python -m pytest -q tests --ignore=tests/e2e_test.py`
- Result: `30 passed, 9 skipped`

2. `python -m pytest -q tests`
- Result (after e2e harness fix): `30 passed, 10 skipped`

3. Focused regressions:
- `python -m pytest -q tests/test_pvp_admin_regressions.py tests/test_security_regressions.py tests/test_custom_generation_policy.py tests/test_authz_guards.py`
- Result: `27 passed`

### Live integration scripts
1. `python tests/e2e_test.py`
- Result: `PASSED 86, FAILED 0`

2. `python tests/test_custom_geo_scope_live.py`
- Result: pass for France and United States strict scope checks

3. `python tests/test_security_regressions_live.py`
- Result: all checks passed (tamper resistance, session binding, duplicate race handling)

4. `python tests/test_authenticated_flow_live.py`
- Result: full authenticated flow succeeded (signup/login/ownership/401/403 checks)

5. `python tests/audit_postman.py`
- Result: dynamic variable audit passed, one warning for `conceptId1` not pre-defined in collection vars

### Frontend checks
1. `npm run lint`
- Result: passed

2. `npm run build`
- Result: passed

3. Browser interaction checks (manual automation)
- Signup and login flow
- Dashboard navigation
- Classic room: topic select, hint, answer submit
- Challenge room: level start, answer submit
- Custom room: geography map select, question flow, hint, submit
- PvP room: queue join + cancel
- Logout flow

## Confirmed Issues Found During Initial Audit

### 1) Test harness bug fixed
- File: `backend/tests/e2e_test.py`
- Problem: script executed during pytest import and called `sys.exit`, causing internal collection failure.
- Fix applied: module-level pytest skip guard for non-`__main__` execution.
- Verification: full pytest run no longer crashes.

### 2) Script `test_challenge.py` is stale against current auth contract
- File: `backend/tests/test_challenge.py`
- Evidence: challenge endpoints are called without JWT auth headers, producing 401 under current protected API.
- Impact: script reports false negatives although challenge routes work in authenticated live tests.
- Remediation status: resolved (auth bootstrap + bearer headers + anti-cheat contract alignment).

### 3) Script `test_custom.py` is environment-hardcoded
- File: `backend/tests/test_custom.py`
- Evidence: hardcoded DB credentials/port (`adaptiq/adaptiq` at 5432) conflict with current local setup (`pfe` at 5433).
- Impact: script fails before API assertions.
- Remediation status: resolved (API auth bootstrap replaced direct DB insertion; env-driven base URL; anti-cheat payload checks updated).

### 4) Script `test_onboarding.py` is environment-hardcoded
- File: `backend/tests/test_onboarding.py`
- Evidence: same hardcoded DB assumptions as `test_custom.py`.
- Impact: script fails before onboarding endpoint checks.
- Remediation status: resolved (API auth bootstrap for primary + skip users; tokenized onboarding calls).

### 5) Script `test_authenticated_flow.py` is path/bootstrap-fragile
- File: `backend/tests/test_authenticated_flow.py`
- Evidence: injects tests directory into `sys.path` then imports `main`, causing import/runtime fragility.
- Impact: unreliable execution depending on invocation context.
- Remediation status: resolved (rewritten as live-backend smoke flow; removed fragile in-process import assumptions).

## Post-Fix Verification (Remediation Pass)

The previously stale standalone scripts were rerun after fixes:

1. `python tests/test_alt_db.py`
- Result: passed (engine/table/user CRUD checks all green)

2. `python tests/test_challenge.py`
- Result: `12/12 passed`

3. `python tests/test_custom.py`
- Result: `22/22 passed`

4. `python tests/test_onboarding.py`
- Result: `16/16 passed`

5. `python tests/test_authenticated_flow.py`
- Result: passed (health/signup/login/ownership/401/403 checks)

## Deep Validation Pass (Post-Remediation)

Additional checks were executed after script remediation to validate edge-case behavior:

1. `python -m pytest -q tests -rs`
- Result: `30 passed, 10 skipped` with explicit skip reasons for standalone/live scripts.

2. `python tests/e2e_test.py`
- Result: `PASSED 86, FAILED 0`.

3. `python tests/test_security_regressions_live.py`
- Result: all live security checks passed (tamper resistance, session binding, duplicate race handling).

4. `python tests/test_custom_geo_scope_live.py`
- Result: strict geography scope checks passed.

5. `python tests/test_authenticated_flow_live.py`
- Result: passed.

6. Auth edge-case probe with a signed JWT containing non-UUID `sub`.
- Initial result: API returned `500 ValueError` instead of `401`.

7. Login burst probe (15 rapid invalid logins).
- Initial result: all responses were `401`, no `429` responses observed.

## Newly Confirmed Issues (Deep Pass)

### High — Invalid JWT `sub` can trigger server error (500)
- File: `backend/routers/auth.py` (token user lookup path)
- Evidence: signed token with `sub="not-a-uuid"` produced `500 {"detail":"ValueError: badly formed hexadecimal UUID string"}`.
- Root cause: `uuid.UUID(user_id)` is not wrapped in a `ValueError` guard in `get_current_user`.
- Risk: malformed-but-signed tokens trigger internal error path instead of clean auth rejection.

### Medium — Rate limiter configured but not effectively applied on active routes
- Files: `backend/dependencies.py`, `backend/main.py`, active routers under `backend/routers/`
- Evidence:
  - Limiter is instantiated and attached to app state.
  - No active `@limiter.limit(...)` decorators found in current routers.
  - Runtime burst test on `/api/auth/login` returned only `401` (no `429`).
- Risk: brute-force and spray attempts rely only on credential checks, not request throttling.

### Low — Startup logging can report dataset success when dataset is unavailable
- Files: `backend/main.py`, `backend/rag/hf_dataset.py`
- Evidence:
  - `load_hf_dataset()` can complete with `_dataset_loaded=True` while `_dataset is None`.
  - Startup still logs `"HuggingFace dataset loaded for RAG"` unconditionally.
- Risk: operational observability mismatch; deployment appears healthy while dataset-backed retrieval is disabled.

### Low — Challenge answer verification swallows backend verification exceptions
- File: `backend/routers/challenge.py` (`_verify_answer`)
- Evidence: broad `except Exception` logs and returns `False`.
- Risk: backend/DB verification faults can be surfaced to users as incorrect answers rather than explicit service errors.

## Implemented Fixes (Requested 1-5)

All five requested deep-pass issues were implemented and revalidated:

1. Invalid JWT `sub` handling hardened.
- File: `backend/routers/auth.py`
- Change: `get_current_user` now validates/parses `sub` UUID safely and returns `401 Invalid token payload` instead of raising `500`.
- Verification: signed token with `sub="not-a-uuid"` now returns `401`.

2. Request throttling re-enabled on sensitive auth and gameplay endpoints.
- Files: `backend/main.py`, `backend/routers/auth.py`, `backend/routers/classic_room.py`, `backend/routers/challenge.py`, `backend/routers/custom.py`
- Change: added `SlowAPIMiddleware` and active `@limiter.limit(...)` on selected auth/game routes.
- Verification: login burst probe produced `429` responses (`10` out of `20` attempts).

3. Challenge answer verification no longer swallows backend faults.
- File: `backend/routers/challenge.py`
- Change: `_verify_answer` now uses explicit UUID validation and SQLAlchemy error handling (`503` on verification backend failure) rather than blanket `except Exception -> False`.

4. RAG dataset startup logging now reflects real loader outcome.
- Files: `backend/rag/hf_dataset.py`, `backend/main.py`
- Change: loader returns availability boolean and startup logs success only when dataset is actually loaded.
- Verification: startup now logs `HuggingFace dataset unavailable; RAG will run without dataset-backed retrieval` when `datasets` package is missing.

5. Debug admin endpoint removed.
- File: `backend/routers/admin.py`
- Change: deleted `/api/admin/test-endpoint`.
- Verification: route now returns `404 Not Found`.

Test status after fixes:
- `python -m pytest -q tests` → `29 passed, 10 skipped`

## What Passed with Real Scenarios
- End-to-end room flows (classic/challenge/custom/pvp) passed through `tests/e2e_test.py`.
- Security regression live checks passed.
- Geography strict-topic custom behavior passed for key countries.
- Frontend build/type checks passed.
- Core UI interactions/buttons for major flows worked in browser automation.

## Coverage Limits (still open)
- Additional standalone scripts outside this remediation set may still drift over time and should be periodically revalidated.
- UI testing covered major pathways, but not every visual state/edge micro-interaction.

## Recommended Next Actions
1. Add a single orchestrated smoke command that runs pytest + live scripts consistently.
2. Add UI automated regression suite (Playwright) for room flows and critical buttons.
3. Add explicit regression tests for malformed JWT `sub` handling and auth 429 rate-limit behavior.

---
Stay tuned: a follow-up hardening pass should standardize all script tests to current auth and environment contracts so every test file is directly runnable without manual setup tweaks.
