# AdaptIQ Backend

FastAPI backend for AdaptIQ adaptive learning, quiz rooms, chat, admin operations, PostgreSQL persistence, Redis-backed runtime state, and backend-only LLM generation.

## Runtime Shape

- App entry: `main.py`
- Config: `config.py`
- Routers: `routers/`
- Services: `services/`
- ORM models: `database/`
- Alembic migrations: `alembic/versions/`
- Local Compose services: PostgreSQL, Redis, pgAdmin, Redis Commander, optional backend container

Active API prefixes:

- `/api/auth`
- `/api/rooms/classic`
- `/api/challenge`
- `/api/custom`
- `/api/visual`
- `/api/pvp`
- `/api/onboarding`
- `/api/chat`
- `/api/admin`

## Local Setup

```powershell
Set-Location backend
# Requires Docker Desktop/daemon to be running.
docker compose up -d
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
alembic upgrade head
python main.py
```

Local backend URL: `http://localhost:8000`.

Production disables public docs/OpenAPI unless `ENABLE_PUBLIC_DOCS=true`. Local development can enable docs explicitly.

## Security Notes

- Browser auth uses an HttpOnly `adaptiq_access` cookie.
- CSRF uses readable `adaptiq_csrf` plus `X-CSRF-Token` on unsafe cookie-authenticated requests.
- Bearer auth remains temporarily supported for scripts, integration tests, and Newman/Postman.
- Admin routes require authenticated admins; DB inspector output redacts sensitive columns and values.
- `/health` returns only `{ "status": "ok" }`.
- Do not log or commit `.env` secrets, OTPs, tokens, raw prompts, or API keys.

## Redis And Docker

Redis should normally publish `127.0.0.1:6379`. If Memurai owns that port, either stop Memurai or run Docker Redis on the alternate host port:

```powershell
$env:REDIS_HOST_PORT = "6380"
$env:REDIS_COMMANDER_HOST_PORT = "8082"
docker compose up -d --force-recreate redis redis-commander
```

Keep `REDIS_URL` or `REDIS_HOST_PORT` aligned with the port actually used.

## Database And Data Integrity

Use Alembic for schema changes:

```powershell
alembic heads
alembic current
alembic upgrade head
```

Data repair and audit:

```powershell
.venv\Scripts\python.exe scripts\repair_data_integrity.py --dry-run
.venv\Scripts\python.exe scripts\repair_data_integrity.py --apply
.venv\Scripts\python.exe scripts\cleanup_test_users.py --dry-run
```

The repair script checks question concepts, primary concept mismatch, invalid options JSON, placeholder/sample answers, blank explanations, dangling links, and custom topic/fact state.

Source and concept semantics:

- `QuestionBank.source` records provenance. Known groups include seed/admin/import rows, generic generated rows, classic generated rows, challenge generated rows, custom generated/template/RAG rows, probes, and unknown values.
- Admin overview reports source groups as `generated`, `seeded`, `admin`, `unknown`, `by_category`, and `by_source`.
- Room selection uses the shared source taxonomy so custom/challenge rows do not leak into classic pools.
- `Concept.topic` is the broad family, `Concept.scope` is the narrower context, and `Concept.name` is the direct concept name.
- Visual `ended_at` is the UTC close/completion timestamp; `NULL` means active or not closed.

Config knobs for room/runtime tuning:

- `CLASSIC_QUESTIONS_PER_SESSION`
- `VISUAL_QUESTIONS_PER_SESSION`
- `PVP_QUESTIONS_PER_MATCH`
- `PVP_CANDIDATE_POOL_SIZE`
- `CUSTOM_ROOM_GENERATION_TARGET`
- `CUSTOM_ROOM_RECENT_QUESTION_LIMIT`
- `CHALLENGE_POINTS_LEVEL_1` through `CHALLENGE_POINTS_LEVEL_5`
- `CHALLENGE_STREAK_UP_THRESHOLD`
- `CHALLENGE_STREAK_DOWN_THRESHOLD`
- `CHALLENGE_RANK_D_MIN`, `CHALLENGE_RANK_C_MIN`, `CHALLENGE_RANK_B_MIN`, `CHALLENGE_RANK_A_MIN`
- `CHALLENGE_SESSION_QUESTION_TTL_SECONDS`
- `ADMIN_DB_INSPECTOR_DEFAULT_LIMIT`, `ADMIN_DB_INSPECTOR_MAX_LIMIT`
- `DATA_REPAIR_BATCH_SIZE`, `CLEANUP_USER_BATCH_SIZE`

## Custom Topic Approval

Admin-approved Custom Room topics use:

- `GET /api/admin/custom-topics/candidates`
- `POST /api/admin/custom-topics/approve`
- `POST /api/admin/custom-topics/toggle-active`
- `GET /api/custom/topics`

Approved topics are stored in `custom_topics`. Harvested facts are stored in `custom_facts` with `source_question_id` provenance.

## Validation

Full pytest currently collects `tests/integration/test_challenge_idempotency.py`,
which calls the live backend at `http://127.0.0.1:8000`. Bring up Docker Compose
and start `main.py` before running the full suite. Without the live backend and
database, use focused unit tests instead of treating the live failure as a code
regression.

```powershell
.venv\Scripts\python.exe -m pytest -q tests
.venv\Scripts\python.exe -m pytest -q tests/unit/test_security_regressions.py tests/unit/test_auth_helpers.py tests/test_chat.py
.venv\Scripts\python.exe -m pytest -q tests/unit/test_db_integrity_audit.py tests/unit/test_question_concept_repair.py tests/unit/test_custom_topic_approval.py
.venv\Scripts\python.exe -m pytest -q tests/unit/test_config_runtime_knobs.py tests/unit/test_question_sources.py
```

Postman/Newman:

```powershell
npx newman run ..\docs\api\AdaptIQ_Complete_Postman.json --reporters cli,json --reporter-json-export ..\docs\reports\newman_run_latest.json
.venv\Scripts\python.exe ..\scripts\sanitize_newman_report.py ..\docs\reports\newman_run_latest.json --in-place
```

Standalone live validations are not collected by pytest. Run them explicitly after a backend is running:

```powershell
.venv\Scripts\python.exe scripts\live_validation\security_regressions_live.py
.venv\Scripts\python.exe scripts\live_validation\authenticated_flow_live.py
.venv\Scripts\python.exe scripts\live_validation\challenge_deep.py
.venv\Scripts\python.exe scripts\live_validation\custom_geo_scope_live.py
.venv\Scripts\python.exe scripts\live_validation\e2e_full.py
```

Secret scan from project root:

```powershell
.\backend\.venv\Scripts\python.exe scripts\scan_secrets.py
```
