"""Shared security helpers for redaction and safe display payloads."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from typing import Any


SENSITIVE_COLUMN_KEYWORDS = (
    "password",
    "hash",
    "token",
    "secret",
    "otp",
    "api_key",
    "apikey",
    "key",
    "credential",
)


_EMAIL_RE = re.compile(r"([^@\s]+)@([^@\s]+\.[^@\s]+)")
_SAFE_PATH_RE = re.compile(r"^[MmZzLlHhVvCcSsQqTtAa0-9,\.\-\+\s]+$")
_SAFE_VIEWBOX_RE = re.compile(r"^\s*-?\d+(?:\.\d+)?\s+-?\d+(?:\.\d+)?\s+\d+(?:\.\d+)?\s+\d+(?:\.\d+)?\s*$")


def stable_digest(value: Any, length: int = 12) -> str:
    text = "" if value is None else str(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def redact_email(value: Any) -> str:
    text = "" if value is None else str(value)
    match = _EMAIL_RE.fullmatch(text.strip())
    if not match:
        return "[REDACTED]"
    local, domain = match.groups()
    prefix = local[:2] if len(local) >= 2 else local[:1]
    return f"{prefix}***@{domain}"


def is_sensitive_column(column_name: str) -> bool:
    normalized = (column_name or "").lower()
    return any(keyword in normalized for keyword in SENSITIVE_COLUMN_KEYWORDS)


def redact_db_value(column_name: str, value: Any) -> Any:
    normalized = (column_name or "").lower()
    if is_sensitive_column(normalized):
        return "[REDACTED]"
    if normalized == "email" or normalized.endswith("_email"):
        return redact_email(value)
    return value


def redact_log_value(value: Any, max_len: int = 80) -> str:
    text = "" if value is None else str(value)
    text = _EMAIL_RE.sub(lambda m: redact_email(m.group(0)), text)
    text = re.sub(r"\b\d{4,8}\b", "[CODE]", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._\-]+", "Bearer [REDACTED]", text, flags=re.I)
    text = text.replace("\r", " ").replace("\n", " ")
    return text[:max_len]


def safe_svg_shape_payload(svg: str | None) -> dict[str, str] | None:
    """Extract a safe path-only SVG payload from generated silhouette SVG."""
    if not svg:
        return None
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return None

    tag = root.tag.rsplit("}", 1)[-1].lower()
    if tag != "svg":
        return None

    view_box = root.attrib.get("viewBox") or root.attrib.get("viewbox") or "0 0 240 160"
    if not _SAFE_VIEWBOX_RE.fullmatch(view_box):
        return None

    path_data: str | None = None
    for node in root.iter():
        node_tag = node.tag.rsplit("}", 1)[-1].lower()
        if node_tag in {"script", "foreignobject", "iframe", "object", "embed", "image", "use"}:
            return None
        for attr_name, attr_value in node.attrib.items():
            normalized_attr = attr_name.lower()
            if normalized_attr.startswith("on"):
                return None
            if normalized_attr in {"href", "xlink:href", "src"}:
                return None
            if isinstance(attr_value, str) and "javascript:" in attr_value.lower():
                return None
        if node_tag == "path" and not path_data:
            candidate = node.attrib.get("d", "")
            if candidate and _SAFE_PATH_RE.fullmatch(candidate):
                path_data = candidate

    if not path_data:
        return None
    return {"path": path_data, "viewBox": view_box}
