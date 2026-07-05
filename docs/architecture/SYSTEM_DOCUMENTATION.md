# AdaptIQ System Documentation

This document summarizes the current live system. It is intentionally concise; use the source code, migrations, and tests for exact implementation details.

## System Overview

AdaptIQ is an adaptive learning platform with:

- FastAPI backend
- React/TypeScript frontend
- PostgreSQL database
- Redis runtime state/cache/quota layer
- Backend-only LLM integrations
- Admin dashboard and operational tooling

## Core User Flows

- Signup/login/logout with HttpOnly cookie auth and CSRF.
- Dashboard/profile access via `/api/auth/me`.
- Classic, challenge, custom, visual, and PvP quiz rooms.
- Chat assistant with scope and prompt-injection protections.
- Admin management for users, questions, concepts, governance, custom topics, DB inspection, and monitoring.

## Backend Responsibilities

- Authenticate and authorize users.
- Validate request payloads.
- Enforce rate limits and ownership checks.
- Generate or retrieve quiz/chat content.
- Persist answer/session/mastery data.
- Redact sensitive admin/log output.
- Expose minimal public health and admin-only diagnostics.

## Frontend Responsibilities

- Render room workflows and admin tools.
- Use shared API helpers for cookies and CSRF.
- Avoid persistent private localStorage state.
- Avoid browser-side LLM clients and API keys.
- Surface safe error/status messages without exposing secrets.

## Data Model Areas

- Users and auth/session state
- Question bank and concept links
- User responses and concept mastery
- Challenge sessions and answers
- Custom topics and facts
- Visual room tables
- PvP matches
- Governance rules/audits
- Monitoring/analytics-derived admin payloads

## Admin Custom Topic Approval

Admins can approve Custom Room topics from the dashboard or API.

Backend endpoints:

- `GET /api/admin/custom-topics/candidates`
- `POST /api/admin/custom-topics/approve`

User-facing endpoint:

- `GET /api/custom/topics`

Approval creates or updates `custom_topics` and harvests eligible question-bank rows into `custom_facts` with `source_question_id` provenance.

## Operational Validation

Recommended validation commands:

Full backend pytest and frontend e2e both exercise live HTTP paths. Ensure Docker
Compose is healthy and `backend/main.py` is running at `http://127.0.0.1:8000`
before using these as end-to-end validation.

```powershell
Set-Location backend
.venv\Scripts\python.exe -m pytest -q tests
.venv\Scripts\python.exe scripts\repair_data_integrity.py --dry-run

Set-Location ..\frontend
npm run lint
npm run build
npm run test:e2e
```

Run Newman from the project root when the backend is live:

```powershell
npx newman run docs/api/AdaptIQ_Complete_Postman.json --reporters cli,json --reporter-json-export docs/reports/newman_run_latest.json
```

## Known Improvement Areas

- Remove temporary Bearer compatibility after scripts and Postman migrate fully to cookie/CSRF or service tokens.
- Add richer privacy-safe product analytics.
- Add centralized security-event reporting.
- Add production-grade dashboards for Redis, DB, LLM availability, and rate-limit budgets.
- Formalize CDN/cache headers for static frontend assets.
- Add load-test profiles for LLM-heavy and quiz-heavy paths.
