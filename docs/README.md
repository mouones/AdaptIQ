# AdaptIQ Documentation Index

This folder contains active runbooks, architecture notes, validation artifacts, and historical reports for the live `P_F_E` app.

Use code, migrations, tests, `CLAUDE.md`, and the active README files as the source of truth. Treat archived reference copies and older reports as historical unless a current runbook links to a specific item.

## Active Starting Points

- [Project README](../README.md)
- [Development Runbook](../CLAUDE.md)
- [Backend README](../backend/README.md)
- [Frontend README](../frontend/README.md)

## Architecture

- [Layered Project Walkthrough](architecture/PROJECT_WALKTHROUGH_LAYERED.md)
- [System Documentation](architecture/SYSTEM_DOCUMENTATION.md)

## Operations

- [Admin Dashboard Runbook](operations/ADMIN_DASHBOARD_README.md)
- [Challenge Configuration Notes](operations/env_challenge_setup.md)
- [Governance Runbook](operations/GOVERNANCE_RUNBOOK.md)
- [State Recovery Plan](operations/STATE_RECOVERY_PLAN.md)
- [Project Cleanup Plan](operations/PROJECT_CLEANUP_PLAN.md)
- [Custom Room Rollback Plan](operations/CUSTOM_ROOM_NON_ADAPTIVE_ROLLBACK_PLAN.md)
- [Backend Auth Status](operations/backend/AUTH_CONTINUATION_STATUS.md)
- [Backend Database Configuration](operations/backend/DATABASE_CONFIG.md)

## Testing And Validation

- [Test Users Guide](testing/TEST_USERS.md)
- Postman collection: `api/AdaptIQ_Complete_Postman.json`
- Latest Newman report: `reports/newman_run_latest.json`
- Security audit: [Security Audit 2026-06-03](reports/SECURITY_AUDIT_2026-06-03.md)
- Current state audit: [Project State Audit 2026-06-04](reports/PROJECT_STATE_AUDIT_2026-06-04.md)

## Historical Reference

- Older cleanup/reference material was archived outside this repository during
  development and is not part of the published project.
- `docs/reports/**` contains dated reports. A report is evidence for its date, not guaranteed current runtime behavior.
- Do not copy secrets, `.env` values, or local machine-specific credentials into documentation.
