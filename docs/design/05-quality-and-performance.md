# AdaptIQ — Quality & Performance Engineering

> Part 5 of the design dossier. How AdaptIQ stays fast and trustworthy under real
> use, the caching design, and the measured impact of the improvement work.
> Companion runtime docs: `docs/reports/QUALITY_PERF_ROADMAP_2026-07-04.md`,
> `docs/reports/PROJECT_STATE_AUDIT_2026-07-04.md`.

## 1. The governing constraint

> The "next question" request must feel instant, but generating a correctly-
> difficult, fact-checked question takes **seconds** (LLM + multi-source fetch +
> validation).

The resolution is a hard rule: **the request path never waits on the LLM**. Slow
work happens **ahead of time**; the request path only does cheap work.

## 2. Pre-generation: moving slow work off the request path

A background **worker** (`services/question_generation_worker.py`, started in
`main.py`'s lifespan) consumes refill requests from Redis and keeps **ready
queues** warm — Redis lists of pre-generated question ids, keyed by
topic / level / difficulty bucket (`services/question_queue.py`).

- Rooms enqueue a refill when a bucket runs low (watermarks like
  `CLASSIC_PREGEN_TOPUP_THRESHOLD`, `CHALLENGE_PREGEN_*` in `config.py`).
- The worker generates only the shortfall, spaced out, each item in its own DB
  session, then persists it (with dedup, governance, concept inference).
- Provider `429`s set a Redis **back-off** flag so the system stops hammering a
  rate-limited API.

**Ideal hot path:** pop an id from the ready queue → load the row → shuffle
options → return. **No LLM.** The LLM only runs inline if the queue is cold — and
even that is removable (see §4, item 4).

## 3. Caching layers

| Layer | Purpose | Key / TTL |
|-------|---------|-----------|
| **Session state** | Server-side answer verification, θ snapshot, seen ids | `session:*`, `current_q:*` (1 h) |
| **Ready queues** | Pre-generated question ids per bucket | `*:ready:*` (6 h) |
| **Seen-set** (opt-in) | Skip the 3-join "already seen" union per selection | `seen:{user}:{topic}` (1 h) |
| **Quotas / locks / back-off** | Coordination & rate control | various |

Rule: **never** cache authenticated API responses at a shared proxy/CDN; caching
is per-user and server-side. Static frontend assets *can* be CDN-hosted after
build.

## 4. The improvement programme (8 items, flag-gated)

A review of the engine found real quality and performance gaps. Each fix ships
behind a **default-off flag** (or is additive), so production behaviour is
unchanged until a change is measured and deliberately enabled. Full detail in
`docs/reports/QUALITY_PERF_ROADMAP_2026-07-04.md`.

| # | Item | Flag | Type |
|---|------|------|------|
| 1 | IRT θ/β scale unification (correct ZPD targeting) | `ENABLE_IRT_LOGIT_SCALE` | quality |
| 2 | Offline item-difficulty recalibration → shadow column | *offline job* | quality |
| 3 | Per-user seen-set Redis cache | `ENABLE_SEEN_SET_CACHE` | perf |
| 4 | No inline LLM on queue miss (serve DB + refill) | `ENABLE_NO_INLINE_LLM` | perf (tail) |
| 5 | Candidate-pool sampling vs `ORDER BY random()` | `ENABLE_CANDIDATE_POOL_SAMPLING` | perf |
| 6 | Cross-process Redis answer lock | `ENABLE_REDIS_SESSION_LOCK` | scaling |
| 7 | `users.elo_global` synced on match finalize | *additive* | consistency |
| 8 | Unified per-concept θ math across rooms | `ENABLE_UNIFIED_CONCEPT_THETA` | consistency |

Also landed (no flag, no behaviour change): parallelised the external-source
fetches in the blender (`asyncio.gather`), removed an N+1 in session concept
selection, and added a hot-path index `ix_user_responses_user_created`.

**Why flags?** Two reasons. (1) *Safety* — a graduation-grade system should not
change scoring/selection behaviour silently; defaults stay put. (2) *Evidence* —
each flag can be A/B-measured against the metric it targets before promotion.

## 5. Measurement

`backend/scripts/measure_quality_perf.py` provides:

- `--quality` — reads `user_responses` and reports the observed correct-rate
  overall and per difficulty bucket, plus the share of answers inside the 60–75%
  **ZPD band** (the success-at-target metric for item 1).
- `--latency` — signs up a throwaway user and times N classic-question requests,
  reporting **p50 / p95 / p99**.

### Measured result — Wave A (items 5 + 3), 40 requests

| metric | baseline (flags off) | Wave A (warm cache) |
|--------|----------------------|---------------------|
| p50 | 8.6 ms | 9.8 ms |
| p95 | 32.3 ms | **11.4 ms** |
| p99 | 4239 ms | **12.9 ms** |
| mean | 119 ms | **9.7 ms** |

The tail collapses: the 4.2 s p99 spike (a cold inline-LLM generation) disappears
once selection avoids the expensive `random()` sort and the seen-set union, and
the full test suite still passes with the flags enabled. The baseline quality
probe showed **0%** of answers in the ZPD band with difficulty skewed to the
extremes — the concrete signal that item 1 (`ENABLE_IRT_LOGIT_SCALE`) exists to
move.

## 6. Recommended enablement order
1. **Wave A** — `ENABLE_CANDIDATE_POOL_SAMPLING` + `ENABLE_SEEN_SET_CACHE` (pure
   latency, no behaviour change). *Enabled and measured.*
2. **Wave B** — `ENABLE_IRT_LOGIT_SCALE` (quality; watch ZPD-band %).
3. **Wave C** — `ENABLE_NO_INLINE_LLM` after confirming pre-generation keeps
   queues warm enough that misses are rare.
4. **Wave D** — `ENABLE_REDIS_SESSION_LOCK` + `ENABLE_UNIFIED_CONCEPT_THETA` when
   running multi-process.

Each promotion is a config change with a measurable target and an instant
rollback (flip the flag off).

---

Back to the [design index](./README.md).
