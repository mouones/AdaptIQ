#!/usr/bin/env bash
# AdaptIQ full validation runner (Linux/macOS/Git Bash)
#
# Run from project root:
#   bash scripts/run_all_validation.sh --install
#
# Fast rerun:
#   bash scripts/run_all_validation.sh
#
# Options:
#   --skip-docker
#   --skip-frontend
#   --skip-postman
#   --skip-e2e
#   --api-base http://localhost:8000

set -Eeuo pipefail

INSTALL=0
SKIP_DOCKER=0
SKIP_FRONTEND=0
SKIP_POSTMAN=0
SKIP_E2E=0
API_BASE="http://localhost:8000"
BACKEND_WAIT_SECONDS=75

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install) INSTALL=1; shift ;;
    --skip-docker) SKIP_DOCKER=1; shift ;;
    --skip-frontend) SKIP_FRONTEND=1; shift ;;
    --skip-postman) SKIP_POSTMAN=1; shift ;;
    --skip-e2e) SKIP_E2E=1; shift ;;
    --api-base) API_BASE="$2"; shift 2 ;;
    --backend-wait) BACKEND_WAIT_SECONDS="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 2 ;;
  esac
done

ROOT="$(pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
RUN_STAMP="$(date +%Y%m%d_%H%M%S)"
RUN_DIR="$ROOT/generated/validation_runs/$RUN_STAMP"
mkdir -p "$RUN_DIR"

FAILURES=0
SUMMARY=()
BACKEND_PID=""

add_summary() {
  SUMMARY+=("$1")
}

run_step() {
  local name="$1"
  shift
  echo
  echo "=== $name ==="
  local start end elapsed
  start="$(date +%s)"
  if "$@"; then
    end="$(date +%s)"
    elapsed="$((end-start))s"
    add_summary "PASS | $name | $elapsed"
    echo "PASS: $name ($elapsed)"
  else
    end="$(date +%s)"
    elapsed="$((end-start))s"
    add_summary "FAIL | $name | $elapsed"
    echo "FAIL: $name"
    FAILURES=$((FAILURES+1))
  fi
}

run_logged() {
  local name="$1"
  local workdir="$2"
  shift 2
  local log="$RUN_DIR/$(echo "$name" | tr -c 'a-zA-Z0-9_-' '_').log"
  echo "$*"
  (cd "$workdir" && "$@") 2>&1 | tee "$log"
  local code="${PIPESTATUS[0]}"
  if [[ "$code" -ne 0 ]]; then
    echo "$name exited with code $code. See $log"
    return "$code"
  fi
}

python_exe() {
  if [[ -x "$BACKEND/.venv/bin/python" ]]; then
    echo "$BACKEND/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    echo "python3"
  elif command -v python >/dev/null 2>&1; then
    echo "python"
  else
    echo "Python not found" >&2
    return 1
  fi
}

http_ok() {
  curl -fsS --max-time 3 "$1" >/dev/null 2>&1
}

wait_backend() {
  local deadline=$((SECONDS + BACKEND_WAIT_SECONDS))
  while [[ "$SECONDS" -lt "$deadline" ]]; do
    if http_ok "$API_BASE/api/system/health"; then
      return 0
    fi
    sleep 2
  done
  return 1
}

cleanup() {
  if [[ -n "${BACKEND_PID:-}" ]] && kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
    echo "Stopping backend process $BACKEND_PID"
    kill "$BACKEND_PID" >/dev/null 2>&1 || true
  fi

  {
    echo "# AdaptIQ validation summary"
    echo
    echo "- Run: $RUN_STAMP"
    echo "- API: $API_BASE"
    echo "- Output folder: $RUN_DIR"
    echo
    echo "## Results"
    for line in "${SUMMARY[@]}"; do
      echo "- $line"
    done
  } > "$RUN_DIR/summary.md"

  printf "%s\n" "${SUMMARY[@]}" > "$RUN_DIR/summary.txt"

  echo
  echo "Summary written to: $RUN_DIR/summary.txt"
  echo "Markdown summary: $RUN_DIR/summary.md"
}
trap cleanup EXIT

add_summary "AdaptIQ validation run: $RUN_STAMP"
add_summary "Root: $ROOT"
add_summary "API base: $API_BASE"

step_preflight() {
  [[ -d "$BACKEND" ]] || { echo "Missing backend folder: $BACKEND"; return 1; }
  if [[ ! -d "$FRONTEND" ]]; then
    echo "Frontend folder not found; frontend steps will be skipped."
  fi
}

step_docker() {
  if [[ ! -f "$ROOT/docker-compose.yml" ]]; then
    echo "docker-compose.yml not found; skipping Docker startup."
    return 0
  fi
  command -v docker >/dev/null 2>&1 || { echo "Docker not found. Use --skip-docker if services are already running."; return 1; }
  run_logged "docker_compose_up" "$ROOT" docker compose up -d postgres redis
}

step_backend_deps() {
  local py
  py="$(python_exe)"
  if [[ "$INSTALL" -eq 1 ]]; then
    if [[ ! -d "$BACKEND/.venv" ]]; then
      run_logged "create_backend_venv" "$BACKEND" "$py" -m venv .venv
    fi
    run_logged "pip_install_backend" "$BACKEND" "$BACKEND/.venv/bin/python" -m pip install -r requirements.txt
  else
    echo "Install skipped. Use --install to install backend requirements."
  fi
}

step_pytest() {
  local py junit
  py="$(python_exe)"
  junit="$RUN_DIR/backend_pytest_junit.xml"
  run_logged "backend_pytest" "$BACKEND" "$py" -m pytest -q --tb=short --junitxml "$junit"
}

step_backend_health() {
  if http_ok "$API_BASE/api/system/health"; then
    echo "Backend already healthy at $API_BASE"
    return 0
  fi
  local py
  py="$(python_exe)"
  (cd "$BACKEND" && nohup "$py" main.py > "$RUN_DIR/backend_server.log" 2> "$RUN_DIR/backend_server.err.log" & echo $! > "$RUN_DIR/backend.pid")
  BACKEND_PID="$(cat "$RUN_DIR/backend.pid")"
  wait_backend || { echo "Backend did not become healthy. See $RUN_DIR/backend_server.log"; return 1; }
}

step_postman() {
  command -v npx >/dev/null 2>&1 || { echo "npx not found. Install Node.js or use --skip-postman."; return 1; }
  local collection
  collection="$(find "$ROOT" -type f \( -name "*.postman_collection.json" -o -iname "*collection*.json" -o -iname "*postman*.json" \) \
    -not -path "*/node_modules/*" -not -path "*/.venv/*" | sort | head -n 1)"
  [[ -n "$collection" ]] || { echo "No Postman collection found. Export your collection JSON into the project, then rerun."; return 1; }
  echo "Using collection: $collection"
  run_logged "postman_newman" "$ROOT" npx newman run "$collection" --env-var "baseUrl=$API_BASE" -r cli,json --reporter-json-export "$RUN_DIR/newman_report.json"
}

step_frontend() {
  [[ -d "$FRONTEND" ]] || { echo "No frontend folder; skipping."; return 0; }
  command -v npm >/dev/null 2>&1 || { echo "npm not found. Install Node.js or use --skip-frontend."; return 1; }
  if [[ "$INSTALL" -eq 1 ]]; then
    run_logged "frontend_npm_install" "$FRONTEND" npm install
  fi
  run_logged "frontend_build" "$FRONTEND" npm run build

  if grep -q '"test"' "$FRONTEND/package.json"; then
    run_logged "frontend_tests" "$FRONTEND" npm test -- --run
  else
    echo "No frontend test script found; skipping npm test."
  fi
}

step_e2e() {
  [[ -d "$FRONTEND" ]] || { echo "No frontend folder; skipping."; return 0; }
  if [[ -f "$FRONTEND/playwright.config.ts" ]]; then
    run_logged "playwright_tests" "$FRONTEND" npx playwright test
  else
    echo "No playwright.config.ts found; skipping E2E."
  fi
}

run_step "Preflight: check folders" step_preflight
if [[ "$SKIP_DOCKER" -eq 0 ]]; then run_step "Start Docker services: postgres + redis" step_docker; fi
run_step "Backend dependencies" step_backend_deps
run_step "Backend pytest suite" step_pytest
run_step "Start backend and verify health" step_backend_health
if [[ "$SKIP_POSTMAN" -eq 0 ]]; then run_step "Postman/Newman API validation" step_postman; fi
if [[ "$SKIP_FRONTEND" -eq 0 ]]; then run_step "Frontend install/build/tests" step_frontend; fi
if [[ "$SKIP_E2E" -eq 0 ]]; then run_step "Playwright E2E tests if configured" step_e2e; fi

if [[ "$FAILURES" -gt 0 ]]; then
  echo
  echo "Validation finished with $FAILURES failed step(s)."
  exit 1
else
  echo
  echo "Validation finished successfully."
fi
