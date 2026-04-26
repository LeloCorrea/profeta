import json
import logging
import re
from typing import Any


SENSITIVE_KEYWORDS = (
    "token",
    "secret",
    "password",
    "authorization",
    "api_key",
    "apikey",
)

SENSITIVE_PATTERNS = (
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"\b\d{8,}:[A-Za-z0-9_-]{20,}\b"),
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def _sanitize_scalar(key: str, value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value

    key_lower = key.lower()
    text = str(value)

    if any(keyword in key_lower for keyword in SENSITIVE_KEYWORDS):
        return "[REDACTED]"

    if any(pattern.search(text) for pattern in SENSITIVE_PATTERNS):
        return "[REDACTED]"

    if len(text) > 240:
        return f"{text[:237]}..."

    return text


def sanitize_fields(**fields: Any) -> dict[str, Any]:
    return {key: _sanitize_scalar(key, value) for key, value in fields.items()}


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    payload = {"event": event, **sanitize_fields(**fields)}
    logger.log(level, json.dumps(payload, ensure_ascii=False, default=str))