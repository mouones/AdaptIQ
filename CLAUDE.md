# CLAUDE.md

## Runtime Scope

Use only these as active runtime sources:

- backend/
- frontend/
- docs/
- scripts/
- README.md

Treat these as references/history, not runtime authorities:

- docs/reports/ (dated audits; evidence for their date, not guaranteed current)

## Project Layout Rules

- Keep API runtime code in `backend/{routers,services,database,schemas}`.
- Keep frontend runtime code in `frontend/src/{pages,components,services,context,types}`.
- Keep env files machine-local only:
  - `backend/.env`
  - `frontend/.env.local`
- Keep generated/runtime artifacts in ignored folders:
  - `backend/generated/`
  - `backend/logs/`
  - `frontend/dist/`
  - `frontend/test-results/`
  - `frontend/playwright-report/`
- Do not move runtime modules unless all imports/routes are updated in the same change.
- Visual geography utility scripts belong in `backend/scripts/visual/`; Natural Earth downloads belong in `backend/generated/visual_shapes/`.
- If starting helper backend processes, write stdout/stderr and pid files under `backend/logs/runtime/<date>/`.
- Do not reference paths from deleted/temporary folders (for example `other/`).
- Do not commit until the user explicitly asks for a commit.
- Older reference docs and generated historical artifacts were archived outside this repository during development and are not part of the published project.

## Local Ports

- Frontend dev server: `http://localhost:5173`
- Backend API: `http://localhost:8000`
- Admin dashboard: `http://localhost:5173/admin` with authenticated admin user
- Swagger docs: `http://localhost:8000/docs` in local/dev only. Production disables public docs/OpenAPI unless `ENABLE_PUBLIC_DOCS=true`.
- PostgreSQL (docker): `localhost:5433`
- Redis (docker): `localhost:6379` by default; use `REDIS_HOST_PORT=6380` if Memurai owns `6379`
- PgAdmin (docker): `http://localhost:5050`
- Redis Commander (docker): `http://localhost:8081` by default; use `REDIS_COMMANDER_HOST_PORT=8082` for the same conflict case

## Security Model

- Browser auth uses `adaptiq_access` HttpOnly cookie plus `adaptiq_csrf` readable CSRF cookie.
- Unsafe cookie-authenticated requests must send `X-CSRF-Token`.
- Bearer tokens are still accepted temporarily for scripts, tests, Newman/Postman, and non-browser clients.
- Frontend code must not persist `adaptiq_token`, `adaptiq_user`, or chat history in `localStorage`.
- Frontend LLM calls are backend-only. Do not inject LLM API keys through Vite.
- Admin routes require authenticated admin users. Admin DB table responses redact sensitive columns/values.
- Admin custom-topic approval is available in the dashboard and through `/api/admin/custom-topics/*`.
- Visual Room routes require authenticated users and session ownership checks.
- Timed bans are managed through the admin users endpoint and should remain covered by unit tests.
- Public `/health` returns only `{ "status": "ok" }`; detailed health is admin-only.

## Redis / Memurai Recovery

Docker Redis is the expected local runtime. If host `127.0.0.1:6379` is held by local Memurai, Docker Redis will run internally but cannot publish the host port.

From an elevated PowerShell:

```powershell
Stop-Service -Name Memurai -Force
sc.exe config Memurai start= demand
```

Then from `backend`:

```powershell
docker compose up -d --force-recreate redis redis-commander
docker compose ps -a
```

Acceptance:

- Redis shows `127.0.0.1:6379->6379/tcp`.
- Redis Commander shows `127.0.0.1:8081->8081/tcp`.
- `GET http://127.0.0.1:8000/health` returns only `{ "status": "ok" }`.

If Memurai cannot be stopped because Windows denies service control, use alternate Docker host ports and keep backend config aligned:

```powershell
Set-Location backend
$env:REDIS_HOST_PORT = "6380"
$env:REDIS_COMMANDER_HOST_PORT = "8082"
docker compose up -d --force-recreate redis redis-commander
```

For persistent local use, set `REDIS_HOST_PORT=6380` in `backend/.env`; `config.py` builds the default `REDIS_URL` from that port unless `REDIS_URL` is explicitly set.

## Setup Runbook (PowerShell)

From `P_F_E` root:

1. Start infra (recommended for local):

```powershell
Set-Location backend
docker compose up -d
```

2. Backend setup:

```powershell
Set-Location backend
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
alembic upgrade head
python main.py
```

3. Frontend setup:

```powershell
Set-Location frontend
npm install
npm run dev
```

## Validation Commands

Backend tests:

```powershell
Set-Location backend
.venv\Scripts\python.exe -m pytest -q tests
.venv\Scripts\python.exe -m pytest -q tests/unit/test_security_regressions.py tests/unit/test_auth_helpers.py tests/test_chat.py
.venv\Scripts\python.exe -m pytest -q tests/unit/test_db_integrity_audit.py tests/unit/test_question_concept_repair.py tests/unit/test_custom_topic_approval.py
```

Frontend checks:

```powershell
Set-Location frontend
npm run lint
npm run build
npm run test:e2e
```

Security and data checks:

```powershell
Set-Location backend
.venv\Scripts\python.exe scripts\cleanup_test_users.py --dry-run
.venv\Scripts\python.exe scripts\cleanup_test_users.py --apply --yes
.venv\Scripts\python.exe scripts\repair_data_integrity.py --dry-run
.venv\Scripts\python.exe scripts\repair_data_integrity.py --apply
Set-Location ..
.\backend\.venv\Scripts\python.exe scripts\scan_secrets.py
```

Postman/Newman assets:

- Collection: `docs/api/AdaptIQ_Complete_Postman.json`
- Latest report target: `docs/reports/newman_run_latest.json`
- Current project state audit target: `docs/reports/PROJECT_STATE_AUDIT_2026-06-04.md`
- Helper diagnostics:
  - `backend/scripts/audit_postman.py`
  - `backend/scripts/admin_diag_postman.py`
- Note: test users in Postman are generated from `mailboxBaseEmail` using plus aliases (`name+user1_<ts>@domain`).
  Set `mailboxBaseEmail` to an inbox you control before running forgot-password flows.

## Core Notes

- API prefix is `/api` (for example `/api/auth`, `/api/challenge`, `/api/custom`, `/api/pvp`, `/api/admin`).
- Frontend API base comes from `frontend/src/config.ts` (`VITE_API_URL` fallback points to localhost backend).
- Ensure `DATABASE_URL`/`REDIS_URL` in `backend/.env` match docker ports above.
- `backend/alembic/versions/bb15d1154671_add_visual_tables_autogen.py` is part of the local migration chain and should remain present.
- Current Alembic chain is single-head through `20260704_02_add_question_calibration_shadow.py` (via `20260704_01_add_user_responses_user_created_index.py` and the `20260611_0x` visual-session migrations).
- Custom-topic approval is admin-only:
  - `GET /api/admin/custom-topics/candidates`
  - `POST /api/admin/custom-topics/approve`
  - `POST /api/admin/custom-topics/toggle-active`
- The frontend admin dashboard has a `topics` tab for approving custom-topic candidates.
- Approved custom topics are stored in `custom_topics`; harvested facts are stored in `custom_facts` with `source_question_id` provenance.
- `/api/custom/topics` merges built-in catalogue topics with admin-approved DB topics.
- Challenge scoring/progression uses typed config defaults from `backend/config.py`:
  - `CHALLENGE_POINTS_LEVEL_1` through `CHALLENGE_POINTS_LEVEL_5`
  - `CHALLENGE_STREAK_UP_THRESHOLD`
  - `CHALLENGE_STREAK_DOWN_THRESHOLD`
  - `CHALLENGE_RANK_D_MIN`, `CHALLENGE_RANK_C_MIN`, `CHALLENGE_RANK_B_MIN`, `CHALLENGE_RANK_A_MIN`
  - `CHALLENGE_SESSION_QUESTION_TTL_SECONDS`
- Room sizing and runtime knobs are also config-driven:
  - `CLASSIC_QUESTIONS_PER_SESSION`
  - `VISUAL_QUESTIONS_PER_SESSION`
  - `PVP_QUESTIONS_PER_MATCH`
  - `PVP_CANDIDATE_POOL_SIZE`
  - `CUSTOM_ROOM_GENERATION_TARGET`
  - `CUSTOM_ROOM_RECENT_QUESTION_LIMIT`
  - `ADMIN_DB_INSPECTOR_DEFAULT_LIMIT`
  - `ADMIN_DB_INSPECTOR_MAX_LIMIT`
  - `DATA_REPAIR_BATCH_SIZE`
  - `CLEANUP_USER_BATCH_SIZE`
- Quality/performance flags (all default `false`; see `docs/reports/QUALITY_PERF_ROADMAP_2026-07-04.md`):
  - `ENABLE_IRT_LOGIT_SCALE` (item 1): classic θ update + ZPD selection use one
    consistent scale (1-5 `difficulty_irt` ⇄ logit β).
  - `ENABLE_CANDIDATE_POOL_SAMPLING` (item 5): sample a freshness pool
    (`CANDIDATE_POOL_SIZE`) instead of `ORDER BY random()`.
  - `ENABLE_SEEN_SET_CACHE` (item 3): per-user Redis seen-set (`SEEN_SET_TTL_SECONDS`)
    to skip the 3-join seen-question union.
  - `ENABLE_NO_INLINE_LLM` (item 4): never run LLM/RAG inline on a queue miss; serve
    a DB question and enqueue a refill.
  - `ENABLE_REDIS_SESSION_LOCK` (item 6): cross-process Redis answer lock (else
    in-process asyncio lock).
  - `ENABLE_UNIFIED_CONCEPT_THETA` (item 8): custom room uses the shared
    `ConceptIRT.compute_update` math.
- Offline job `scripts/recalibrate_question_difficulty.py --dry-run|--apply` (item 2)
  writes the shadow column `question_bank.difficulty_irt_calibrated`; never touches
  served `difficulty_irt`.
- `QuestionBank.source` is provenance, not a boolean LLM flag. Admin overview groups it into `generated`, `seeded`, `admin`, `unknown`, `by_category`, and `by_source`.
- Generated source examples include `llm`, `classic_llm`, `challenge_llm`, `custom_llm_simple`, `custom_template`, `custom_rag`, probes, and imports. Classic/challenge filters use the shared taxonomy to keep room-specific rows out of unrelated pools.
- `Concept.topic` is the broad family; `Concept.scope` is the narrower context; `Concept.name` should be the direct concept name without redundant prefixes such as `Mixed - ...`.
- Visual `ended_at` is the UTC timestamp when a Visual session closes or completes; `NULL` means active or not closed. Natural completion and explicit end should both stamp it once.
- `repair_data_integrity.py` also reports and backfills unchecked question-bank governance state when governance is enabled.
- `cleanup_test_users.py` targets generated live/e2e accounts such as `test`, `copilot`, `e2e`, `flowtest`, `pw-smoke`, `geo_scope`, `sec_cookies`, `auditpvp`, `livepvpfix`, and plus-alias Postman users. Output is bucketed and redacted.
- Old scratch/admin diagnostic scripts were moved out of active `scripts/`; top-level `scripts/` should stay reserved for reusable project-wide checks such as `scan_secrets.py`.
- Standalone/live validation scripts live in `backend/scripts/live_validation/`; normal `pytest -q tests` should run without intentional skips.
- Newman reports saved under `docs/reports/` must be sanitized with `scripts/sanitize_newman_report.py` before they are kept.
- Real `.env` values must stay untracked and must not be copied into docs, logs, reports, or chat output.

## Code Reading Map

- Startup/config: `backend/main.py`, `backend/config.py`
- Auth/security: `backend/routers/auth.py`, `frontend/src/context/AuthContext.tsx`, `frontend/src/services/http.ts`
- Admin: `backend/routers/admin.py`, `backend/routers/governance.py`, `frontend/src/pages/AdminDashboard.tsx`
- Rooms: `classic_room.py`, `challenge.py`, `custom.py`, `visual_room.py`, `pvp.py`
- Services: `backend/services/*`
- Database: `backend/database/*`, `backend/alembic/versions/*`
- Current Alembic chain should be single-head through `20260704_02_add_question_calibration_shadow.py`.
- Full explanation: `docs/walkthrough.md`
