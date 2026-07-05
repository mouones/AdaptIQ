# AdaptIQ

**An adaptive learning platform that serves every learner the *right* question at
the *right* difficulty — in real time.**

![AdaptIQ home](docs/images/home.png)

AdaptIQ estimates each learner's ability **per concept** using Item Response
Theory and serves the next question inside their **Zone of Proximal Development**
(a 60–75% success band). Because generating fact-checked, correctly-difficult
questions is slow, that work happens **ahead of time** in a background worker that
keeps Redis queues warm — so the gameplay stays instant.

> Tech stack: **FastAPI** (async) · **React + TypeScript** (Vite, Tailwind) ·
> **PostgreSQL** · **Redis** · **Groq LLM** + agentic **RAG**.

### Features

- 🎯 **Per-concept adaptive difficulty** — 1PL IRT ability tracking (θ) with ZPD
  targeting and spaced repetition.
- 🧩 **Five game rooms** — Classic (adaptive), Challenge (ranked E→A), Custom
  (your topics), PvP (1-v-1 ELO duels), Visual (map/geography).
- 🤖 **Grounded question generation** — LLM + multi-source RAG (Wikipedia,
  Wikidata, DBpedia, open data) with validation & governance so no hallucinated
  "facts" are served.
- 📊 **Admin dashboard** — users, questions, sessions, concepts, governance, DB
  inspector, monitoring, custom-topic approval.
- 🏆 **Engagement** — points, levels (Novice→Master), streaks, leaderboards.
- 🔐 **Security-first** — HttpOnly cookie + CSRF auth, admin authorization,
  rate limits, redacted logs, startup guardrails.

### 📖 Understand the design

The **[design dossier](docs/design/README.md)** explains the reasoning behind
AdaptIQ end to end — pre-product study, requirements, architecture & technology
choices, the learning engine (IRT/ELO/leveling/mastery), and the
quality/performance engineering. Start there to understand *why* it is built this
way.

> This README is the operational entry point (how to run it). `docs/reports/`
> holds dated audits and the quality/performance roadmap.

## Screenshots

| Sign in | Create account |
|---|---|
| ![Login](docs/images/login.png) | ![Sign up](docs/images/signup.png) |

## Quick Start

```powershell
Set-Location backend
# Requires Docker Desktop/daemon to be running.
docker compose up -d
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe main.py

Set-Location ..\frontend
npm install
npm run dev
```

Local URLs:

- Frontend: `http://localhost:5173`
- Backend: `http://localhost:8000`
- Admin dashboard: `http://localhost:5173/admin`
- Local API docs: `http://localhost:8000/docs` when public docs are enabled
- PostgreSQL Docker port: `127.0.0.1:5433`
- Redis Docker port: `127.0.0.1:6379`, or `6380` if Memurai owns `6379`

## Architecture Layers

### Frontend Foundation

- React, TypeScript, Vite, Tailwind, React Router.
- Auth state lives in React memory and is refreshed with `/api/auth/me`.
- API calls go through shared fetch helpers that include cookies and CSRF headers.
- The admin dashboard includes overview, users, questions, sessions, concepts, governance, DB inspector, monitoring, and custom-topic approval.
- There is no active standalone admin server on `localhost:9000`; old instructions for that server are archived.

### API And Backend Logic

- FastAPI app in `backend/main.py`.
- Router prefix is `/api`; main domains are auth, classic, challenge, custom, visual, PvP, onboarding, chat, and admin.
- Business logic is split across `backend/services/`; route handlers should stay thin where possible.
- LLM calls are backend-only. Browser-side LLM keys are not part of the live design.

### Database And Storage

- PostgreSQL stores users, question bank rows, concept links, room sessions, custom topics/facts, governance data, and analytics records.
- Alembic owns schema changes; the current chain is single-head through `20260704_02_add_question_calibration_shadow.py` (shadow calibration columns), which follows the `user_responses` composite-index and visual-session-streak migrations.
- `scripts/repair_data_integrity.py` audits and repairs question concepts, explanations, option payloads, custom topic/fact state, and placeholder/sample data issues.
- The repair script also reports unchecked `question_bank` governance state and can backfill governance fields when trustworthy generation is enabled.
- `scripts/cleanup_test_users.py` removes generated test users without targeting real users.
- Visual geography shape helpers live in `backend/scripts/visual/`; downloaded Natural Earth files live in `backend/generated/visual_shapes/`.
- `QuestionBank.source` is provenance. Admin overview groups it into generated, seeded, admin, unknown, source category, and raw source counts.
- `Concept.topic` is the broad family, `Concept.scope` is the narrower context, and `Concept.name` is the direct display concept.

### Auth And Permissions

- Browser auth uses `adaptiq_access` as an HttpOnly cookie.
- CSRF protection uses the readable `adaptiq_csrf` cookie plus `X-CSRF-Token` on unsafe requests.
- Bearer tokens remain temporarily supported for scripts, tests, Newman/Postman, and non-browser clients.
- Admin endpoints require authenticated admin users; DB inspector responses redact sensitive fields.

### Hosting And Deployment

- Docker Compose runs PostgreSQL, Redis, pgAdmin, Redis Commander, and optional backend service.
- Public service bindings are restricted to localhost in local compose.
- Production should set `ENVIRONMENT=production`, disable auto table creation, rotate secrets, configure real CORS origins, and enable SMTP.
- Public docs/OpenAPI are disabled by default in production unless explicitly enabled.

### Compute, CI/CD, And Version Control

- `CLAUDE.md` is the active development/operations runbook.
- Keep generated artifacts out of commits: backend logs/generated files, frontend `dist/`, Playwright reports, and local env files.
- Run backend, frontend, secret scan, DB integrity, and Newman checks before merging security-sensitive work.
- Do not rewrite user changes in a dirty worktree; layer changes carefully and inspect conflicts.
- Do not commit until explicitly requested.
- Older cleanup/reference material was archived outside this repository during development and is not part of the published project.

### Security And Low-Level Security

- Do not print or commit `.env` secrets.
- Use HttpOnly cookies, CSRF, admin authorization, route rate limits, redacted logs, and sensitive-column redaction.
- Visual Room and admin data paths must enforce authenticated ownership/admin checks.
- Chat and LLM prompts treat retrieved context and user input as untrusted data.

### Rate Limiting

- Route limits exist for auth, chat/LLM-heavy flows, visual room, challenge/custom paths, and admin-sensitive operations.
- Redis-backed quotas should be preferred for per-user or model-budget enforcement.
- Account-based login throttling and progressive lockout should remain part of security regression coverage.
- Room and budget defaults are configurable in `backend/config.py`, including classic/Visual/PvP question counts, custom generation targets, challenge scoring/rank thresholds, admin DB inspector limits, repair batch size, and cleanup batch size.

### Caching And CDN

- Redis is used for runtime cache/session/quota behavior.
- Frontend static assets can be CDN-hosted after `npm run build`.
- Do not cache authenticated API responses at shared proxy/CDN layers.

### Load Balancing

- Stateless API scaling is possible when instances share PostgreSQL, Redis, and consistent secrets.
- Sticky sessions should not be required for browser auth because cookies are verified server-side.
- LLM and RAG latency should be budgeted separately from normal API paths.

### Logs, Observability, And Analytics

- Runtime telemetry tracks request counts, errors, latency, and rate-limit events.
- Logs must redact tokens, OTPs, emails where possible, prompt snippets, and secrets.
- Ad hoc backend server stdout/stderr and stale pid files belong under `backend/logs/runtime/<date>/`, not in `backend/`.
- Product analytics should track privacy-safe funnels: signup, onboarding completion, room starts, question submissions, hint usage, custom-topic approval, retention, and error recovery.

### Availability And Recovery

- `/health` is intentionally minimal: `{ "status": "ok" }`.
- Detailed dependency health belongs behind authenticated admin endpoints.
- Recovery docs cover Redis/Memurai conflicts, Docker port checks, Alembic migration state, DB repair dry-runs, and test-user cleanup.

## Validation

The backend suite includes a live HTTP integration test, and frontend e2e includes
a live signup smoke. Start Docker Compose and the backend first; otherwise those
checks fail with service-unavailable signup responses even when offline/unit tests
are healthy.

```powershell
Set-Location backend
.venv\Scripts\python.exe -m pytest -q tests
.venv\Scripts\python.exe scripts\repair_data_integrity.py --dry-run

Set-Location ..\frontend
npm run lint
npm run build
npm run test:e2e

Set-Location ..
.\backend\.venv\Scripts\python.exe scripts\scan_secrets.py
```

Postman/Newman collection:

- `docs/api/AdaptIQ_Complete_Postman.json`
- Latest JSON report target: `docs/reports/newman_run_latest.json`
- Sanitize saved reports with `scripts/sanitize_newman_report.py --in-place` before keeping them.

## Deep Reading Map

Use this order when explaining or auditing the project:

1. `backend/main.py` and `backend/config.py` for startup, CORS, docs exposure, app state, Redis, DB, and LLM setup.
2. `backend/routers/*` for HTTP contracts, auth dependencies, ownership checks, and admin checks.
3. `backend/services/*` for room logic, governance, LLM/RAG behavior, rate limits, chat, and scoring.
4. `backend/database/*` and `backend/alembic/versions/*` for tables, foreign keys, indexes, and migrations.
5. `frontend/src/App.tsx`, `AuthContext.tsx`, and `services/http.ts` for routes, cookie auth, and CSRF.
6. `frontend/src/pages/*` and `frontend/src/services/*` for UI flows and request construction.
7. `backend/tests`, `frontend/tests`, and the Postman collection for executable contracts.

The full walkthrough is in `docs/walkthrough.md`.

## Cleanup And Rollback

- Use `git status --short` before staging anything.
- Generated test users: run `backend/scripts/cleanup_test_users.py --dry-run`, inspect counts, then run `--apply --yes`.
- Cleanup includes explicit generated prefixes such as `test`, `copilot`, `e2e`, `flowtest`, `pw-smoke`, `geo_scope`, `sec_cookies`, `auditpvp`, and plus-alias users. Output is redacted and bucketed.
- Data integrity: run `backend/scripts/repair_data_integrity.py --dry-run`, inspect planned changes, then run `--apply`.
- No rollback commit has been made automatically; create one only when explicitly requested.

## Current Runbooks

- Development/runtime notes: `CLAUDE.md`
- Documentation index: `docs/README.md`
- Backend notes: `backend/README.md`
- Frontend notes: `frontend/README.md`
- Security audit: `docs/reports/SECURITY_AUDIT_2026-06-03.md`
- Project state audit: `docs/reports/PROJECT_STATE_AUDIT_2026-07-04.md` (latest; supersedes the 2026-06-04 audit)
- Quality & performance roadmap: `docs/reports/QUALITY_PERF_ROADMAP_2026-07-04.md`
