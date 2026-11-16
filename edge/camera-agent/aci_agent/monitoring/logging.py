"""Structured logging with credential redaction (no secrets in logs)."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime

_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<user>[^:/@\s]+):(?P<pw>[^@\s]+)@")
_KEY_VALUE_SECRET = re.compile(
    r"(?i)(?P<key>password|passwd|secret|token|api[_-]?key|authorization)(?P<sep>\s*[=:]\s*)(?P<val>[^\s,;&\"']+)"
)


def redact(text: str) -> str:
    text = _URL_CREDENTIALS.sub(lambda m: f"{m['scheme']}{m['user']}:***@", text)
    return _KEY_VALUE_SECRET.sub(lambda m: f"{m['key']}{m['sep']}***", text)


_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                payload[key] = redact(value) if isinstance(value, str) else value
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = f"{self.formatTime(record)} {record.levelname:<7} {record.name}: {record.getMessage()}"
        if record.exc_info:
            out += "\n" + self.formatException(record.exc_info)
        return redact(out)


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    stream = sys.stdout
    if hasattr(stream, "reconfigure"):
        # Windows defaults stdout to a legacy code page; non-ASCII log text would be dropped.
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter() if json_output else TextFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True
