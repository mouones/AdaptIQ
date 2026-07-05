# Admin Dashboard Runbook

The admin dashboard is available at `/admin` for authenticated users with `is_admin=true`.

## Capabilities

- Overview metrics for users, questions, responses, concepts, sessions, and PvP.
- Daily analytics and top-user summaries.
- User list, profile detail, profile edits, admin toggles, active toggles, and timed bans.
- Question-bank list, create, edit, and delete.
- Concept analytics, create, edit, delete, and force delete.
- Custom-topic approval from catalogue/question-bank candidates.
- Governance block rules and audit log.
- Read-only DB inspector with backend redaction.
- Monitoring telemetry for requests, errors, latency, and rate limits.

## Custom Topic Approval

The `topics` tab lets admins approve Custom Room topics without using Postman.

Flow:

1. Dashboard loads candidates from `GET /api/admin/custom-topics/candidates`.
2. Admin reviews topic name, slug, source, and eligible question count.
3. Admin clicks `Approve`.
4. Frontend posts to `POST /api/admin/custom-topics/approve`.
5. Backend creates/updates `custom_topics` and harvests facts into `custom_facts`.
6. `/api/custom/topics` includes the approved DB topic.

The UI disables approval when a candidate is already approved or has no eligible facts.

## Question Source Overview

`QuestionBank.source` is provenance metadata. The overview keeps legacy compatibility fields, but current reporting should use the clearer groups:

- `generated`: rows from LLM/template/RAG/probe generation paths.
- `seeded`: rows from seed fixtures or curated banks.
- `admin`: rows created manually through admin/import paths.
- `unknown`: rows with blank or unrecognized source values.
- `by_category`: normalized source category counts.
- `by_source`: raw source-value counts for debugging.

Room selection uses this taxonomy so challenge/custom generated rows do not enter unrelated classic pools.

## Concept Metadata

Concept rows use three layers:

- `topic`: broad family such as `history`, `geography`, or `mixed`.
- `scope`: narrower context such as a country, topic, war, or `general`.
- `name`: direct concept name for display and matching.

Avoid creating display names with redundant prefixes such as `Mixed - ...`; use `scope` for the context.

## Security Expectations

- All admin API calls use authenticated cookie auth plus CSRF through the shared frontend helper.
- Bearer auth remains supported for scripts and Postman, but the browser dashboard should not persist bearer tokens.
- Non-admin users are redirected away by the frontend route guard and rejected by backend admin dependencies.
- The DB inspector must never expose password hashes, OTP/reset fields, tokens, secrets, or other sensitive values.

## Validation

Backend:

```powershell
Set-Location backend
.venv\Scripts\python.exe -m pytest -q tests/unit/test_admin_content_db_inspector_contract.py tests/unit/test_custom_topic_approval.py
```

Frontend:

```powershell
Set-Location frontend
npm run lint
npm run build
npm run test:e2e
```

Postman/Newman:

```powershell
npx newman run docs/api/AdaptIQ_Complete_Postman.json --reporters cli,json --reporter-json-export docs/reports/newman_run_latest.json
```
