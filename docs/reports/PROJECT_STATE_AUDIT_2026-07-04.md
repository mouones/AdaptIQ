# AdaptIQ — Project State Audit (2026-07-04)

Supersedes `PROJECT_STATE_AUDIT_2026-06-04.md`. Scope: current live app
(`backend/`, `frontend/`, `docs/`, `scripts/`). This audit records the state
after the 2026-07-04 dashboard-robustness, documentation, and low-risk
performance pass.

## Snapshot

- **Backend**: FastAPI, routers under `/api` (auth, classic, challenge, custom,
  onboarding, admin, governance, pvp, visual, chat). Business logic in
  `backend/services/`; ORM + Alembic in `backend/database/` + `backend/alembic/`.
- **Frontend**: React + TypeScript + Vite. Cookie auth (`adaptiq_access`
  HttpOnly + `adaptiq_csrf` double-submit); no tokens in `localStorage`.
- **Alembic head**: `20260704_02_add_question_calibration_shadow`
  (single head), following `20260704_01_add_user_responses_user_created_index` →
  `20260611_02_add_visual_session_streaks` → `20260611_01_add_visual_session_time`
  → `20260604_04_drop_global_concept_name_unique_index`.
- **Storage**: PostgreSQL (users, question bank, concepts, room sessions, custom
  topics/facts, governance, analytics), Redis (session/state, ready queues,
  prewarm requests, matchmaking).

## Changes landed 2026-07-04

### Dashboard reliability (the reported failure)
- `routers/auth.py` `GET /api/auth/stats` previously ran the Challenge / PvP /
  Visual time-sum and session-count queries **unguarded**; a single missing or
  partly-migrated column (e.g. `visual_sessions.total_time_ms`) 500-ed the whole
  endpoint, so the user dashboard showed only the amber "temporarily
  unavailable" banner with zeroed stats. These now degrade **per metric** via a
  shared `_safe_stats_scalar` helper (log + 0 + rollback) instead of failing the
  whole response. No change to the numbers when the schema is healthy.
- Admin Overview (`frontend/src/pages/AdminDashboard.tsx`) loaded its three
  widgets with `Promise.all`, so one failing sub-request blanked the entire
  Overview tab silently. Now uses `Promise.allSettled` + an inline error notice;
  the simple list tabs (users/questions/sessions/monitoring/governance) surface
  a `tabError` message instead of swallowing errors in `catch {}`.

### Documentation corrections
- Alembic head references updated in `CLAUDE.md`, `README.md`,
  `docs/walkthrough.md`.
- `SECURITY_AUDIT_2026-06-03.md`: the "JWT/profile/chat in `localStorage`"
  High finding is annotated **RESOLVED (verified 2026-07-04)** — current
  `AuthContext.tsx`/`http.ts` have zero `localStorage`/`adaptiq_token`
  references; auth is cookie-based.
- `POST /api/admin/custom-topics/toggle-active` documented in `CLAUDE.md`,
  `README.md`, `backend/README.md`, `frontend/README.md`.
- Dangling `review_extract/` reference removed from `README.md`; parent-level
  `../other/` `../rapport/` paths clarified.

### Low-risk performance (no logic change)
- `services/source_blender.py` `blend()`: the independent Wikidata / DBpedia /
  Wikipedia-narrative / HF fetches now run concurrently via `asyncio.gather`
  (previously serialized four slow third-party calls). WorldBank stays a
  sequential fallback so the result set is identical. Assembled
  `structured_facts` / `sources` ordering is unchanged.
- `services/classic_service.py` `select_concepts_for_session()`: the per-concept
  `UserConceptTheta` + `UserConceptRepeatQueue` lookups (an N+1 over every topic
  concept per session start) are replaced by two batched `IN (...)` queries.
  Scoring is unchanged.
- New index `ix_user_responses_user_created` on `user_responses (user_id,
  created_at)` (migration `20260704_01` + ORM `__table_args__`) supports the hot
  per-user "seen questions" history scan and the dashboard stats window queries.
- Removed dead `services/question_cache_service.py` (never instantiated; its
  `invalidate_user_all` scan loop was buggy). A correct caching design is in
  `QUALITY_PERF_ROADMAP_2026-07-04.md`.

### Tests
- `tests/unit/test_alembic_graph.py` and `tests/unit/test_repair_schema_preflight.py`
  updated for the new head `20260704_01`. `requests` is already declared in
  `requirements-dev.txt`.

## Roadmap items landed 2026-07-04 (second tranche)

- **IRT θ/β unification (roadmap item 1)** — implemented behind
  `ENABLE_IRT_LOGIT_SCALE` (**default off**): `difficulty_to_beta_continuous`
  feeds a logit β to the per-concept θ update, and the classic ZPD band is
  converted to 1-5 bucket bounds before filtering `difficulty_irt`. Unit tests in
  `tests/unit/test_irt_logit_scale.py`. Live default-off classic flow verified
  unchanged.
- **`User.elo_global` consistency (roadmap item 7)** — `pvp_service` now mirrors
  `PvPRating.elo_rating` onto `User.elo_global` in `end_match`/`forfeit_match`.

## Roadmap items landed 2026-07-04 (third tranche — all flag-gated, default off)

All 8 `QUALITY_PERF_ROADMAP_2026-07-04.md` items are now implemented behind
default-off flags (item 2 is an additive offline job, item 7 is additive):

- Item 2 — offline recalibration job + `difficulty_irt_calibrated` shadow column
  (migration `20260704_02`).
- Item 3 — `ENABLE_SEEN_SET_CACHE`: per-user Redis seen-set (verified populating
  on the hot path with a ~3600s TTL).
- Item 4 — `ENABLE_NO_INLINE_LLM`: classic + challenge skip inline LLM/RAG on a
  queue miss, serve a DB question, enqueue a refill.
- Item 5 — `ENABLE_CANDIDATE_POOL_SAMPLING`: freshness-pool sampling vs `random()`.
- Item 6 — `ENABLE_REDIS_SESSION_LOCK`: cross-process Redis answer lock.
- Item 8 — `ENABLE_UNIFIED_CONCEPT_THETA`: custom room uses shared `ConceptIRT.compute_update`.

Verified: 338 backend tests pass; 12/12 frontend e2e pass; a full flags-on classic
flow (all six flags on) serves + answers questions end-to-end.

### Measurement harness + Wave A result

`scripts/measure_quality_perf.py` provides `--quality` (offline correct-rate /
ZPD-band analysis) and `--latency` (live p50/p95/p99 probe).

**Wave A** (`ENABLE_CANDIDATE_POOL_SAMPLING` + `ENABLE_SEEN_SET_CACHE`) is enabled
in `backend/.env` and measured against the classic questions endpoint (40 reqs):

| metric | baseline (off) | Wave A (warm cache) |
| --- | --- | --- |
| p50 | 8.6 ms | 9.8 ms |
| p95 | 32.3 ms | 11.4 ms |
| p99 | 4239 ms | 12.9 ms |
| mean | 119 ms | 9.7 ms |

Full suite (338 tests) passes with Wave A on. Baseline quality probe showed **0%**
of responses in the 60-75% ZPD band with difficulty skewed to extremes — the target
metric to move when `ENABLE_IRT_LOGIT_SCALE` (Wave B) is enabled.

## Known open items (deferred)

1. Promote the flags to defaults after measuring success-rate-at-target (quality)
   and p50/p95 question latency + DB/LLM call counts (performance) in an A/B window.
2. Extend `ENABLE_NO_INLINE_LLM` prewarm coverage so queue misses are rare before
   the flag becomes the default.

## Pending your confirmation

- **Data-integrity `--apply`**: the dry-run is clean-scoped, but running
  `repair_data_integrity.py --apply` mutates the local DB (topic normalization,
  concept inference for ~454 unlinked questions, ~19 blank explanations filled).
  It was blocked by the safety classifier pending explicit approval.

## Validation

Run the full stack per `CLAUDE.md` (Docker + backend + built frontend), then:
`pytest -q tests`, `npm run lint/build/test:e2e`, `scan_secrets.py`,
`repair_data_integrity.py --dry-run`. Live-integration tests
(`test_challenge_idempotency.py`, `security-smoke.spec.ts`) require the running
stack + seeded DB + `GROQ_API_KEY`.
