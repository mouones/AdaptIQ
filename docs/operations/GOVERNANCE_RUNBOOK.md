# Trustworthy Generation Governance Runbook

This runbook covers the feature-flagged trustworthy-generation governance layer:

- Enforces DB-persisted block rules **before writing questions to** `question_bank`.
- Re-checks questions **just before serving** them from `question_bank`.
- Records **audit logs** for persist/serve decisions.

## 1) Apply the database migration

From `backend/`:

```bash
alembic upgrade head
```

This migration:
- Adds `gov_*` fields to `question_bank`.
- Creates `governance_block_rules`.
- Creates `question_audits`.

## 2) Enable / disable the feature flag

Set the environment variable and restart the backend:

```bash
# Enable
ENABLE_TRUSTWORTHY_GENERATION=true

# Disable (default)
ENABLE_TRUSTWORTHY_GENERATION=false
```

When disabled:
- No governance checks run.
- No governance audit rows are written.

## 3) Manage blocked rules (admin API)

All endpoints below require an authenticated admin. Browser calls use the normal
cookie plus CSRF flow; scripts and Postman may still use temporary Bearer auth.

Base path:
- `/api/admin/governance`

### List rules

```bash
curl -sS "$BASE/api/admin/governance/blocked-rules" \
  -H "Authorization: Bearer $TOKEN"
```

Optional filters:
- `kind=topic|keyword`
- `is_active=true|false`

### Create a rule

```bash
curl -sS -X POST "$BASE/api/admin/governance/blocked-rules" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"kind":"keyword","pattern":"blocked phrase","is_active":true}'
```

- `kind` must be `topic` or `keyword`.
- `pattern` is matched as a case-insensitive substring against the topic + question payload.

### Toggle rule active state

```bash
curl -sS -X PATCH "$BASE/api/admin/governance/blocked-rules/$RULE_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"is_active":false}'
```

### Delete a rule

```bash
curl -sS -X DELETE "$BASE/api/admin/governance/blocked-rules/$RULE_ID" \
  -H "Authorization: Bearer $TOKEN"
```

## 4) Review audits + acceptance metrics

List audits:

```bash
curl -sS "$BASE/api/admin/governance/audits?limit=50" \
  -H "Authorization: Bearer $TOKEN"
```

Optional filters:
- `action=persist|serve`
- `room=classic|challenge|custom|pvp`
- `approved=true|false`

The response includes `persist_acceptance.rate`, computed over all-time `action=persist` audit rows.

## 5) Enforcement points (what is gated)

With the feature flag enabled, governance is applied:

- **Pre-persist**: generated questions are evaluated before inserting into `question_bank`.
- **Pre-serve**: `question_bank` rows are evaluated just before returning them to clients.
  - If rejected at serve-time, the row is marked `gov_approved=false` and `gov_safe=false` so it is less likely to be selected again.

## 6) Troubleshooting

- Too many rejections:
  - Check `/api/admin/governance/audits?approved=false&action=persist` for dominant `reasons`.
  - Review active block rules for overly-broad `pattern` values.

- "Nothing to serve" / frequent fallbacks:
  - Confirm `ENABLE_TRUSTWORTHY_GENERATION=true` is set only where intended.
  - Confirm the migration is applied (new `gov_*` columns exist on `question_bank`).
