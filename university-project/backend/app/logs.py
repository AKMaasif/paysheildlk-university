"""Logging: one-line JSON when asked for, and no secrets in any line.

The live stream's address carries the sign-in token in its query string (`/ws?token=...`), and the server's access log
prints that address. Every log line passes through a filter that blanks tokens and other secret-looking query values."""
import json
import logging
import os
import re
import time

_SECRET_QUERY = re.compile(r"((?:token|access_token|refresh_token|password|secret|api_key|apikey|code)=)[^&\s\"']+", re.IGNORECASE)
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE)


def scrub(text: str) -> str:
    return _BEARER.sub(r"\1[removed]", _SECRET_QUERY.sub(r"\1[removed]", text))


class RedactSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(scrub(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: scrub(v) if isinstance(v, str) else v for k, v in record.args.items()}
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, easy for a log collector to read."""

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": scrub(record.getMessage()),
        }
        for key in ("request_id", "method", "path", "status", "duration_ms"):
            if hasattr(record, key):
                out[key] = getattr(record, key)
        if record.exc_info:
            out["exception"] = scrub(self.formatException(record.exc_info))
        return json.dumps(out, ensure_ascii=False)


def configure_logging() -> None:
    """LOG_FORMAT=json for structured lines (default is plain text). LOG_LEVEL defaults to INFO."""
    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler()
    if os.environ.get("LOG_FORMAT", "text").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactSecrets())
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, "_payshield", False):
            root.removeHandler(existing)
    handler._payshield = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)
    # The server's own loggers keep their handlers, so the filter goes on the loggers themselves.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactSecrets) for f in logger.filters):
            logger.addFilter(RedactSecrets())
        if os.environ.get("LOG_FORMAT", "text").lower() == "json":
            for h in logger.handlers:
                h.setFormatter(JsonFormatter())
