# Project Cleanup and Professionalization Plan

Date: 2026-04-14

Status note as of 2026-06-06: this is a historical cleanup plan, not a current
to-do list. The active code now uses `backend/schemas/*` for most API schemas,
while `backend/pydantic_visual.py` remains a live root-level Visual Room schema
module imported by `backend/routers/visual_room.py`.

## Goals
- Make the repository structure predictable and professional.
- Keep backend root focused on app entry/config only.
- Remove random scripts/docs from code roots.
- Keep generated/runtime files out of version control.
- Make setup and delivery reproducible for any new developer.

## Current Issues
- Backend root has one-off scripts mixed with application code.
- Backend root has many schema files that should live in a dedicated package.
- Runtime artifacts are present in backend root (logs and pid).
- Project and backend markdown status files are scattered across source roots.
- No repository-level gitignore appears to exist at the project root.

## Target Structure (High Level)
- Root: only product-level files and top-level folders (backend, frontend, docs, res, reference/archive).
- Backend root: entrypoint/config/build files and domain packages only.
- Backend scripts: all maintenance/admin/data scripts under scripts with clear subfolders.
- Docs: all operational/status/report markdown files under a docs tree with an index.
- Generated/log/runtime data: excluded from git and written to dedicated runtime/output folders.

## Phase 1: Baseline and Safety (0.5 day)
- Create a cleanup branch.
- Snapshot current behavior: run backend tests and frontend build once.
- Record command baseline in one docs page: setup, test, run, build.
- Confirm owners for each file category (app code, scripts, docs, generated outputs).

## Phase 2: Backend Root Cleanup (1 to 2 days)
- Move one-off scripts out of backend root into scripts subfolders.
- Proposed moves:
- create_test_user.py -> scripts/users/create_test_user.py
- init_custom_db.py -> scripts/db/init_custom_db.py
- test.py -> scripts/dev/smoke_test.py (or tests if it is a real test)
- bcrypt_utils.py -> services/security/bcrypt_utils.py (or common utils package)
- Schema modules are now mostly consolidated under `backend/schemas/`.
- Active schema modules include `schemas/types.py`, `schemas/challenge.py`,
  `schemas/custom.py`, `schemas/onboarding.py`, `schemas/pvp.py`, and
  `schemas/auth.py`.
- `backend/pydantic_visual.py` is still active and should not be treated as
  deleted until its imports are moved deliberately.

## Phase 3: Documentation Consolidation (0.5 to 1 day)
- Keep only README files in source roots.
- Move status/plan/report markdown files into docs with clear categories.
- Suggested categories:
- docs/architecture
- docs/operations
- docs/testing
- docs/reports
- Add docs/README.md as an index with links and ownership.
- Rename files with consistent style and dates where relevant.

## Phase 4: Repo Hygiene and Ignore Rules (0.5 day)
- Add repository-level .gitignore at project root.
- Ensure ignore coverage for:
- Python virtual environments, caches, compiled files
- Node modules and build outputs
- Logs, pid files, generated reports, local DB files
- Untrack already-committed runtime artifacts from git history moving forward.
- Keep sample environment files only (.env.example), exclude local env files.

## Phase 5: Professional Tooling and Automation (1 day)
- Backend standards: formatter/linter/import sort and optional type checks.
- Frontend standards: lint and build checks.
- Add pre-commit hooks for formatting/linting.
- Add CI pipeline with minimum gates:
- backend tests
- frontend install and build
- lint checks
- Add one setup script for new machines and one archive script for lightweight source zips.

## Definition of Done
- Backend root contains only core app files and package directories.
- No random one-off scripts in backend root.
- No markdown status/report files in backend root (except README).
- No runtime artifacts tracked (logs, pid, generated output).
- Docs are centralized and indexed.
- Fresh clone setup works end-to-end with documented commands.
- CI passes on default branch.

## Recommended Execution Order This Week
1. Implement Phase 4 first (.gitignore and untracking) to stop future clutter.
2. Execute Phase 2 (script and schema relocation with import updates).
3. Execute Phase 3 (docs move and index).
4. Add Phase 5 automation and CI gates.
5. Run full verification and publish a cleanup summary.
