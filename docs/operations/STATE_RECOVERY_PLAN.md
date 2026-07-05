# State Recovery Plan

## Goal
Restore room UX behavior from the old baseline while keeping the current backend architecture and data model.

- UI behavior source: oldstate/mw/mhd/prot1
- Backend/API source: mhd/backend (current)

## Non-Negotiables (Keep Current State)
- JWT login/signup/token flow
- Alembic + current schema
- Redis-backed sessions and cache patterns
- Concept-oriented adaptation and concept tables
- Current auth guards and admin protections
- Current question_bank and concept link model

## Baseline Mapping
- Old baseline root: oldstate/mw/mhd/prot1
- Current app root: mhd/frontend
- Current backend root: mhd/backend

## Frontend Delta Snapshot (old -> current)
- Classic page: 43 add / 23 del
- Challenge page: 10 add / 3 del
- Custom page: 77 add / 8 del
- apiService: 96 add / 33 del
- challengeService: 3 add / 3 del
- customService: 65 add / 27 del
- challenge types: 2 add / 1 del
- custom types: 6 add / 0 del
- App shell/routes: 15 add / 4 del

## Main Root-Cause Categories
1. Frontend-backend contract drift
- hidden answer fields and submit payload mismatches
- response shape mismatches between room pages and services

2. Topic-concept routing drift (Custom room)
- topic-specific screens can route to unrelated concept data
- strict topic label was not always enforced before generation

3. Session lifecycle drift
- stale session IDs and inconsistent reset/start semantics

4. UI feedback state drift
- answer correctness rendering derived from local heuristics instead of backend truth

## Workstreams

### WS1: Contract Freeze and Room API Adapters
Create and enforce one adapter per room in frontend services:
- Classic: use submit response as single source of truth for correctness and explanation
- Challenge: never rely on question.correctAnswer at generation time; rely on submit response
- Custom: enforce topic and concept identity from selection to generation and submit

Done/Active notes:
- Classic and Challenge adapter fixes applied in current state.
- Continue validating all pages against current API payloads.

### WS2: Room Behavior Parity (Old UX, Current Backend)
#### Classic Room
- keep old visual interaction flow
- keep current secure backend flow (hidden answer until submit)
- ensure next-question transition is resilient

#### Challenge Room
- keep old gauntlet UX
- use submit response for correct/wrong highlighting and context
- preserve current rank/level backend logic

#### Custom Room
- preserve old navigation UX (history themes + map)
- enforce strict topic relevance in generated questions
- only show concept picker when concept matches selected topic label

#### PvP Room
- no old page equivalent exists in old baseline
- keep current PvP page as source and validate against current backend contracts

### WS3: Cross-Room Regression Matrix
For each room, verify:
- start session works
- question generation works
- answer submit returns expected payload
- correctness visualization is accurate
- explanation/context is non-empty or safe fallback
- session progression reaches summary without dead-ends

### WS4: Auth + Navigation Integrity
- verify route guards
- verify token expiry handling
- verify room entry from dashboard and direct route reload

## File Priorities

### Highest Priority
- frontend/src/services/apiService.ts
- frontend/src/services/challengeService.ts
- frontend/src/services/customService.ts
- frontend/src/pages/ClassicRoom.tsx
- frontend/src/pages/ChallengeRoom.tsx
- frontend/src/pages/CustomRoom.tsx

### Backend Contract Touchpoints
- backend/routers/classic_room.py
- backend/routers/challenge.py
- backend/routers/custom.py
- backend/schemas/types.py
- backend/schemas/challenge.py
- backend/schemas/custom.py

## Execution Order
1. Lock service contracts per room
2. Normalize room page state handling to service contracts
3. Fix strict topic/concept routing for Custom
4. Validate PvP separately with current backend only
5. Run full regression (API + frontend build + browser smoke)
6. Document final behavior and remaining gaps

## Tracking Checklist
- [x] Locate old baseline and current roots
- [x] Build old vs current file map
- [x] Build recovery workstreams
- [x] Complete Classic full browser regression pass
- [x] Complete Challenge full browser regression pass
- [x] Complete Custom full browser regression pass
- [x] Complete PvP full browser regression pass
- [x] Execute full API regression suite with strict checks
- [ ] Final stabilization pass and summary

## Notes Log
- This plan intentionally uses old UI behavior as reference and current backend as authority.
- Any old behavior that conflicts with current security model (example: exposing correct answer before submit) will not be restored.

### Corrections Applied (2026-04-13)
- Challenge now uses fresh auth headers per request (no module-level cached header).
- Challenge submit flow now has an in-flight guard to prevent duplicate answer submits (prevents frontend-triggered 409 races).
- Classic question selection now excludes non-Classic cache sources (`challenge_llm`, `custom_llm`).
- Classic no longer hard-fails when adaptive selection is empty; it auto-generates a new Classic question, auto-discovers/links a concept, and continues session flow.
- Classic next-question dead-end handling in frontend now transitions to summary when pool exhaustion is reported.

### Test Reset Performed (2026-04-13)
- Truncated all public app tables (kept `alembic_version`).
- Flushed Redis runtime state.
- Re-seeded baseline + deterministic test users (`scripts/setup_test_users.py`).

### Validation Snapshot (Post-Reset)
- Classic API progression regression: no `/api/rooms/classic/questions` 500 responses, no answer non-200 statuses.
- Challenge authenticated flow validated (rank/start/generate/submit/end = 200; duplicate submit guard still enforced as 409 server-side).
- Custom authenticated flow validated with World War II topic relevance and successful submit/end.
- PvP lobby/search/cancel flow validated.
- Frontend production build succeeds (`vite build`).

### Continuation Phases
1. Stabilization hardening pass (error UX polish for non-happy-path responses and timeout states).
2. Replace Tailwind CDN script usage with local Tailwind build pipeline.
3. Expand automated authenticated regression scripts in `backend/tests` to match current JWT-protected contracts.
