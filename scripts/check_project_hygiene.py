"""Check for generated artifacts and stale runtime references.

This script is read-only. It reports files that should stay in ignored runtime
folders, plus old standalone-admin references in active docs.
"""

from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IGNORED_DIR_NAMES = {
    ".git",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "test-results",
    "playwright-report",
}
ALLOWED_ARTIFACT_DIRS = {
    Path("backend/logs"),
    Path("backend/generated"),
    Path("frontend/logs"),
    Path("frontend/test-results"),
    Path("frontend/playwright-report"),
}
ALLOWED_BACKEND_SCRIPT_ROOT = {
    "admin_diag_postman.py",
    "audit_postman.py",
    "cleanup_stale_data.py",
    "cleanup_test_users.py",
    "generate_real_test_user_history.py",
    "live_room_harvest.py",
    "plagiarism_audit.py",
    "plagiarism_check.py",
    "populate_questions.py",
    "repair_data_integrity.py",
    "reset_question_cache_and_seed.py",
    "setup_test_users.py",
}
ACTIVE_DOC_SUFFIXES = {".md", ".json", ".txt"}


def _relative(path: Path) -> Path:
    return path.relative_to(PROJECT_ROOT)


def _is_inside_any(path: Path, roots: set[Path]) -> bool:
    rel = _relative(path)
    return any(rel == root or root in rel.parents for root in roots)


def _walk_files() -> list[Path]:
    files: list[Path] = []
    for path in PROJECT_ROOT.rglob("*"):
        if any(part in IGNORED_DIR_NAMES for part in path.parts):
            continue
        if path.is_file():
            files.append(path)
    return files


def check_artifacts(files: list[Path]) -> list[str]:
    problems = []
    for path in files:
        rel = _relative(path)
        if path.suffix.lower() in {".log", ".pid"} and not _is_inside_any(path, ALLOWED_ARTIFACT_DIRS):
            problems.append(str(rel))
        if rel.parts[:1] == ("debug_screenshots",):
            problems.append(str(rel))
    return sorted(set(problems))


def check_backend_script_root() -> list[str]:
    script_root = PROJECT_ROOT / "backend" / "scripts"
    if not script_root.exists():
        return []
    return sorted(
        path.name
        for path in script_root.glob("*.py")
        if path.name not in ALLOWED_BACKEND_SCRIPT_ROOT
    )


def check_stale_admin_refs(files: list[Path]) -> list[str]:
    problems = []
    for path in files:
        rel = _relative(path)
        if rel.parts[:2] in {("docs", "reports"), ("docs", "reference")}:
            continue
        if path.suffix.lower() not in ACTIVE_DOC_SUFFIXES:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            lower = line.lower()
            if "localhost:9000" not in lower and "admin_server.py" not in lower:
                continue
            if any(token in lower for token in ("no active", "not active", "historical", "archived", "do not follow")):
                continue
            problems.append(str(rel))
            break
    return sorted(problems)


def main() -> None:
    files = _walk_files()
    report = {
        "artifact_files_outside_runtime_dirs": check_artifacts(files),
        "unexpected_backend_script_root_files": check_backend_script_root(),
        "active_docs_with_stale_admin_server_refs": check_stale_admin_refs(files),
    }
    report["status"] = "ok" if not any(report.values()) else "needs_attention"
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
