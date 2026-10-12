"""Private rotating logs with deterministic secret redaction."""
from __future__ import annotations

from collections.abc import Iterable
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
from typing import TextIO

from trading.operations.paths import UserPaths, ensure_private_directory, ensure_private_file


_HANDLER_MARKER = "autotrader-shadow-private-handler"
_MAX_LOG_BYTES = 10 * 1024 * 1024
_LOG_BACKUP_COUNT = 5
_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bBearer\s+[^\s,;]+"),
    re.compile(r"(?i)\b(api[_-]?key|authorization|secret)(\s*[=:]\s*)[^\s,;]+"),
)


class PrivateRotatingFileHandler(RotatingFileHandler):
    """Keep the active log private after initial open and every rollover."""

    def _open(self) -> TextIO:
        stream = super()._open()
        ensure_private_file(Path(self.baseFilename))
        return stream


class RedactingFormatter(logging.Formatter):
    def __init__(self, *, secrets: Iterable[str] = ()) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
        self._secrets = tuple(
            sorted({value for value in secrets if value}, key=len, reverse=True)
        )

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        for secret in self._secrets:
            rendered = rendered.replace(secret, "[REDACTED]")
        for pattern in _PATTERNS:
            rendered = pattern.sub(self._replace, rendered)
        return rendered

    @staticmethod
    def _replace(match: re.Match[str]) -> str:
        if match.lastindex == 2:
            return f"{match.group(1)}{match.group(2)}[REDACTED]"
        return "[REDACTED]"


def configure_shadow_logging(
    *,
    user_paths: UserPaths | None = None,
    level: str = "INFO",
    secrets: Iterable[str] = (),
) -> logging.Logger:
    paths = user_paths or UserPaths.current()
    ensure_private_directory(paths.log_directory)
    logger = logging.getLogger("autotrader.shadow")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    for existing_handler in tuple(logger.handlers):
        logger.removeHandler(existing_handler)
        existing_handler.close()
    handler = PrivateRotatingFileHandler(
        paths.shadow_log_file,
        maxBytes=_MAX_LOG_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.name = _HANDLER_MARKER
    handler.setFormatter(RedactingFormatter(secrets=secrets))
    logger.addHandler(handler)
    ensure_private_file(paths.shadow_log_file)
    return logger


__all__ = ["RedactingFormatter", "configure_shadow_logging"]
