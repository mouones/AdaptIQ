# Challenge Room Configuration Notes

This file describes the current challenge-room tuning path. Challenge scoring and rank behavior are implemented in `backend/services/challenge_service.py`, with typed defaults read from `backend/config.py`.

## Current Runtime Behavior

- Challenge API prefix: `/api/challenge`.
- Challenge scoring table is built from `CHALLENGE_POINTS_LEVEL_1` through `CHALLENGE_POINTS_LEVEL_5`.
- Streak movement uses `CHALLENGE_STREAK_UP_THRESHOLD` and `CHALLENGE_STREAK_DOWN_THRESHOLD`.
- Rank thresholds use `CHALLENGE_RANK_D_MIN`, `CHALLENGE_RANK_C_MIN`, `CHALLENGE_RANK_B_MIN`, and `CHALLENGE_RANK_A_MIN`; rank E starts at zero.
- Temporary generated-question state uses `CHALLENGE_SESSION_QUESTION_TTL_SECONDS`.
- Challenge question/session behavior is implemented in `backend/routers/challenge.py` and `backend/services/challenge_service.py`.
- Redis stores temporary challenge/session question state where available.
- Duplicate challenge submits are guarded by a database uniqueness constraint and transactional submit logic.

## Environment Values That Still Matter

These are general runtime settings used by challenge flows indirectly:

```powershell
DATABASE_URL=postgresql+asyncpg://...
REDIS_PASSWORD=...
REDIS_HOST_PORT=6379
REDIS_URL=redis://:password@localhost:6379/0
GROQ_API_KEY=...
JWT_SECRET_KEY=...
ENVIRONMENT=development
ENABLE_PUBLIC_DOCS=true
CHALLENGE_POINTS_LEVEL_1=3:-1
CHALLENGE_POINTS_LEVEL_2=5:-2
CHALLENGE_POINTS_LEVEL_3=7:-4
CHALLENGE_POINTS_LEVEL_4=9:-6
CHALLENGE_POINTS_LEVEL_5=11:-9
CHALLENGE_STREAK_UP_THRESHOLD=4
CHALLENGE_STREAK_DOWN_THRESHOLD=2
CHALLENGE_SESSION_QUESTION_TTL_SECONDS=21600
CHALLENGE_RANK_D_MIN=1000
CHALLENGE_RANK_C_MIN=3000
CHALLENGE_RANK_B_MIN=7000
CHALLENGE_RANK_A_MIN=15000
```

Do not commit real values. Keep local values in `backend/.env`.

Browser CSRF for challenge requests is handled by the `adaptiq_csrf` cookie and
`X-CSRF-Token` header created by `backend/routers/auth.py`; there is no active
challenge-specific CSRF secret to configure.

## Changing Challenge Rules

To change points, streak behavior, question TTLs, or rank thresholds:

1. Update `backend/.env` or deployment environment values.
2. Add or update focused tests under `backend/tests/unit/` and `backend/tests/integration/`.
3. Run the challenge/security regression tests.
4. Update this runbook if new knobs or score rules are added.

## Validation

```powershell
Set-Location backend
.venv\Scripts\python.exe -m pytest -q tests/unit/test_challenge_streaks.py tests/integration/test_challenge_idempotency.py
.venv\Scripts\python.exe -m pytest -q tests/unit/test_security_regressions.py
```

Live challenge probes require a running backend and seeded users:

```powershell
Set-Location backend
.venv\Scripts\python.exe scripts\live_validation\e2e_full.py
.venv\Scripts\python.exe scripts\live_validation\challenge_deep.py
```
