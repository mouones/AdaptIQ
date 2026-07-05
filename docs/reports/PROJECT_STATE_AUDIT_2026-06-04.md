# AdaptIQ Project State Audit - 2026-06-04

## Executive Summary

This pass checked the live `P_F_E` app, not archived/reference copies. The backend already contained admin custom-topic approval endpoints, but the frontend dashboard did not expose the workflow. The current dashboard now has typed frontend service calls, a `topics` tab, and a Playwright UI contract test.

Active documentation was also refreshed from code/config reality. Challenge tuning now has typed config defaults for points, streaks, rank thresholds, and temporary question TTLs.

## Changes In This Pass

- Added frontend admin custom-topic approval UI.
- Added `fetchCustomTopicCandidates` and `approveCustomTopic` admin service calls.
- Added Playwright coverage for the admin topic approval UI contract.
- Added a top-level project README with requested architecture/operations layers.
- Updated active docs index, backend README, frontend README, admin dashboard runbook, challenge config notes, architecture docs, and `CLAUDE.md`.
- Removed mojibake from rewritten active docs.
- Added source-provenance grouping for admin question overview metrics.
- Added room/config tuning knobs for classic, Visual, PvP, challenge, admin limits, cleanup, and data repair.
- Added concept `scope` metadata so concept names can stay direct while topic/scope preserve context.
- Clarified Visual `ended_at` as the UTC close/completion timestamp.

## Current Code-Truth Findings

### High Priority

- **Frontend admin approval gap was real.**
  - Backend endpoints existed: `GET /api/admin/custom-topics/candidates` and `POST /api/admin/custom-topics/approve`.
  - Frontend dashboard had no candidate list or approval action before this pass.
  - Status: present in the dashboard `topics` tab.

- **Some active docs were stale or misleading.**
  - `docs/operations/env_challenge_setup.md` documented env vars that the current backend does not read.
  - Several active docs contained mojibake and old route/config references.
  - Status: key active docs were rewritten; reference/history docs were intentionally not rewritten.

### Medium Priority

- **Main frontend bundle is large.**
  - `npm run build` completed but Vite warned that the main JS chunk exceeds 500 kB.
  - Recommendation: add route-level code splitting and manual vendor chunks.

- **Temporary Bearer-token compatibility remains.**
  - This is intentional for scripts/Postman/tests, but it should be removed after migration to safer automation auth.
  - Recommendation: set a deprecation date and migrate Postman/scripts to a dedicated non-browser service-token flow or cookie/CSRF where practical.

- **Product analytics is not yet a formal layer.**
  - Admin telemetry exists, but product events/funnels are not fully specified.
  - Recommendation: implement privacy-safe analytics for onboarding, room start/end, question submission, hint usage, custom-topic approval, chat rejection, and retention.

### Low Priority

- **Some historical docs remain stale by design.**
  - Old reference material has been moved to `../archive/2026-06-04/`; dated reports remain evidence for their dates.
  - Recommendation: do not rewrite old evidence reports; add dated replacement reports instead.

- **Challenge tuning now has config defaults.**
  - Points, streak thresholds, rank thresholds, and session-question TTLs are read from `backend/config.py`.
  - Recommendation: keep these knobs covered by config override tests before adding more challenge rules.

## Database Integrity Areas To Keep Watching

Existing repair/audit tooling should continue checking:

- `question_bank.primary_concept_id` nulls and mismatches.
- duplicate or missing primary `question_concepts`.
- dangling concept links.
- invalid options JSON.
- blank question text, answer, topic, or explanation.
- placeholder/sample answers such as `A`, `B`, `C`, `D`, `Option 1`, `Option 2`, or `Option 3`.
- `custom_topics` and `custom_facts` empty-state regressions.
- harvested fact provenance through `custom_facts.source_question_id`.
- question source taxonomy drift, including unknown sources and custom/challenge rows leaking into classic pools.
- concept names with redundant prefixes such as `Mixed - ...`.
- Visual sessions where `is_completed` and `ended_at` disagree.

## Security Improvement Backlog

### P0/P1

- Keep rotating any secret that was visible in IDE/chat if it is real.
- Keep production docs/OpenAPI disabled unless explicitly enabled.
- Keep public `/health` minimal and dependency health admin-only.
- Keep admin DB inspector redaction tests in regression coverage.
- Keep raw prompt, OTP, token, API key, and full email redaction in logs.

### P2

- Remove temporary Bearer compatibility after automation migration.
- Add centralized security-event telemetry for auth failures, CSRF failures, admin denials, rate-limit hits, and prompt-injection blocks.
- Add per-user LLM budget dashboards and alerts.
- Add dependency vulnerability scan to CI.

## Architecture And Scaling Suggestions

- Add frontend route-level lazy loading to reduce the main bundle.
- Add explicit cache-control headers for static assets and authenticated API responses.
- Add load-test profiles for normal quiz paths and LLM-heavy paths separately.
- Add production Redis/PostgreSQL backups and restore drills.
- Add dashboards for DB pool saturation, Redis latency, LLM availability, LLM latency, and rate-limit budget use.
- Add rollout checks for Alembic head consistency before backend start.

## Product Analytics Suggestions

Track privacy-safe events with stable IDs and redacted metadata:

- signup completed
- login succeeded/failed
- onboarding completed
- room started/ended by room type
- question shown/submitted
- hint requested
- chat question rejected/answered
- custom topic candidate approved
- user returned after 1/7/30 days
- frontend route error

Avoid raw prompts, emails, answer text, OTPs, tokens, and secrets in analytics payloads.

## Validation Status

Completed in the final validation pass:

- Backend full pytest: passed, 302/302, zero skipped; 4 jose datetime deprecation warnings remain.
- Frontend `npm run lint`: passed.
- Frontend `npm run build`: passed; Vite still warns that the main JS chunk is larger than 500 kB.
- Frontend `npm run test:e2e`: passed, 2/2 Chromium tests.
- Alembic heads/current: both report `20260604_04 (head)`.
- DB upgrade: applied `20260604_02`, `20260604_03`, and `20260604_04` to the local DB.
- DB repair: deterministic repairs applied; final dry-run has no automatic repairs remaining.
- DB repair residual: 17 concept-name normalization conflicts remain for manual review because the repair script refuses to merge conflicting scoped concept names automatically.
- Generated-user cleanup: applied after live/e2e/Newman runs; final dry-run reported `matched_users = 0`.
- Docker state: Postgres healthy on `127.0.0.1:5433`, Redis healthy on `127.0.0.1:6380`, Redis Commander healthy on `127.0.0.1:8082`.
- Runtime `/health`: returned only `{ "status": "ok" }`.
- Live security regression script: passed.
- Live authenticated flow script: passed.
- Live deep challenge script: passed.
- Live custom geography scope script: passed.
- Standalone backend live API script: passed, 93/93.
- Newman/Postman collection: passed, 104 requests and 148 assertions with zero failures.
- Newman JSON report: sanitized in place at `docs/reports/newman_run_latest.json`.
- Secret scan: passed, no secret patterns found.
- Project hygiene scan: passed.
