#!/usr/bin/env python3
"""
Run a broad AdaptIQ audit without stopping on the first failure.

The script records every command outcome in generated/validation_runs so a broken
feature is visible in the report but does not block later checks.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "generated" / "validation_runs"
OUT_DIR.mkdir(parents=True, exist_ok=True)
STAMP = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
JSON_REPORT = OUT_DIR / f"heavy_audit_{STAMP}.json"
MD_REPORT = OUT_DIR / f"heavy_audit_{STAMP}.md"


def run_step(name: str, command: list[str], cwd: Path, timeout: int = 300) -> dict[str, Any]:
    start = time.time()
    print(f"\n=== {name} ===", flush=True)
    print("$ " + " ".join(command), flush=True)
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        output = completed.stdout or ""
        status = "passed" if completed.returncode == 0 else "failed"
        print(output[-4000:], flush=True)
        return {
            "name": name,
            "status": status,
            "returncode": completed.returncode,
            "duration_seconds": round(time.time() - start, 2),
            "command": command,
            "cwd": str(cwd.relative_to(ROOT)),
            "output_tail": output[-12000:],
        }
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        print(output[-4000:], flush=True)
        print(f"TIMEOUT after {timeout}s", flush=True)
        return {
            "name": name,
            "status": "timeout",
            "returncode": None,
            "duration_seconds": round(time.time() - start, 2),
            "command": command,
            "cwd": str(cwd.relative_to(ROOT)),
            "output_tail": output[-12000:],
            "error": f"Timed out after {timeout}s",
        }
    except Exception as exc:
        print(f"ERROR: {exc}", flush=True)
        return {
            "name": name,
            "status": "error",
            "returncode": None,
            "duration_seconds": round(time.time() - start, 2),
            "command": command,
            "cwd": str(cwd.relative_to(ROOT)),
            "output_tail": "",
            "error": repr(exc),
        }


def main() -> int:
    py = sys.executable
    npm_cmd = "npm.cmd" if platform.system().lower().startswith("win") else "npm"

    steps = [
        ("backend syntax compile", [py, "-m", "compileall", "-q", "backend"], ROOT, 180),
        ("backend focused regression tests", [py, "-m", "pytest", "backend/tests/unit/test_challenge_option_counts.py", "backend/tests/unit/test_custom_generation_policy.py", "backend/tests/unit/test_config_runtime_knobs.py", "-q"], ROOT, 240),
        ("backend unit tests", [py, "-m", "pytest", "backend/tests/unit", "-q"], ROOT, 600),
        ("backend integration tests", [py, "-m", "pytest", "backend/tests/integration", "-q"], ROOT, 900),
        ("frontend typecheck", [npm_cmd, "run", "lint"], ROOT / "frontend", 300),
        ("frontend production build", [npm_cmd, "run", "build"], ROOT / "frontend", 300),
        ("frontend e2e tests", [npm_cmd, "run", "test:e2e"], ROOT / "frontend", 900),
    ]

    results = [run_step(*step) for step in steps]
    summary = {
        "started_at_utc": STAMP,
        "project_root": str(ROOT),
        "python": sys.version,
        "platform": platform.platform(),
        "totals": {
            "passed": sum(1 for r in results if r["status"] == "passed"),
            "failed": sum(1 for r in results if r["status"] == "failed"),
            "timeout": sum(1 for r in results if r["status"] == "timeout"),
            "error": sum(1 for r in results if r["status"] == "error"),
            "total": len(results),
        },
        "results": results,
    }
    JSON_REPORT.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = [
        "# AdaptIQ Heavy Audit",
        "",
        f"Generated: {STAMP} UTC",
        "",
        "| Step | Status | Return code | Duration |",
        "|---|---:|---:|---:|",
    ]
    for r in results:
        lines.append(f"| {r['name']} | {r['status']} | {r['returncode']} | {r['duration_seconds']}s |")
    lines += ["", "## Failure / Output Tails", ""]
    for r in results:
        if r["status"] != "passed":
            lines += [f"### {r['name']}", "", "```", r.get("output_tail", "")[-4000:], "```", ""]
    MD_REPORT.write_text("\n".join(lines), encoding="utf-8")

    print(f"\nJSON report: {JSON_REPORT}")
    print(f"Markdown report: {MD_REPORT}")
    # Always return 0 so CI/user terminal can inspect the complete audit report.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
