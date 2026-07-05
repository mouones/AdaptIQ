# AdaptIQ — Architecture & Technology Choices

> Part 3 of the design dossier. Explains the system shape and the reasoning
> behind each major technology decision. For runtime setup see the root
> [`README.md`](../../README.md) and [`CLAUDE.md`](../../CLAUDE.md).

## 1. High-level architecture

```
┌────────────────────────┐        HTTPS (cookie + CSRF)        ┌──────────────────────────────┐
│  React + TypeScript     │  ───────────────────────────────▶ │  FastAPI (async)              │
│  (Vite, Tailwind, RR)   │                                    │  routers → services → models  │
│  pages / services /     │  ◀─────────────────────────────── │                               │
│  context                │        JSON                        │  ┌──────────┐  ┌───────────┐ │
└────────────────────────┘                                     │  │ LLM      │  │ RAG       │ │
                                                                │  │ (Groq)   │  │ pipeline  │ │
                                                                │  └────┬─────┘  └─────┬─────┘ │
                                                                │       │ (off request path)   │
                                                 ┌──────────────┴───────┴──────────────────────┘
                                                 │
                    ┌────────────────┐   ┌───────▼────────┐   ┌───────────────────────────┐
                    │ PostgreSQL     │   │ Redis          │   │ Background worker          │
                    │ durable state  │   │ sessions,      │   │ pre-generates questions    │
                    │ (users, Qbank, │   │ ready queues,  │   │ into ready queues          │
                    │  concepts, …)  │   │ quotas, locks  │   │                            │
                    └────────────────┘   └────────────────┘   └───────────────────────────┘
```

**One idea dominates the shape:** slow work (LLM/RAG/multi-source fetch) is moved
**off the request path** into a background worker that keeps Redis "ready queues"
warm. The user request then does cheap work only: pop a question id, load the row,
shuffle, return.

## 2. Layered backend

`routers/` (thin HTTP) → `services/` (business logic) → `database/` (SQLAlchemy
models) + `schemas/` (Pydantic contracts). Routers stay thin so logic is testable
without HTTP and reusable across rooms. Cross-cutting concerns (auth, rate limits,
logging, monitoring) live in middleware/dependencies.

## 3. Technology decisions & rationale

### 3.1 Backend framework — **FastAPI (async)**
- **Why:** the workload is I/O-bound (DB, Redis, HTTP to LLM/sources). An async
  framework lets a single process handle many concurrent waits efficiently.
- **Also:** first-class Pydantic validation (typed request/response contracts),
  automatic OpenAPI docs, and dependency injection for auth/DB/session wiring.
- **Alternative considered:** Flask/Django — synchronous by default; would need
  more work to overlap the many external awaits.

### 3.2 Datastore — **PostgreSQL**
- **Why:** the data is relational (users → responses → questions → concepts, with
  foreign keys and integrity constraints). Postgres gives transactions, rich
  indexing, `JSON` columns where useful, and window/aggregate queries for the
  analytics dashboard.
- **Schema evolution:** **Alembic** owns migrations (single-head chain); auto
  table creation is allowed in dev but **forbidden in production**.

### 3.3 Runtime state & cache — **Redis**
- **Why:** three needs map naturally onto Redis:
  1. **Session/answer state** — short-lived per-session JSON (server-side answer
     verification, θ snapshots, seen ids).
  2. **Ready queues** — lists of pre-generated question ids per
     topic/level/bucket, consumed on the hot path.
  3. **Coordination** — pre-generation request queue, per-key locks, provider
     back-off flags, per-user quotas.
- Redis is optional at boot: the app degrades to an in-memory store for local dev.

### 3.4 LLM — **Groq (`llama-3.1-8b-instant`)**
- **Why:** very low latency and generous throughput make on-demand *and*
  batch generation viable; an 8B instruction model is sufficient for grounded MCQ
  generation when it is given verified facts to work from.
- **Key rule:** the browser never talks to the LLM. All LLM calls are
  **backend-only** so keys are never shipped to clients and every call passes
  through validation/governance.

### 3.5 Retrieval — **Agentic RAG + multi-source blend**
- **Why:** an ungrounded LLM hallucinates "facts". AdaptIQ retrieves verified
  context first (Wikipedia narrative, Wikidata/DBpedia structured facts, open
  data), blends it (a 40/40/20 fact/narrative/pattern mix), and only then asks the
  LLM to write a question it can defend — which a validator then checks.

### 3.6 Frontend — **React + TypeScript + Vite + Tailwind**
- **Why:** a component model fits the room/dashboard UI; TypeScript catches
  contract drift against the backend; Vite gives fast dev/build; Tailwind keeps
  styling co-located and consistent. Routing via React Router; auth state lives in
  memory and is refreshed from `/api/auth/me` (never persisted to `localStorage`).

### 3.7 Infrastructure — **Docker Compose**
- PostgreSQL, Redis, pgAdmin, Redis Commander (and optionally the backend) run
  locally via Compose; host bindings are restricted to localhost.

## 4. Security architecture (summary)

- **Auth:** HttpOnly `adaptiq_access` cookie + double-submit `adaptiq_csrf` token;
  bearer tokens remain temporarily accepted for scripts/tests.
- **Authorization:** admin routes gated; DB-inspector redacts sensitive columns.
- **Startup guardrails:** `validate_security_config()` fails fast on a weak JWT
  secret, auto-create-tables in prod, unauthenticated Redis in prod, or
  `DEV_BYPASS_AUTH` in prod.
- **Rate limiting:** per-route limits on auth, LLM-heavy, and admin-sensitive
  paths; Redis-backed quotas for per-user/model budgets.
- **Untrusted input:** retrieved context and user chat are treated as data, never
  as instructions to the model.

## 5. Data model (essentials)

- `users`, `user_responses` (one row per answer — the raw signal for adaptation).
- `question_bank` (+ IRT difficulty, provenance `source`, governance columns).
- `concepts`, `question_concepts`, `user_concept_theta`,
  `user_concept_repeat_queue` (the per-concept adaptation + spaced repetition).
- Room state: `classic_sessions`, `challenge_*`, `custom_*`, `pvp_*`, `visual_*`.

## 6. Configurability

Room sizes, scoring, rank thresholds, TTLs, pre-generation watermarks, and the
quality/performance feature flags are all in `backend/config.py`, so behaviour
can change via environment without touching routers. See
[05 — Quality & Performance](./05-quality-and-performance.md).

---

Next: [04 — Learning Engine](./04-learning-engine.md).
