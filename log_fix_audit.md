# Log-driven fix audit

Source logs reviewed:
- `Pasted text.txt`
- `Pasted text (2).txt`
- `Pasted text (3).txt`

## Fixed issues

### 1. Challenge duplicate question persistence
The logs showed duplicate `question_bank_pkey` errors when the same generated challenge question was persisted more than once, then the old background retry reused the request SQLAlchemy session and failed with `A transaction is already begun on this Session`.

Changes:
- `database/crud.py::store_question` is now idempotent.
- It returns the existing `QuestionBank` row if the UUID already exists.
- It catches concurrent `IntegrityError`, rolls back, re-reads the row, and returns it.
- `routers/challenge.py` no longer schedules unsafe background persistence using the request session.
- Challenge now either persists before returning the question or returns a clean 500 retry message.

### 2. Visual room invalid session UUID crash
The logs showed `/api/visual/next` crashing with `ValueError: badly formed hexadecimal UUID string`.

Changes:
- `services/visual_room_service.py::get_visual_session` now validates UUID safely.
- Invalid session IDs return `None`, allowing the router to return a clean 404 instead of an unhandled exception.

### 3. Local DB missing user columns
Older local PostgreSQL databases can miss `users.ban_until`, `users.ban_reason`, or `users.profile_picture` because `create_all()` does not alter existing tables.

Changes:
- `main.py` startup schema guards now add those missing columns with `IF NOT EXISTS` when `AUTO_CREATE_TABLES` is enabled.
- Existing Alembic migrations remain present.

## Validation run

```text
python -m py_compile database/crud.py routers/challenge.py services/visual_room_service.py main.py
# passed

python -m pytest tests/unit/test_challenge_option_counts.py tests/unit/test_visual_room_logic.py tests/unit/test_auth_stats_helpers.py -q
# 15 passed
```

## Packaging

Clean source zip rebuilt without:
- `.venv`
- `node_modules`
- `dist`
- `.git`
- caches
- runtime logs
- compiled files
