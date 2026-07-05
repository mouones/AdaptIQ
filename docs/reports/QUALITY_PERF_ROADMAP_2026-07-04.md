# AdaptIQ — Quality & Performance Roadmap (2026-07-04)

Design document. **Update 2026-07-04: all 8 items are now implemented** — items
1, 3, 4, 5, 6, 8 behind default-off flags; item 2 as an additive offline job; item
7 additive. Each item's status box below records how. The plan improves quiz
**quality** and **performance** (including caching) **without discarding the two
invariants** the product depends on:

1. **Personalization** — each user is served questions targeted to their current
   ability (their Zone of Proximal Development), not a global static difficulty.
2. **Per-concept mastery tracking** — each user has a per-concept ability
   estimate (θ) that is updated on every answer and drives selection + spaced
   repetition.

Every item below lists the **change**, the **expected gain**, the **blast
radius**, and a **rollback/flag** strategy, ordered by value-to-risk.

---

## 0. Purpose of the engine (so the roadmap preserves it)

- **Quiz loop**: pick a concept for the user → pick/generate a question at the
  right difficulty → grade → update the user's θ for that concept → occasionally
  enqueue the item for spaced repetition.
- **Leveling** (`config.compute_level`): global points ladder Novice → Apprentice
  → Scholar → Expert → Master. Points come from correctness, speed bonus, and
  hint/wrong penalties. This is a **progression/motivation** signal, independent
  of θ.
- **Challenge ranks** E → D → C → B → A: cumulative `rank_points`; the rank gates
  which challenge levels (1-5) are playable. In-session level moves on streaks.
- **ELO** (`PvPRating.elo_rating`, standard Elo K=32/16): PvP skill rating used
  for matchmaking (±300 window) and leaderboards.
- **θ / IRT** is the *personalization* core; leveling/ranks/ELO are *progression*
  layers on top. Improvements must keep θ authoritative for difficulty targeting.

---

## 1. IRT θ/β scale unification  — highest quality impact  — ✅ IMPLEMENTED (flag default off)

> **Status: implemented 2026-07-04 behind `ENABLE_IRT_LOGIT_SCALE` (default off).**
> `database/irt.difficulty_to_beta_continuous` converts the stored 1-5
> `difficulty_irt` to a logit β for the per-concept θ update, and
> `classic_service.select_next_question` converts the logit ZPD band to 1-5
> bucket bounds before filtering the column. Verified: θ=0 → buckets [2,3],
> θ=1.5 → [3,4] (previously matched no rows). Unit tests in
> `tests/unit/test_irt_logit_scale.py`. Flip `ENABLE_IRT_LOGIT_SCALE=true` to
> enable; measure success-rate-at-target before making it the default.


**Problem.** The live per-concept update and ZPD selection feed a 1–5
`difficulty_irt` where a logit β is expected. In `concept_irt.update_concept_theta`
the term `irt_probability(θ≈0, β≈2.5)` gives P≈0.08, so nearly every answer looks
"surprising" and biases θ. In `classic_service.select_next_question` the ZPD band
`target_beta_range(θ)` is in logits (≈ −1.1…−0.4 for θ≈0) but is compared against
the 1–5 column, so the band matches almost nothing and pushes selection into the
random/LLM fallbacks. A `difficulty_to_beta` converter already exists but is not
used on these paths.

**Change.** Route every difficulty→β through `difficulty_to_beta` (and β→bucket
through `beta_to_difficulty`) consistently in both the θ update and the selection
band. Interpret/backfill `QuestionBank.difficulty_irt` as a logit or keep it 1–5
and convert at read time — pick one representation and document it.

**Gain.** Real ZPD targeting (questions land at ~60–75% success), unbiased θ,
fewer cold LLM fallbacks (also a latency win).

**Blast radius.** Changes difficulty served and θ trajectories for every user.

**Rollback/flag.** Gate behind `ENABLE_IRT_LOGIT_SCALE` (default off). Log both
old and new selected buckets for an A/B window; compare success-rate-at-target
before flipping the default. No schema change required if converting at read time.

---

## 2. Question difficulty recalibration (offline)  — ✅ IMPLEMENTED

> **Status: implemented 2026-07-04.** `scripts/recalibrate_question_difficulty.py`
> (`--dry-run`/`--apply --min-sample N`) aggregates `user_responses` outcomes per
> question and writes a learned difficulty to the **shadow** column
> `difficulty_irt_calibrated` (migration `20260704_02`), never touching the served
> `difficulty_irt`. Off the request path, reversible until reviewed/promoted. Math
> tested in `tests/unit/test_recalibrate_difficulty.py`.


**Problem.** `crud.recalibrate_question_irt` and the `UserResponse` CRUD path are
dead, so `QuestionBank.difficulty_irt` is frozen at its seed/generation value and
never learns from real answer data.

**Change.** A periodic **offline** job (cron/management command, never on the
request path) that recomputes item β from aggregated `UserResponse` outcomes
(1PL update) for items above a minimum exposure count.

**Gain.** Item difficulty tracks reality; better ZPD matches over time.

**Blast radius.** Moves item β; bounded by min-exposure gate and clamping.

**Rollback/flag.** Job is opt-in and idempotent; write to a shadow column first
(`difficulty_irt_calibrated`) and swap only after review.

---

## 3. Real per-user question cache (replaces the removed dead one)  — ✅ IMPLEMENTED (flag default off)

> **Status: implemented 2026-07-04 behind `ENABLE_SEEN_SET_CACHE` (default off).**
> `ClassicService.get_user_seen_question_ids` takes an optional `redis_client`;
> when enabled it reads/populates a per-user/topic Redis set (`seen:{user}:{topic}`,
> TTL `SEEN_SET_TTL_SECONDS`) with populate-on-miss, skipping the 3-join union.
> `mark_seen_in_cache` appends on answer with an EXISTS guard (never creates a
> partial set). Wired through `select_next_question` (via `SessionService.redis`)
> and the router fallbacks. Verified live: the set populates on the hot path with a
> ~3600s TTL. Session-local asked ids are always merged fresh.


**Problem.** `get_user_seen_question_ids` unions 3 joins over up to 5000 rows on
**every** classic selection (and again in router fallbacks). The old
`QuestionCacheService` that was supposed to help was never wired and has been
removed.

**Change.** A correct Redis design:
- **Per-user seen-set**: `seen:{user_id}:{topic}` as a Redis Set (or bitmap of
  question-id hashes) with TTL, updated on each answer. Selection reads the set
  instead of re-running the 3-join union; the DB union becomes a periodic
  reconciliation, not a per-question call.
- **Per-(topic,bucket) warm pool** already exists (`question_queue.py`); extend it
  so selection pops a candidate and checks membership against the seen-set in
  O(1).
- Fix the `while cursor != 0` SCAN bug (string vs int cursor) in any reused scan
  helper.

**Gain.** Removes the biggest per-question DB fan-out; keeps personalization
(cache is per-user) and mastery tracking (θ path untouched).

**Blast radius.** Selection reads a cache; must stay correct on cache miss
(fall back to the DB union). Redis memory grows with active users × history.

**Rollback/flag.** `ENABLE_SEEN_SET_CACHE` (default off); on miss or when disabled,
use the existing DB union unchanged.

---

## 4. Take LLM/RAG off the request path  — ✅ IMPLEMENTED (flag default off)

> **Status: implemented 2026-07-04 behind `ENABLE_NO_INLINE_LLM` (default off).**
> On a ready-queue miss, classic (`classic_room.py`) and challenge (`challenge.py`)
> no longer run inline LLM/RAG: they enqueue a background pregen refill
> (`_request_classic_pregen` / `_request_challenge_pregen`, `force=True`) and fall
> through to serve a real DB question, so the request never blocks on the model.
> Default off preserves the current inline-fallback behavior.


**Problem.** On a ready-queue miss, classic (`classic_room.py`) and challenge
(`challenge.py`) run multi-second LLM + RAG + a second confidence-scoring LLM
call **inside the user request** — the dominant tail latency.

**Change.** Proactive prewarming per (topic, bucket) and per (level) so the queue
rarely misses; on miss, serve a slightly-off-band **cached** item and enqueue a
background refill rather than generating inline. Never block the user on the LLM.

**Gain.** p95/p99 question latency drops from seconds to a DB read + shuffle.

**Blast radius.** Occasionally serves a question one bucket off the ideal band.

**Rollback/flag.** `ALLOW_INLINE_LLM_FALLBACK` (default on today → off after
prewarm coverage is verified).

---

## 5. Replace `ORDER BY random()` on the hot selection path  — ✅ IMPLEMENTED (flag default off)

> **Status: implemented 2026-07-04 behind `ENABLE_CANDIDATE_POOL_SAMPLING` (default
> off).** `ClassicService._fetch_candidates` pulls a bounded freshness pool
> (`CANDIDATE_POOL_SIZE`, least-/never-served first) and shuffles it in Python,
> avoiding a `random()` sort over the whole filtered set. Applied to all three
> classic selection query paths. Default off keeps `ORDER BY random()`.


**Problem.** `select_next_question` uses `ORDER BY random()` over `QuestionBank`
up to 3 times per selection — a near full-scan.

**Change.** Precomputed per-(topic,bucket) candidate id pools (refreshed by the
worker) or keyset/`TABLESAMPLE SYSTEM`. Selection samples from the pool minus the
seen-set (item 3).

**Gain.** Removes repeated random full-scans on the hot path.

**Blast radius.** Sampling distribution changes slightly (still uniform-ish).

**Rollback/flag.** Feature flag; fall back to `ORDER BY random()` on empty pool.

---

## 6. Cross-process session lock  — ✅ IMPLEMENTED (flag default off)

> **Status: implemented 2026-07-04 behind `ENABLE_REDIS_SESSION_LOCK` (default
> off).** `SessionService.session_lock` uses a Redis lock (`SET NX PX` with a unique
> token + atomic Lua compare-and-delete release) when enabled and Redis is
> available; otherwise the in-process `asyncio.Lock` (unchanged default).


**Problem.** `services/session.py` guards answer races with an in-process
`asyncio.Lock`, which does not hold across multiple worker processes/replicas.

**Change.** Redis lock (`SET key val NX PX ttl`) keyed by session id, with the
existing lock TTL config.

**Gain.** Correct idempotency/double-submit protection under horizontal scaling.

**Blast radius.** Adds a Redis round-trip per answer; needs safe lock release.

**Rollback/flag.** Keep the `asyncio.Lock` path when Redis is unavailable.

---

## 7. `User.elo_global` consistency  — ✅ IMPLEMENTED

> **Status: implemented 2026-07-04.** `pvp_service._sync_user_elo_global` mirrors
> the authoritative `PvPRating.elo_rating` onto `User.elo_global` inside
> `end_match` (and therefore `forfeit_match`, which delegates to it). Additive —
> does not touch the Elo math or `PvPRating`.


**Problem.** `User.elo_global` is written only by a setup script and read by a
schema; **no match code updates it**. The real rating is `PvPRating.elo_rating`.
Any UI reading `User.elo_global` is stale.

**Change.** Either drop the dormant column (and repoint readers to
`PvPRating.elo_rating`) or sync it inside `end_match`/`forfeit_match`.

**Gain.** One source of truth for ELO; no stale leaderboard values.

**Blast radius.** Small; touches PvP finalize + any `elo_global` reader.

**Rollback/flag.** Prefer "sync in finalize" (additive) over a destructive drop.

---

## 8. Unify the two θ update paths  — ✅ IMPLEMENTED (flag default off)

> **Status: implemented 2026-07-04 behind `ENABLE_UNIFIED_CONCEPT_THETA` (default
> off).** The per-concept update math is now a single source of truth,
> `ConceptIRT.compute_update` (theta step + variance decay + mastery mapping);
> `update_concept_theta` was refactored to use it (no behavior change for classic).
> When enabled, the custom room (`custom.py`) uses `compute_update` too, so both
> rooms apply the same variance decay and mastery mapping.


**Problem.** Classic uses `ConceptIRT.update_concept_theta`; custom does its own
inline `update_theta(...)` (`custom.py`) with different semantics — mastery means
slightly different things per room.

**Change.** Converge both rooms on a single mastery-update function (the
`ConceptIRT` path, after item 1), so θ and `mastery_level` are consistent.

**Gain.** Consistent mastery across rooms; one place to reason about correctness.

**Blast radius.** Changes custom-room θ updates.

**Rollback/flag.** Land after item 1; gate with the same IRT flag.

---

## Suggested sequencing

1. **Item 1 (IRT scale)** behind a flag — unlocks correct targeting and reduces
   LLM fallbacks (compounds with items 3–5).
2. **Items 3 + 5** (seen-set cache + candidate pools) — the biggest DB latency
   wins, independent of behavior.
3. **Item 4** (LLM off request path) — the biggest tail-latency win.
4. **Item 2** (offline recalibration), then **6, 7, 8** as consistency/scaling
   hardening.

Each item ships behind its own flag with before/after metrics (success-rate-at-
target for quality; p50/p95 question latency and DB/LLM call counts for
performance) so changes are measurable and reversible.
