# Backend Auth Status

This is the current auth status note for the live app.

## Current Model

- Browser auth uses the backend-set `adaptiq_access` HttpOnly cookie.
- CSRF uses the readable `adaptiq_csrf` cookie plus `X-CSRF-Token` on unsafe requests.
- Bearer tokens remain temporarily supported for scripts, Newman/Postman, and integration tests.
- The frontend must not persist `adaptiq_token`, `adaptiq_user`, or chat history in `localStorage`.
- Logout clears auth and CSRF cookies.

## Local Validation

```powershell
Set-Location backend
.venv\Scripts\python.exe -m pytest -q tests/unit/test_auth_helpers.py tests/unit/test_auth_signup_contract.py tests/unit/test_auth_password_reset_contract.py
```

Live/browser validation:

```powershell
Set-Location frontend
npm run test:e2e
```

After live validation, clean generated users:

```powershell
Set-Location backend
.venv\Scripts\python.exe scripts\cleanup_test_users.py --dry-run
.venv\Scripts\python.exe scripts\cleanup_test_users.py --apply --yes
```
