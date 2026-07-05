# AdaptIQ Full Code Walkthrough

This is the active project walkthrough for the live `P_F_E` app. It explains how the code is connected, how to read it, what each layer owns, and how the main user flows move from browser to backend to database and back.

Do not use archived/reference copies as runtime truth. Current truth comes from:

- `backend/`
- `frontend/src/`
- `backend/alembic/versions/`
- `backend/tests/`
- `frontend/tests/`
- active runbooks: `README.md`, `CLAUDE.md`, and `docs/README.md`

## 1. What This Project Is

AdaptIQ is a full-stack adaptive learning app:

- FastAPI backend on `http://localhost:8000`
- React/Vite frontend on `http://localhost:5173`
- PostgreSQL for users, questions, sessions, mastery, governance, and admin data
- Redis for runtime cache/session/quota behavior
- Backend-only LLM/RAG integration for quiz and chat generation
- Admin dashboard inside the React app at `/admin`

There is no standalone Python admin server on `:9000`. Any old `admin_server.py`, `localhost:9000`, or `c:\Users\mns\Desktop\mw\mhd` instruction is historical and should not be followed.

## 2. Correct Reading Order

Read by entry points and flows, not random files.

1. Backend startup:
   - `backend/main.py`
   - `backend/config.py`
   - `backend/database/*`
2. Backend routers:
   - `backend/routers/auth.py`
   - `backend/routers/classic_room.py`
   - `backend/routers/challenge.py`
   - `backend/routers/custom.py`
   - `backend/routers/visual_room.py`
   - `backend/routers/pvp.py`
   - `backend/routers/admin.py`
   - `backend/routers/governance.py`
3. Backend services:
   - `backend/services/*`
4. Database models and migrations:
   - `backend/database/*`
   - `backend/alembic/versions/*`
5. Frontend shell:
   - `frontend/src/App.tsx`
   - `frontend/src/context/AuthContext.tsx`
   - `frontend/src/services/http.ts`
6. Frontend pages and services:
   - `frontend/src/pages/*`
   - `frontend/src/services/*`
7. Tests:
   - `backend/tests/*`
   - `frontend/tests/*`
   - `docs/api/AdaptIQ_Complete_Postman.json`
8. Standalone live scripts:
   - `backend/scripts/live_validation/*`

For any feature, trace:

```text
frontend page -> frontend service -> backend route -> backend service -> database model -> migration -> tests
```

Example for custom-topic approval:

```text
AdminDashboard.tsx
-> adminService.ts
-> GET /api/admin/custom-topics/candidates
-> POST /api/admin/custom-topics/approve
-> routers/admin.py
-> custom_topics/custom_facts
-> 20260604_01_add_custom_fact_source_question.py
-> 20260604_02_add_concept_scope.py
-> 20260604_03_create_visual_tables.py
-> 20260604_04_drop_global_concept_name_unique_index.py
-> 20260611_01_add_visual_session_time.py
-> 20260611_02_add_visual_session_streaks.py
-> 20260704_01_add_user_responses_user_created_index.py
-> 20260704_02_add_question_calibration_shadow.py   (current head)
-> test_custom_topic_approval.py
-> admin-custom-topics.spec.ts
```

## 3. Backend Startup

Start with `backend/main.py`.

It creates the FastAPI app, configures middleware, runs startup/lifespan initialization, prepares Redis/DB/LLM app state, mounts routers, and controls public docs exposure.

Important startup questions:

- How is the FastAPI app created?
- Which routers are mounted?
- What middleware is installed?
- How are CORS origins parsed?
- How are public docs/OpenAPI enabled or disabled?
- How are PostgreSQL and Redis initialized?
- How is the LLM client attached?

Production public docs are disabled unless:

```powershell
ENABLE_PUBLIC_DOCS=true
```

Local `/health` is intentionally minimal:

```json
{ "status": "ok" }
```

Detailed dependency status belongs behind admin-only endpoints.

## 4. Backend Config

`backend/config.py` loads environment values such as:

- `DATABASE_URL`
- `REDIS_PASSWORD`
- `REDIS_HOST_PORT`
- `REDIS_URL`
- `GROQ_API_KEY`
- `JWT_SECRET_KEY`
- `ENVIRONMENT`
- `ENABLE_PUBLIC_DOCS`
- `ENABLE_TRUSTWORTHY_GENERATION`
- `DEV_BYPASS_AUTH`

It also owns the main tuning knobs that should not be hardcoded in routers:

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
- `CHALLENGE_POINTS_LEVEL_1` through `CHALLENGE_POINTS_LEVEL_5`
- `CHALLENGE_STREAK_UP_THRESHOLD`
- `CHALLENGE_STREAK_DOWN_THRESHOLD`
- `CHALLENGE_RANK_D_MIN`, `CHALLENGE_RANK_C_MIN`, `CHALLENGE_RANK_B_MIN`, and `CHALLENGE_RANK_A_MIN`
- `CHALLENGE_SESSION_QUESTION_TTL_SECONDS`

Redis can run on default host port `6379`, or on `6380` when local Memurai owns `6379`.

Security-sensitive config rules:

- Do not enable `DEV_BYPASS_AUTH` in production.
- Do not use a weak/default JWT secret.
- Browser CSRF uses a per-login double-submit token from `routers/auth.py`, not a
  separate configured signing secret.
- Do not commit `.env`.
- Do not expose LLM keys through Vite/browser code.

## 5. Backend Router Layer

Routers define the HTTP contract. For every route, identify:

- method and path
- request schema
- response shape
- auth dependency
- admin/ownership checks
- service calls
- direct DB writes
- rate limits

Main prefixes:

| Area | Prefix | Purpose |
|---|---|---|
| Auth | `/api/auth` | signup, login, logout, `/me`, reset password, bootstrap admin |
| Classic | `/api/rooms/classic` | adaptive training questions, hints, answers |
| Challenge | `/api/challenge` | rank, sessions, generated questions, submit answers |
| Custom | `/api/custom` | topics, sessions, generated custom questions, hints |
| Visual | `/api/visual` | visual room sessions, questions, hints, explanations |
| PvP | `/api/pvp` | matchmaking, match state, answers, leaderboard |
| Onboarding | `/api/onboarding` | survey, flags, tour state |
| Chat | `/api/chat` | scholar chat assistant |
| Admin | `/api/admin` | dashboard data, users, questions, concepts, DB inspector |
| Governance | `/api/admin/governance` | block rules and audit logs |

Routers should stay as thin as practical. Heavy business rules belong in services.

## 6. Backend Service Layer

Services enforce behavior:

| Service | Responsibility |
|---|---|
| `challenge_service.py` | rank, level, streaks, points, challenge session updates |
| `classic_service.py` | concept-aware question selection and answer processing |
| `custom_service.py` | topic catalogue, fact selection, custom prompt support |
| `visual_room_service.py` | visual question generation, answer checking, SVG-safe payloads |
| `pvp_service.py` | queue, matchmaking, match answers, Elo updates |
| `chat_service.py` | chat scope checks, prompt-injection handling, answer synthesis |
| `governance_service.py` | block rules, question audits, persisted governance fields |
| `concept_service.py` | concept discovery and question-concept repair support |
| `rate_limits.py` | shared rate-limit decorators/budgets |
| `security_utils.py` | redaction and security helpers |

When reading a service, ask:

- Which tables does it read/write?
- Does it trust frontend input?
- Does it check current user ownership?
- Does it call Redis?
- Does it call LLM/RAG?
- Does it write logs?
- What happens on failure?

## 7. Database And Migrations

Models live in `backend/database/`; migrations live in `backend/alembic/versions/`.

Core data areas:

| Table Area | Purpose |
|---|---|
| `users` | accounts, points, admin flag, active flag, ban fields |
| `question_bank` | reusable questions, options, answers, explanations, concepts, governance fields |
| `question_concepts` | many-to-many question/concept links with primary concept marker |
| `concepts` | concept taxonomy |
| `user_responses` | answer history |
| `challenge_sessions` / `challenge_answers` | competitive challenge state and answer records |
| `custom_topics` / `custom_facts` | approved custom rooms and harvested facts |
| `visual_*` tables | visual room content and sessions |
| `pvp_*` tables | queue, matches, answers, Elo ratings |
| `governance_block_rules` / `question_audits` | content safety rules and decision history |

Important current migration idea:

- `custom_facts.source_question_id` records provenance back to `question_bank`.
- `concepts.scope` stores a narrower context so `concepts.name` can be a direct concept name and `concepts.topic` can remain the broad family.

Question-bank source provenance:

- `QuestionBank.source` describes where a row came from.
- Seed/admin/import rows are different from generated rows.
- Generated rows can come from classic, challenge, custom, template, RAG, probe, or generic LLM paths.
- Admin overview groups sources into `generated`, `seeded`, `admin`, `unknown`, `by_category`, and `by_source`.
- Classic and challenge pools use the shared source taxonomy to keep room-specific generated rows out of unrelated selection pools.

Concept naming:

- `Concept.topic` is the broad family such as `history`, `geography`, or `mixed`.
- `Concept.scope` is the narrower context such as `France`, `World War I`, or `general`.
- `Concept.name` should be the direct concept name, not a prefixed value such as `Mixed - Core Concepts`.

Run migration checks:

```powershell
Set-Location backend
.venv\Scripts\python.exe -m alembic heads
.venv\Scripts\python.exe -m alembic current
.venv\Scripts\python.exe -m alembic upgrade head
```

## 8. Auth And Permissions

Browser auth uses cookies:

- `adaptiq_access`: HttpOnly access cookie
- `adaptiq_csrf`: readable CSRF cookie
- `X-CSRF-Token`: header required for unsafe cookie-authenticated requests

Bearer tokens remain temporarily supported for scripts, tests, Newman/Postman, and non-browser clients.

Login flow:

```text
Login.tsx
-> POST /api/auth/login
-> auth.py verifies password
-> auth.py sets access + CSRF cookies
-> AuthContext stores user only in React memory
-> future authFetch calls include credentials and CSRF
```

Frontend must not persist:

```text
adaptiq_token
adaptiq_user
adaptiq_scholar_history
```

Frontend route guards are UX only. Backend dependencies must enforce real security.

## 9. Frontend Foundation

Frontend entry points:

- `frontend/src/App.tsx`: route map
- `frontend/src/context/AuthContext.tsx`: user state and `/api/auth/me` refresh
- `frontend/src/services/http.ts`: shared fetch helper with cookies and CSRF
- `frontend/src/config.ts`: API base URL

Important pages:

| Page | Purpose |
|---|---|
| `Login.tsx` / `Signup.tsx` | auth forms |
| `Dashboard.tsx` | room navigation and user state |
| `ClassicRoom.tsx` | adaptive training UI |
| `ChallengeRoom.tsx` | ranked challenge UI |
| `CustomRoom.tsx` | custom topic learning UI |
| `VisualRoomQuiz.tsx` | visual room UI |
| `PvPRoom.tsx` | matchmaking and 1v1 match UI |
| `AdminDashboard.tsx` | authenticated admin dashboard |
| `ChatAssistant.tsx` | scholar chat widget |

The frontend does not own provider API keys and does not call LLM providers directly.

## 10. Main Feature Flows

### Auth

```text
Login/Signup page
-> authFetch / fetch
-> /api/auth/login or /api/auth/signup
-> users table
-> access cookie + CSRF cookie
-> AuthContext user memory
-> protected route
```

### Classic Room

```text
ClassicRoom.tsx
-> apiService.ts
-> /api/rooms/classic/questions
-> classic_room.py
-> ClassicService
-> question_bank + concepts + Redis current question
-> UI renders safe question payload
```

Answer:

```text
ClassicRoom.tsx
-> /api/rooms/classic/answers
-> server verifies issued question
-> user_responses + concept theta update
-> explanation and next state returned
```

### Challenge Room

Challenge scoring/progression is config-driven through `backend/config.py` defaults.

```text
ChallengeRoom.tsx
-> challengeService.ts
-> /api/challenge/start-session
-> /api/challenge/generate-question
-> question_bank persistence and governance
-> /api/challenge/submit-answer
-> challenge_answers unique session/question guard
-> challenge_sessions score/streak/level update
```

Duplicate submit protection must return one success and one conflict without double-mutating aggregate state.

### Custom Room

```text
CustomRoom.tsx
-> customService.ts
-> /api/custom/topics
-> built-in catalogue + approved custom_topics
-> /api/custom/start-session
-> /api/custom/generate-question
-> custom_facts/RAG/LLM fallback
-> governance before persistence/serving
```

Admin custom-topic approval:

```text
AdminDashboard.tsx topics tab
-> adminService.fetchCustomTopicCandidates()
-> GET /api/admin/custom-topics/candidates
-> candidate rows from catalogue and question_bank coverage
-> approveCustomTopic()
-> POST /api/admin/custom-topics/approve
-> custom_topics upsert
-> custom_facts harvested from eligible question_bank rows
-> /api/custom/topics includes approved topic
```

### Visual Room

Visual Room routes require authenticated users and session ownership.

```text
VisualRoomQuiz.tsx
-> visualRoomService.ts
-> /api/visual/start-session
-> /api/visual/next
-> /api/visual/submit
-> /api/visual/hint
-> /api/visual/explanation
-> /api/visual/session/{session_id}/end
```

Frontend must render structured safe SVG data, not raw injected SVG HTML.

Visual session lifecycle:

- `ended_at` is the UTC close/completion timestamp.
- `NULL` means the session is active or has not been closed.
- Natural completion and explicit end both set `ended_at` when it is missing.
- Repeated end calls should be idempotent.

### PvP

```text
PvPRoom.tsx
-> pvpService.ts
-> /api/pvp/join-queue
-> pvp_service matchmaking
-> pvp_matches with shared question set
-> /api/pvp/match/{id}/answer
-> server verifies question index and answer
-> /api/pvp/match/{id}/end
-> idempotent Elo/rating update
```

### Chat Assistant

```text
ChatAssistant.tsx
-> scholarService.ts
-> /api/chat
-> chat_router.py
-> chat_service scope check
-> prompt-injection checks
-> RAG context treated as untrusted quoted data
-> backend LLM answer
```

The chat flow should never expose raw system prompts, provider keys, or private user data.

## 11. Admin Dashboard

Admin dashboard is inside the React app:

```text
http://localhost:5173/admin
```

It requires:

- authenticated user
- `is_admin=true`
- backend admin dependencies

Tabs:

- overview
- users
- questions
- sessions
- concepts
- topics
- governance
- inspector
- monitoring

The DB inspector is read-only and backend-redacted.

The `topics` tab approves custom topic candidates and seeds facts.

## 12. Governance

Governance is controlled by `ENABLE_TRUSTWORTHY_GENERATION`.

When enabled, the service can:

- evaluate generated candidates before persistence
- evaluate bank rows before serving
- apply decision fields to `question_bank`
- log `question_audits`
- reject rows matching active block rules

Important question-bank fields:

- `gov_approved`
- `gov_safe`
- `gov_confidence`
- `gov_fact_trust`
- `gov_narrative_quality`
- `gov_sources_json`
- `gov_flags_json`
- `gov_checked_at`

Data repair can backfill unchecked governance state for existing rows.

## 13. Operations And Cleanup

Start infrastructure:

```powershell
Set-Location backend
# Docker Desktop/daemon must be running before this command.
docker compose up -d
docker compose ps -a
```

Start backend:

```powershell
Set-Location backend
.venv\Scripts\python.exe main.py
```

Start frontend:

```powershell
Set-Location frontend
npm run dev
```

Cleanup generated users:

```powershell
Set-Location backend
.venv\Scripts\python.exe scripts\cleanup_test_users.py --dry-run
.venv\Scripts\python.exe scripts\cleanup_test_users.py --apply --yes
```

Repair data:

```powershell
Set-Location backend
.venv\Scripts\python.exe scripts\repair_data_integrity.py --dry-run
.venv\Scripts\python.exe scripts\repair_data_integrity.py --apply
```

Visual geography shape utilities:

```powershell
Set-Location backend
.venv\Scripts\python.exe scripts\visual\ingest_shapes_v2.py
.venv\Scripts\python.exe scripts\visual\fix_fr_no.py
```

These scripts keep downloaded Natural Earth files in `backend/generated/visual_shapes/`.

The cleanup script targets explicit generated patterns such as `test`, `copilot`, `e2e`, `flowtest`, `pw-smoke`, `geo_scope`, `sec_cookies`, `auditpvp`, and related plus-alias emails. It reports redacted pattern buckets instead of raw email addresses.

Ad hoc backend helper process logs and stale pid files should live under `backend/logs/runtime/<date>/`, not in the backend root.

## 14. Testing

Backend:

The full backend suite includes a live HTTP integration test. Run it only after
the Compose services and backend API are up.

```powershell
Set-Location backend
.venv\Scripts\python.exe -m pytest -q tests
```

Frontend:

The Playwright e2e suite uses the built frontend and calls the live backend API.

```powershell
Set-Location frontend
npm run lint
npm run build
npm run test:e2e
```

Postman/Newman:

```powershell
npx newman run docs/api/AdaptIQ_Complete_Postman.json --reporters cli,json --reporter-json-export docs/reports/newman_run_latest.json
.\backend\.venv\Scripts\python.exe scripts\sanitize_newman_report.py docs\reports\newman_run_latest.json --in-place
```

Standalone live API scripts live under `backend/scripts/live_validation/` and are run explicitly against an already-running backend.

After live/e2e/browser tests, run generated-user cleanup so the local database does not accumulate test accounts.

## 15. How To Explain Any File

For each important function/class, answer:

- What input does it accept?
- What trust boundary does it cross?
- What user or admin permission is required?
- What service/database table does it touch?
- What state does it mutate?
- What errors can it raise?
- What test covers it?

Example:

```text
approveCustomTopic(candidate)
-> sends candidate type/name/slug/source_topic/max_facts
-> admin route requires authenticated admin
-> backend creates/updates custom_topics
-> backend harvests eligible approved/safe question_bank rows
-> backend writes custom_facts with source_question_id
-> UI refreshes candidates
```

That style is the safest way to understand the project deeply.
