"""Regression tests for Newman report auth redaction."""

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.sanitize_newman_report import REDACTED, sanitize_file, sanitize_payload, sanitize_text  # noqa: E402


def test_sanitize_text_redacts_tokens_and_cookies():
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature"
    value = f"Bearer abc.def.ghi; adaptiq_access={token}; adaptiq_csrf=csrf-value"

    sanitized = sanitize_text(value)

    assert "Bearer abc" not in sanitized
    assert token not in sanitized
    assert "csrf-value" not in sanitized
    assert sanitized.count(REDACTED) >= 3


def test_sanitize_payload_redacts_sensitive_keys():
    payload = {
        "request": {
            "headers": [
                {"key": "Authorization", "value": "Bearer live-token"},
                {"key": "Set-Cookie", "value": "adaptiq_access=live.jwt.sig; Path=/"},
            ],
            "access_token": "raw-token",
        }
    }

    sanitized = sanitize_payload(payload)

    as_text = json.dumps(sanitized)
    assert "live-token" not in as_text
    assert "live.jwt.sig" not in as_text
    assert "raw-token" not in as_text


def test_sanitize_file_writes_redacted_json(tmp_path):
    input_path = tmp_path / "newman.json"
    output_path = tmp_path / "newman.redacted.json"
    input_path.write_text(
        json.dumps({"cookie": "adaptiq_csrf=secret-csrf", "ok": True}),
        encoding="utf-8",
    )

    sanitize_file(input_path, output_path)

    sanitized = output_path.read_text(encoding="utf-8")
    assert "secret-csrf" not in sanitized
    assert REDACTED in sanitized
