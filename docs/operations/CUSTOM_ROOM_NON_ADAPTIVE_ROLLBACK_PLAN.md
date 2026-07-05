# Custom Room Non-Adaptive Rollback Plan

Date: 2026-04-14

## Objective
- Return Custom Room to a simple topic-practice mode (no adaptive learning).
- Preserve current page quality and overall user flow.
- Reuse oldstate behavior where useful, without reintroducing old security weaknesses.

## Deep Comparison Summary

### Current project (adaptive/hardened)
- Backend adds concept-driven adaptivity and rotation in [backend/routers/custom.py](../../backend/routers/custom.py).
- Adaptive controls include:
- `CUSTOM_REQUIRE_RAG_GENERATION` gate ([backend/routers/custom.py](../../backend/routers/custom.py#L66)).
- Dynamic difficulty from theta (`_target_difficulty_for_user`) ([backend/routers/custom.py](../../backend/routers/custom.py#L415)).
- Dynamic focus concept selection (`_pick_dynamic_focus_concept`) ([backend/routers/custom.py](../../backend/routers/custom.py#L703)).
- Repeat-queue mechanics (`UserConceptRepeatQueue`) ([backend/routers/custom.py](../../backend/routers/custom.py#L1614)).
- Concept theta updates on answer submit (`UserConceptTheta`) ([backend/routers/custom.py](../../backend/routers/custom.py#L2066)).
- Server authority/security improvements:
- Question must be issued for session before submit ([backend/routers/custom.py](../../backend/routers/custom.py#L2023)).
- Auth ownership checks with `Depends(get_current_user)` ([backend/routers/custom.py](../../backend/routers/custom.py#L1192)).
- Current schema intentionally avoids exposing `correct_answer` in question payload ([backend/schemas/custom.py](../../backend/schemas/custom.py#L53)).

### Oldstate project (simple/non-adaptive)
- Old router uses direct fixed generation:
- `difficulty = 3`, `strategy = "direct"` ([oldstate/mw/mhd/adaptiq-backend/routers/custom.py](../../../oldstate/mw/mhd/adaptiq-backend/routers/custom.py#L173)).
- No concept adaptation loops.
- No auth dependency on Custom endpoints ([oldstate/mw/mhd/adaptiq-backend/routers/custom.py](../../../oldstate/mw/mhd/adaptiq-backend/routers/custom.py#L86)).
- Old contract exposes `correct_answer` to frontend and expects it back on submit:
- old schema question response includes `correct_answer` ([oldstate/mw/mhd/adaptiq-backend/pydantic_custom.py](../../../oldstate/mw/mhd/adaptiq-backend/pydantic_custom.py#L49)).
- submit requires `correct_answer` from client ([oldstate/mw/mhd/adaptiq-backend/pydantic_custom.py](../../../oldstate/mw/mhd/adaptiq-backend/pydantic_custom.py#L22)).

### Frontend parity finding
- Current [frontend/src/pages/CustomRoom.tsx](../../frontend/src/pages/CustomRoom.tsx) is already very close to oldstate visual flow.
- Main differences are API/security wiring and optional concept metadata:
- Current uses auth headers and configurable API base ([frontend/src/services/customService.ts](../../frontend/src/services/customService.ts#L40)).
- Current no longer depends on `correct_answer` being present in question payload ([frontend/src/types/custom.ts](../../frontend/src/types/custom.ts#L53)).
- Oldstate used local UUID fallback and direct localhost URL ([oldstate/mw/mhd/prot1/src/services/customService.ts](../../../oldstate/mw/mhd/prot1/src/services/customService.ts#L10), [oldstate/mw/mhd/prot1/src/services/customService.ts](../../../oldstate/mw/mhd/prot1/src/services/customService.ts#L19)).

## Recommendation
Do a **safe rollback** of behavior, not a full protocol rollback.

- Keep current secure contract and auth model.
- Replace adaptive question-selection logic with oldstate-style direct generation.
- Keep current UI page structure (already aligned with oldstate style).

This gives oldstate simplicity without answer leakage or session-auth regressions.

## Execution Plan

### Phase 1: Add a mode flag (low risk)
- Add `CUSTOM_ROOM_SIMPLE_MODE` in [backend/config.py](../../backend/config.py) (default `false`).
- Document it in backend env example files.

### Phase 2: Backend generation rollback (core)
- In [backend/routers/custom.py](../../backend/routers/custom.py), create a simple generation path enabled by `CUSTOM_ROOM_SIMPLE_MODE`:
- Use fixed `difficulty=3` and `strategy="direct"` equivalent to oldstate logic.
- Skip adaptive helpers:
- `_target_difficulty_for_user`
- `_pick_dynamic_focus_concept`
- repeat queue reads/writes
- strict keyword rotation heuristics
- Keep current secure behavior:
- session ownership checks
- server-side grading from `question_bank`
- question-issued guard
- keep response without `correct_answer`.

### Phase 3: Backend submit rollback (partial)
- In `submit_answer`, when `CUSTOM_ROOM_SIMPLE_MODE=true`:
- Continue normal session/mastery updates.
- Skip concept-theta and repeat-queue updates (`UserConceptTheta`, `UserConceptRepeatQueue`).

### Phase 4: Frontend alignment with minimal churn
- Keep [frontend/src/pages/CustomRoom.tsx](../../frontend/src/pages/CustomRoom.tsx) layout and interactions.
- Optional cleanup only (not required):
- Remove dormant concept display line if you want pure non-adaptive UX.
- Keep current auth/API wiring; do not reintroduce localhost hardcoding.

### Phase 5: Test adjustments
- Update/add tests to reflect simple mode behavior:
- New test: simple mode bypasses concept/adaptive updates.
- Keep existing contract tests that verify no `correct_answer` leak and server-authoritative submit.
- Review custom policy tests that currently assert geography-scoping/rotation behavior:
- [backend/tests/test_custom_generation_policy.py](../../backend/tests/test_custom_generation_policy.py)
- [backend/tests/test_custom_geo_scope_live.py](../../backend/tests/test_custom_geo_scope_live.py)

### Phase 6: Rollout
1. Deploy with `CUSTOM_ROOM_SIMPLE_MODE=false` (no change).
2. Enable in staging and run smoke + integration checks.
3. Enable in production.
4. Keep ability to toggle off instantly if regressions appear.

## Why not full oldstate restore
- Full oldstate contract leaks answer to client and trusts client for grading.
- Full oldstate router has no auth ownership checks for Custom endpoints.
- These are regressions versus the current security posture.

## File Touch List (planned)
- [backend/config.py](../../backend/config.py)
- [backend/routers/custom.py](../../backend/routers/custom.py)
- [backend/tests/test_custom_generation_policy.py](../../backend/tests/test_custom_generation_policy.py)
- [backend/tests/test_custom.py](../../backend/tests/test_custom.py)
- [backend/.env.example](../../backend/.env.example)
- Optional: [frontend/src/pages/CustomRoom.tsx](../../frontend/src/pages/CustomRoom.tsx)

## Acceptance Criteria
- Custom Room generates topic-focused questions without adaptive concept difficulty.
- Current Custom Room page still works visually and functionally.
- No `correct_answer` in question payload.
- Submit grading remains server-authoritative.
- Profile/admin pages do not break due to Custom changes.
