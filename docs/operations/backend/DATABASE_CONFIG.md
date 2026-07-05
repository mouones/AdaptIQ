# Backend Database And Redis Configuration

This runbook reflects the current `backend/config.py`, `backend/.env.example`, and
`backend/docker-compose.yml` setup. Treat older reference credentials and SQLite
notes as fallback/testing context, not the normal runtime path.

## Active Local Stack

The local backend is designed to run with Docker Compose from `backend/`:

```powershell
Set-Location backend
Copy-Item .env.example .env
# Edit backend/.env and replace every change_this_* value.
docker compose up -d
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe main.py
```

Compose services:

| Service | Container | Local URL/Port | Notes |
|---|---|---|---|
| PostgreSQL | `adaptiq_postgres` | `127.0.0.1:5433` | Uses `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`. |
| Redis | `adaptiq_redis` | `127.0.0.1:${REDIS_HOST_PORT:-6379}` | Requires `REDIS_PASSWORD`. |
| pgAdmin | `adaptiq_pgadmin` | `http://localhost:5050` | Requires `PGADMIN_PASSWORD`. |
| Redis Commander | `adaptiq_redis_commander` | `http://localhost:${REDIS_COMMANDER_HOST_PORT:-8081}` | Uses the Redis password. |

The current sample Postgres URL is:

```env
DATABASE_URL=postgresql+asyncpg://adaptiq:change_this_postgres_password@localhost:5433/adaptiq_db
```

The current sample Redis URL is:

```env
REDIS_PASSWORD=change_this_redis_password
REDIS_URL=redis://:change_this_redis_password@localhost:6379/0
```

## Environment Variables

| Variable | Code default or sample | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://adaptiq:adaptiq@localhost:5433/adaptiq_db` | SQLAlchemy async database URL. `.env.example` overrides the password placeholder. |
| `POSTGRES_USER` | `adaptiq` | Compose Postgres user. |
| `POSTGRES_PASSWORD` | required by Compose | Compose Postgres password. |
| `POSTGRES_DB` | `adaptiq_db` | Compose database name. |
| `REDIS_PASSWORD` | empty in code, required by Compose | Redis password and default URL builder input. |
| `REDIS_HOST_PORT` | `6379` | Host port used when `REDIS_URL` is not explicitly set. |
| `REDIS_URL` | built from `REDIS_PASSWORD` and `REDIS_HOST_PORT` | Runtime Redis URL. |
| `AUTO_CREATE_TABLES` | code default `true`, `.env.example` `false` | Development fallback only. Prefer Alembic for the active stack. |
| `ENVIRONMENT` | `development` | Controls docs, detailed errors, logging, and production safety checks. |
| `ENABLE_PUBLIC_DOCS` | enabled outside production by default | Controls `/docs`, `/redoc`, and `/openapi.json`. |

## Alembic-First Schema Management

Use Alembic for the active PostgreSQL schema:

```powershell
Set-Location backend
.venv\Scripts\python.exe -m alembic heads
.venv\Scripts\python.exe -m alembic current
.venv\Scripts\python.exe -m alembic upgrade head
```

`AUTO_CREATE_TABLES=true` can create tables during startup, but production rejects
that setting and local development should still prefer migrations so the schema
chain remains visible and reproducible.

## Redis Port Conflicts

If local Memurai or another service owns `6379`, keep Docker Redis internal and
publish an alternate host port:

```powershell
Set-Location backend
$env:REDIS_HOST_PORT = "6380"
$env:REDIS_COMMANDER_HOST_PORT = "8082"
docker compose up -d --force-recreate redis redis-commander
```

Then keep `backend/.env` aligned:

```env
REDIS_HOST_PORT=6380
REDIS_URL=redis://:change_this_redis_password@localhost:6380/0
```

## SQLite Fallback

`backend/.env.test` still documents an in-memory SQLite option for offline
validation scripts such as `scripts/live_validation/alt_db.py`. Do not treat that
as the normal app runtime, full test baseline, or production-like setup.

## Validation

With Compose and `main.py` running:

```powershell
Set-Location backend
.venv\Scripts\python.exe -m pytest -q tests
.venv\Scripts\python.exe scripts\repair_data_integrity.py --dry-run
```

From the project root:

```powershell
.\backend\.venv\Scripts\python.exe scripts\scan_secrets.py
```

The full backend suite includes live HTTP coverage. A stopped backend or stopped
Docker stack makes those live checks fail even if unit-only code is healthy.

## Production Notes

- Set strong, unique `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `JWT_SECRET_KEY`, and
  admin UI passwords.
- Set `ENVIRONMENT=production`.
- Set `AUTO_CREATE_TABLES=false` and run Alembic migrations explicitly.
- Use an authenticated Redis URL.
- Disable public docs unless intentionally exposed with `ENABLE_PUBLIC_DOCS=true`.
- Keep real `.env` values out of Git, docs, logs, screenshots, and reports.
