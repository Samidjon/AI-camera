"""Structured (JSON) logging with request correlation and secret redaction."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import UTC, datetime

request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

# user:password@host in connection URLs (rtsp://, postgresql://, redis:// ...)
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<user>[^:/@\s]+):(?P<pw>[^@\s]+)@")
_KEY_VALUE_SECRET = re.compile(
    r"(?i)(?P<key>password|passwd|secret|token|api[_-]?key|authorization)(?P<sep>\s*[=:]\s*)(?P<val>[^\s,;&\"']+)"
)
_SENSITIVE_KEYS = {"password", "passwd", "secret", "token", "access_token", "api_key", "authorization", "jwt_secret"}


def redact(text: str) -> str:
    """Remove credentials from free text. Never log secrets."""
    text = _URL_CREDENTIALS.sub(lambda m: f"{m['scheme']}{m['user']}:***@", text)
    return _KEY_VALUE_SECRET.sub(lambda m: f"{m['key']}{m['sep']}***", text)


def redact_mapping(data: dict) -> dict:
    out: dict = {}
    for key, value in data.items():
        if key.lower() in _SENSITIVE_KEYS:
            out[key] = "***"
        elif isinstance(value, dict):
            out[key] = redact_mapping(value)
        elif isinstance(value, str):
            out[key] = redact(value)
        else:
            out[key] = value
    return out


_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        if rid := request_id_ctx.get():
            payload["request_id"] = rid
        extra = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        if extra:
            payload.update(redact_mapping(extra))
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rid = request_id_ctx.get()
        prefix = f"[{rid}] " if rid else ""
        base = f"{self.formatTime(record)} {record.levelname:<7} {record.name}: {prefix}{record.getMessage()}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return redact(base)


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
    # Uvicorn access logs are replaced by our request middleware log.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True
