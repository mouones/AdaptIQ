# AdaptIQ Layered Project Walkthrough

This is the active layered walkthrough for the live `P_F_E` app. Older reference copies are useful historically, but this file should track current code and runtime behavior.

## Frontend Foundation

The frontend is a React/TypeScript/Vite app. Routes live in `frontend/src/App.tsx`; pages live in `frontend/src/pages`; reusable API calls live in `frontend/src/services`.

The browser does not persist private auth state in `localStorage`. Auth state is kept in React memory and refreshed from `/api/auth/me`. Shared API requests use `authFetch`, which sends cookies and CSRF headers.

The admin dashboard includes a custom-topic approval tab that calls the backend candidate and approval endpoints.

## API And Backend Logic

The backend is a FastAPI app. `backend/main.py` configures app lifecycle, middleware, docs exposure, health behavior, Redis/DB startup, and router inclusion.

Primary router areas:

- Auth and user profile
- Classic room
- Challenge room
- Custom room
- Visual room
- PvP room
- Onboarding
- Chat assistant
- Admin operations

LLM behavior is centralized on the backend. The frontend should not call Gemini/Groq or ship model API keys.

## Database And Storage

PostgreSQL stores users, question bank data, concept links, sessions, challenge answers, custom topics/facts, governance rules/audits, and telemetry-backed admin views.

Redis supports runtime session/cache/quota behavior and is expected to run through Docker locally. Memurai can conflict with host port `6379`; use the Redis recovery notes in `CLAUDE.md`.

Alembic owns migrations. Current provenance/schema work includes `custom_facts.source_question_id` and `concepts.scope`.

`QuestionBank.source` is retained as provenance. Admin overview groups source values into generated, seeded, admin, unknown, category counts, and raw source counts. Room selection uses the same taxonomy so challenge/custom generated rows do not leak into unrelated pools.

Concept rows use `topic` for broad family, `scope` for narrower context, and `name` for the direct concept label.

## Auth And Permissions

Browser auth uses:

- `adaptiq_access`: HttpOnly access-token cookie
- `adaptiq_csrf`: readable CSRF cookie
- `X-CSRF-Token`: required for unsafe cookie-authenticated requests

Temporary Bearer compatibility exists for scripts, integration tests, and Postman.

Admin APIs require authenticated admins. Visual room APIs require authenticated users and session ownership. The admin DB inspector redacts sensitive fields.

Visual sessions use `ended_at` as the UTC close/completion timestamp; a `NULL` value means active or not closed.

## Config And Runtime Tuning

Room and operational defaults live in `backend/config.py` rather than route literals. Important knobs include classic/Visual/PvP question counts, PvP candidate pool size, custom generation target/recent limit, challenge points/streak/rank/TTL settings, admin DB inspector limits, data-repair batch size, and generated-user cleanup batch size.

## Security And Low-Level Security

Security controls include:

- production docs disabled by default
- minimal public health response
- route/user quotas and rate limiting
- prompt-injection and XSS-oriented chat hardening
- frontend removal of token/profile/chat persistence
- backend-only LLM keys
- sensitive log and admin inspector redaction
- secret scanning script

Remaining improvement work should focus on deeper observability, stronger analytics governance, centralized security event reporting, and long-term removal of temporary Bearer compatibility.

## Hosting And Deployment

Docker Compose is the local baseline. Services bind to localhost for development. Production should use managed PostgreSQL/Redis or hardened containers, real CORS origins, rotated secrets, SMTP, public docs disabled, and separate static hosting/CDN for frontend assets.

## Caching, CDN, And Load Balancing

Redis is the runtime cache/session/quota layer. Frontend `dist/` can be served through a CDN. Authenticated API responses should not be cached by shared proxy/CDN layers.

Backend instances can scale horizontally when they share PostgreSQL, Redis, JWT/CSRF secrets, and LLM configuration. LLM/RAG latency should be monitored independently.

## Logs, Availability, And Recovery

Public `/health` is minimal. Detailed dependency health is admin-only.

Operational recovery centers on:

- Docker service health
- Redis/Memurai port conflict handling
- Alembic current/head checks
- DB integrity dry-run/apply
- generated test-user cleanup
- Newman and Playwright smoke validation

## Product Analytics

Recommended privacy-safe product analytics events:

- signup and login success/failure
- onboarding start/complete
- room start/end
- question shown/submitted
- hint requested
- custom topic approved
- chat question accepted/rejected
- rate limit hit
- frontend route error

Analytics events should avoid raw prompts, emails, tokens, OTPs, and full question/answer text unless explicitly redacted or aggregated.
