"""
Structured JSON logging for Docker / production.

Usage
-----
    from log_config import configure_logging
    configure_logging()          # call once at process start

All log records will be emitted as single-line JSON objects to stdout,
making them trivially parseable by docker logs, Loki, Datadog, etc.

The log level is read from cfg.LOG_LEVEL (defaults to "INFO").
An optional file handler is added when cfg.LOG_FILE is non-empty.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any


class _JsonFormatter(logging.Formatter):
    """Emit each LogRecord as a single-line JSON object."""

    # Fields we always want in the root of the JSON envelope
    _ALWAYS = ("level", "logger", "message", "timestamp")

    def format(self, record: logging.LogRecord) -> str:
        record.getMessage()          # populates record.message for Python < 3.12
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level":     record.levelname,
            "logger":    record.name,
            "message":   record.getMessage(),
        }

        # Attach exception info when present
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        # Carry any extra keyword-arguments passed to the logger call
        standard = logging.LogRecord.__init__.__code__.co_varnames
        for key, value in record.__dict__.items():
            if key not in standard and not key.startswith("_"):
                try:
                    json.dumps(value)       # skip non-serialisable extras
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = str(value)

        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str | None = None) -> None:
    """
    Replace the root logger's handlers with a JSON-to-stdout handler.

    Safe to call multiple times — subsequent calls are no-ops.
    """
    root = logging.getLogger()

    # Avoid double-initialisation (e.g. during pytest collection)
    if any(isinstance(h, logging.StreamHandler) and
           isinstance(h.formatter, _JsonFormatter)
           for h in root.handlers):
        return

    # Resolve level: explicit arg > env var LOG_LEVEL > INFO
    _level_str = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    _level = getattr(logging, _level_str, logging.INFO)

    root.handlers.clear()
    root.setLevel(_level)

    # --- stdout handler (always present) ---
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(_JsonFormatter())
    stdout_handler.setLevel(_level)
    root.addHandler(stdout_handler)

    # --- optional file handler ---
    log_file = os.getenv("LOG_FILE", "")
    if log_file:
        try:
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(_JsonFormatter())
            file_handler.setLevel(_level)
            root.addHandler(file_handler)
        except OSError as exc:
            root.warning("Could not open LOG_FILE=%r: %s", log_file, exc)

    # Quieten noisy third-party loggers so they don't drown out app logs
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("ib_insync").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
