# AdaptIQ validation scripts

Put these scripts in your project under:

```text
scripts/run_all_validation.ps1
scripts/run_all_validation.sh
```

Then run from the project root.

## Windows PowerShell

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_all_validation.ps1 -Install
```

Fast rerun:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_all_validation.ps1
```

## Git Bash / Linux / macOS

```bash
bash scripts/run_all_validation.sh --install
```

Fast rerun:

```bash
bash scripts/run_all_validation.sh
```

## What it runs

- Docker Compose postgres + redis
- Backend dependency check/install
- Backend pytest suite
- Backend health check
- Postman/Newman collection if a collection JSON exists in the project
- Frontend build and tests if configured
- Playwright E2E if configured

## Output

Each run creates:

```text
generated/validation_runs/<timestamp>/
```

Inside it you get:

- `summary.txt`
- `summary.md`
- command logs
- pytest JUnit XML
- Newman JSON report if Postman collection is found

## Important

Export your Postman collection JSON into the project folder before running if you want the Postman/Newman numbers to be generated automatically.
