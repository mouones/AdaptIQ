"""Redact auth material from Newman JSON reports."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


REDACTED = "[REDACTED]"
ACCESS_COOKIE = "adaptiq_" + "access"
CSRF_COOKIE = "adaptiq_" + "csrf"

SENSITIVE_KEYS = {
    "access_token",
    "authorization",
    "cookie",
    "csrf_token",
    "id_token",
    "refresh_token",
    "set-cookie",
    "token",
}

TOKEN_PATTERNS = [
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(rf"({ACCESS_COOKIE}=)[^;\"\s]+", re.IGNORECASE),
    re.compile(rf"({CSRF_COOKIE}=)[^;\"\s]+", re.IGNORECASE),
    re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
]


def sanitize_text(value: str) -> str:
    sanitized = value
    for pattern in TOKEN_PATTERNS:
        if pattern.groups:
            sanitized = pattern.sub(lambda match: f"{match.group(1)}{REDACTED}", sanitized)
        else:
            sanitized = pattern.sub(REDACTED, sanitized)
    return sanitized


def sanitize_payload(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {item_key: sanitize_payload(item_value, key=item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        if key and key.strip().lower() in SENSITIVE_KEYS:
            return REDACTED if value.strip() else value
        return sanitize_text(value)
    return value


def sanitize_file(input_path: Path, output_path: Path) -> None:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    sanitized = sanitize_payload(data)
    output_path.write_text(json.dumps(sanitized, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Newman JSON report to sanitize")
    parser.add_argument("output", type=Path, nargs="?", help="Destination path. Omit with --in-place.")
    parser.add_argument("--in-place", action="store_true", help="Overwrite the input report")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.in_place:
        output_path = args.input
    elif args.output:
        output_path = args.output
    else:
        print("Provide an output path or use --in-place.", file=sys.stderr)
        return 2
    sanitize_file(args.input, output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
