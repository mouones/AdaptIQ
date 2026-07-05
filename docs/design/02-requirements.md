# AdaptIQ — Requirements Analysis

> Part 2 of the design dossier. Translates the objectives from the
> [pre-product study](./01-pre-product-study.md) into concrete functional and
> non-functional requirements, and maps each to where it lives in the code.

## 1. Actors

- **Learner** — signs up, plays rooms, answers questions, earns points/ranks.
- **Administrator** — a learner with `is_admin`; curates content, approves
  topics, reviews governance audits, monitors the system.
- **System (background)** — the pre-generation worker and scheduled maintenance.

## 2. Functional requirements

### FR-A · Accounts & security
- FR-A1 Sign up / log in / log out; forgot- and reset-password by email OTP.
- FR-A2 Browser sessions use an **HttpOnly cookie** (`adaptiq_access`) plus a
  readable **CSRF token** (`adaptiq_csrf`) required on unsafe requests.
- FR-A3 Admin-only endpoints reject non-admins; sensitive DB columns are redacted.
- *Code:* `backend/routers/auth.py`, `frontend/src/context/AuthContext.tsx`,
  `frontend/src/services/http.ts`.

### FR-B · Adaptive learning loop (the core)
- FR-B1 Track each user's ability **per concept** (θ) and update it after every
  answer.
- FR-B2 Select the next question inside the user's **ZPD** for the session's
  concepts; a **cold-start** (few responses) widens the band.
- FR-B3 Never repeat a question the user has already seen (across all rooms),
  except deliberate **spaced-repetition** re-tests.
- FR-B4 Award points; wrong answers and hints apply penalties; update the user's
  global level.
- *Code:* `backend/services/classic_service.py`, `services/concept_irt.py`,
  `database/irt.py`, `database/concept_models.py`.

### FR-C · Game rooms
- FR-C1 **Classic** — the core adaptive room.
- FR-C2 **Challenge** — ranked mode (levels 1–5, E→D→C→B→A ranks, streak-driven
  level moves).
- FR-C3 **Custom** — user/admin-chosen topics; fact-driven generation and
  per-fact mastery.
- FR-C4 **PvP** — real-time 1-v-1 duels with ELO rating and matchmaking.
- FR-C5 **Visual** — map/geography spatial questions.
- *Code:* `backend/routers/{classic_room,challenge,custom,pvp,visual_room}.py`.

### FR-D · Content generation & trust
- FR-D1 Generate MCQs via LLM grounded in retrieved facts (RAG).
- FR-D2 Validate generated questions (word count, structured-fact presence,
  narrative quality) before serving.
- FR-D3 Classify each question's **provenance** (`source`) and keep room-specific
  generated rows out of unrelated pools.
- FR-D4 Governance: block-rules, admin approval, and audit trail.
- *Code:* `services/{question_generator_enhanced,question_validator,confidence_scorer,
  source_blender,governance_service}.py`, `backend/rag/*`.

### FR-E · Administration
- FR-E1 Dashboard: overview, users, questions, sessions, concepts, governance,
  DB inspector, monitoring, custom-topic approval.
- FR-E2 Approve/deactivate community custom-topic candidates.
- FR-E3 Timed bans and user management.
- *Code:* `backend/routers/{admin,governance}.py`,
  `frontend/src/pages/AdminDashboard.tsx`.

### FR-F · Onboarding & engagement
- FR-F1 First-login survey + guided tour.
- FR-F2 Daily streaks, dashboard analytics (questions, accuracy, time, points).
- FR-F3 An in-app LLM "Scholar" chat assistant.

## 3. Non-functional requirements

| ID | Requirement | Design response |
|----|-------------|-----------------|
| NFR-1 **Latency** | "Next question" must feel instant | LLM/RAG runs **ahead of time**; the hot path pops a pre-generated question from Redis |
| NFR-2 **Throughput/cost** | Respect LLM rate limits & budget | Batched pre-generation, dedup, `429` backoff, warm-queue watermarks |
| NFR-3 **Correctness of adaptation** | Difficulty must actually track ability | 1PL IRT with ZPD targeting; per-concept θ |
| NFR-4 **Trust/safety** | No hallucinated "facts" served | Multi-source grounding + validation + governance |
| NFR-5 **Security** | Protect accounts & data | HttpOnly cookie, CSRF, admin authz, rate limits, redacted logs |
| NFR-6 **Scalability** | Scale the API horizontally | Stateless API; shared PostgreSQL + Redis; (optional) cross-process locks |
| NFR-7 **Operability** | Diagnose & recover | Structured logs, monitoring, health endpoints, data-repair tooling |
| NFR-8 **Maintainability** | Change behaviour without rewrites | Config-driven knobs; thin routers → services → models |

## 4. Key trade-offs recorded up front

- **Freshness vs. cost** — every served question could be freshly generated
  (maximally personalised) but that is slow and expensive. AdaptIQ **pre-generates
  into difficulty buckets** and reuses, trading a little personalisation for a lot
  of speed and predictable cost.
- **Trust vs. coverage** — strict validation rejects some usable questions
  (lower coverage) to guarantee quality. The reference build errs toward trust.
- **Simplicity vs. precision** — a full 2-/3-parameter IRT model with per-item
  discrimination is more precise but harder to calibrate online; AdaptIQ uses the
  **1-parameter (1PL/Rasch)** model for a robust, cheap online update. See
  [04 — Learning Engine](./04-learning-engine.md).

---

Next: [03 — Architecture & Technology Choices](./03-architecture-and-technology-choices.md).
