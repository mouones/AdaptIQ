"""Lightweight repository secret scanner.

Prints only file, line, and pattern name so secret values are not echoed.
"""

from __future__ import annotations

import re
import sys
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "logs",
    "generated",
    ".pytest_cache",
}
SKIP_PREFIXES = {
    ("docs", "reference"),
    ("backend", "logs"),
    ("frontend", "dist"),
}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".pyc"}

PATTERNS = {
    "groq_api_key": re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    "gemini_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    "aws_access_key_id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "private_key_block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    "jwt_token": re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    "bearer_token": re.compile(
        r"\bBearer\s+(?:eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|[A-Za-z0-9._~+/=-]{20,})",
        re.I,
    ),
    "access_cookie": re.compile(r"\badaptiq_access=(?!\[REDACTED\])[^;\"\s]{20,}", re.I),
    "csrf_cookie": re.compile(r"\badaptiq_csrf=(?!\[REDACTED\])[^;\"\s]{16,}", re.I),
    "jwt_secret_assignment": re.compile(r"JWT_SECRET_KEY\s*=\s*[^\s#]+"),
    "password_assignment": re.compile(r"PASSWORD\s*=\s*[^\s#]+", re.I),
}

# High-confidence provider-key patterns to also scan across git history (values
# that must never appear in any commit, not just the working tree). The working
# tree can be clean while an old commit still leaks a key — history is where the
# .env.test Groq key hid before the public-repo push.
HISTORY_PATTERNS = {
    "groq_api_key": PATTERNS["groq_api_key"],
    "openai_key": PATTERNS["openai_key"],
    "gemini_api_key": PATTERNS["gemini_api_key"],
    "aws_access_key_id": PATTERNS["aws_access_key_id"],
    "slack_token": PATTERNS["slack_token"],
    "github_token": PATTERNS["github_token"],
    "private_key_block": PATTERNS["private_key_block"],
}

SAFE_ASSIGNMENT_VALUES = (
    "",
    "change_this",
    "changeme",
    "placeholder",
    "replace_with",
    "test",
    "example",
    "dummy",
)


def should_skip(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    rel_parts = set(rel.parts)
    return (
        bool(rel_parts & SKIP_DIRS)
        or path.suffix.lower() in SKIP_SUFFIXES
        or any(rel.parts[: len(prefix)] == prefix for prefix in SKIP_PREFIXES)
    )


def candidate_paths() -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        paths = [ROOT / line.strip() for line in result.stdout.splitlines() if line.strip()]
        return [path for path in paths if path.exists()]
    except Exception:
        return [path for path in ROOT.rglob("*") if path.is_file()]


def is_safe_assignment_line(name: str, line: str) -> bool:
    if name not in {"jwt_secret_assignment", "password_assignment"}:
        return False
    value = line.split("=", 1)[-1].strip().strip('"').strip("'")
    return any(value.lower().startswith(prefix) for prefix in SAFE_ASSIGNMENT_VALUES)


# Files that legitimately contain dummy token strings as test fixtures. The
# scanner allowlists its own source and the newman-sanitizer test for the token
# patterns only (never for real provider-key patterns).
_TOKEN_FIXTURE_FILES = {
    ("scripts", "scan_secrets.py"),
    ("backend", "tests", "unit", "test_newman_report_sanitizer.py"),
}


def is_safe_scanner_source_line(path: Path, name: str) -> bool:
    rel = path.relative_to(ROOT)
    return rel.parts in _TOKEN_FIXTURE_FILES and name in {
        "jwt_token",
        "bearer_token",
        "access_cookie",
        "csrf_cookie",
    }


def scan_history() -> list[str]:
    """Scan every blob in git history for high-confidence provider keys.

    Reports commit:path:pattern (never the value). This catches secrets that were
    committed and later removed from the working tree but still live in history.
    """
    findings: list[str] = []
    try:
        rev_list = subprocess.run(
            ["git", "-C", str(ROOT), "rev-list", "--all"],
            text=True, capture_output=True, check=True,
        ).stdout.split()
    except Exception:
        print("history scan skipped (not a git repo or git unavailable)")
        return findings

    for name, pattern in HISTORY_PATTERNS.items():
        try:
            result = subprocess.run(
                ["git", "-C", str(ROOT), "grep", "-I", "-l", "-P", pattern.pattern, *rev_list],
                text=True, capture_output=True,
            )
        except Exception:
            continue
        for line in result.stdout.splitlines():
            if line.strip():
                findings.append(f"{line.strip()}: {name} (in history)")
    return sorted(set(findings))


def main() -> int:
    include_history = "--history" in sys.argv
    findings: list[str] = []
    for path in candidate_paths():
        if should_skip(path):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            for name, pattern in PATTERNS.items():
                if (
                    pattern.search(line)
                    and not is_safe_assignment_line(name, line)
                    and not is_safe_scanner_source_line(path, name)
                ):
                    rel = path.relative_to(ROOT)
                    findings.append(f"{rel}:{line_no}: {name}")

    if include_history:
        findings.extend(scan_history())

    if findings:
        print("Potential secrets found:")
        print("\n".join(findings))
        return 1
    print("No secret patterns found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
