# Test Users

This guide covers deterministic seeded users and generated live/e2e users.

## Seeded Development Accounts

Create or refresh deterministic local accounts:

```powershell
Set-Location backend
.venv\Scripts\python.exe scripts\setup_test_users.py
```

This seeds users, challenge rankings, concept mastery, onboarding flags, and custom mastery rows. Generated credential exports are written under `backend/generated/`, which is ignored.

## Seeded Accounts

| Email | Password | Purpose |
|---|---|---|
| `admin.master@example.com` | `AdminPass123!` | Admin dashboard and full local validation |
| `challenge.e@example.com` | `TestPass123!` | Challenge rank E |
| `challenge.d@example.com` | `TestPass123!` | Challenge rank D |
| `challenge.c@example.com` | `TestPass123!` | Challenge rank C |
| `challenge.b@example.com` | `TestPass123!` | Challenge rank B |
| `classic.novice@example.com` | `TestPass123!` | Classic cold-start profile |
| `classic.expert@example.com` | `TestPass123!` | Classic expert profile |
| `custom.fresh@example.com` | `TestPass123!` | Custom room fresh progress |
| `custom.complete@example.com` | `TestPass123!` | Custom room advanced progress |
| `pvp.grinder@example.com` | `TestPass123!` | PvP rating and matchmaking test |

## Local Links

- Frontend: `http://localhost:5173`
- Backend: `http://localhost:8000`
- Admin dashboard: `http://localhost:5173/admin`
- Local API docs: `http://localhost:8000/docs` when docs are enabled

There is no active standalone admin server on `localhost:9000`.

## Generated Live/E2E Accounts

Live tests may create temporary users with prefixes such as:

- `test`
- `copilot`
- `e2e`
- `flowtest`
- `pw-smoke`
- `geo_scope`
- `sec_cookies`
- `auditpvp`
- `livepvpfix`
- plus-alias generated Postman users

Clean them after live validation:

```powershell
Set-Location backend
.venv\Scripts\python.exe scripts\cleanup_test_users.py --dry-run
.venv\Scripts\python.exe scripts\cleanup_test_users.py --apply --yes
```

Always inspect dry-run counts before apply. The script prints redacted counts and pattern buckets only; it does not expose email addresses or secrets.

## Real Gameplay History

To generate real API-based history without table truncation:

```powershell
Set-Location backend
.venv\Scripts\python.exe scripts\generate_real_test_user_history.py
```

This appends normal room history through live APIs.
