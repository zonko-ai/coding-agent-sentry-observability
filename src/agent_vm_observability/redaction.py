from __future__ import annotations

import hashlib
import re
from typing import Any

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|auth[_-]?token|access[_-]?token|refresh[_-]?token|password|secret)=([^&\s]+)"),
    re.compile(r"(?i)(ANTHROPIC_API_KEY|OPENAI_API_KEY|SENTRY_AUTH_TOKEN|SENTRY_DSN|WORKOS_API_KEY)([=:]\s*)([^\s\"']+)"),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{12,})\b"),
    re.compile(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"),
]


def redact_text(value: str, max_len: int = 4000) -> str:
    redacted = value
    for pattern in SECRET_PATTERNS:
        if pattern.pattern.startswith("(?i)(api"):
            redacted = pattern.sub(lambda m: f"{m.group(1)}=[redacted]", redacted)
        elif "ANTHROPIC_API_KEY" in pattern.pattern:
            redacted = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", redacted)
        elif "sk-" in pattern.pattern:
            redacted = pattern.sub("[redacted-api-key]", redacted)
        else:
            redacted = pattern.sub("[redacted-email]", redacted)
    if len(redacted) > max_len:
        return redacted[:max_len] + f"...[truncated {len(redacted) - max_len} chars]"
    return redacted


def scrub(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [scrub(item) for item in value[:200]]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if re.search(r"(?i)(token|secret|password|api[_-]?key|dsn)", key_text):
                result[key_text] = "[redacted]"
            else:
                result[key_text] = scrub(item)
        return result
    return value


def short_hash(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:16]


def safe_tag_value(value: Any) -> str:
    text = redact_text(str(value), max_len=180)
    return text.replace("\n", " ")


def safe_path_tag_value(value: Any) -> str:
    text = str(value)
    if text.startswith("/Users/") or text.startswith("/home/") or text.startswith("~"):
        return f"path:{short_hash(text)}"
    return safe_tag_value(value)
