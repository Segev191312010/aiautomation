"""Private rotating logs with deterministic secret redaction, and the daily
rotation of the supervisor's logs.

``shadow.log`` rotates by size through its own handler. The supervisor's
``supervisor.log``, every ``<child>.log`` it opens and launchd's two files
rotate once a day at 18:35 ET (after the replay window) into
``<name>.log.YYYY-MM-DD`` and are kept 14 days. Two ways, by who holds the file:

* nobody (or the supervisor itself, which reopens its handle): the file is
  renamed to its archive and a new empty private file is created at the old
  path (a new inode);
* another process (launchd's ``launchd.stdout.log``/``launchd.stderr.log``, a
  running always-on child's ``<name>.log``): the content is copied to the
  archive and the file is truncated in place (same inode, size 0), because a
  rename would leave that process writing into the archive. Every holder opened
  it with O_APPEND, so its next write lands at the new end.

A reader that follows one of these files detects a rotation by an inode change
or by the size shrinking below its offset, and starts over at offset 0.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import stat
from typing import TextIO
from zoneinfo import ZoneInfo

from trading.operations.paths import (
    FILE_MODE,
    PathSecurityError,
    UserPaths,
    ensure_private_directory,
    ensure_private_file,
)


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


# --- daily rotation (T-6) -----------------------------------------------------------

ROTATION_TIMEZONE = ZoneInfo("America/New_York")
# After the replay window (17:35-18:30 ET), when no session child runs; every
# calendar day, sessions or not.
ROTATION_TIME = time(18, 35)
# An archive dated D is deleted by the rotation dated D + ROTATION_KEEP_DAYS:
# the 14 newest days stay.
ROTATION_KEEP_DAYS = 14
_ARCHIVE_DATE = re.compile(r"^(?P<day>\d{4}-\d{2}-\d{2})$")
_COPY_CHUNK = 1 << 20


def rotation_boundary(day: date) -> datetime:
    """``day``'s rotation moment, 18:35 in New York (its own UTC offset on a DST day)."""
    return datetime.combine(day, ROTATION_TIME, tzinfo=ROTATION_TIMEZONE)


def latest_rotation_boundary(now: datetime) -> datetime:
    """The newest rotation moment at or before ``now``."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("rotation times must be timezone-aware")
    local_day = now.astimezone(ROTATION_TIMEZONE).date()
    boundary = rotation_boundary(local_day)
    return boundary if boundary <= now else rotation_boundary(local_day - timedelta(days=1))


def next_rotation_boundary(now: datetime) -> datetime:
    """The first rotation moment strictly after ``now``."""
    return rotation_boundary(latest_rotation_boundary(now).date() + timedelta(days=1))


def archive_path(path: Path, day: date) -> Path:
    """``<name>.log`` -> ``<name>.log.YYYY-MM-DD``."""
    return path.with_name(f"{path.name}.{day.isoformat()}")


def _regular_size(path: Path) -> int | None:
    """The size of a regular file; None when it is missing; a symlink or other type refused."""
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(status.st_mode):
        raise PathSecurityError("rotated logs must be regular files, never symbolic links")
    return status.st_size


def _open_private(path: Path, flags: int) -> int:
    return os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC, FILE_MODE)


def _append_copy(source: Path, target: Path) -> None:
    """Append ``source``'s bytes to the private ``target`` and fsync them."""
    with open(_open_private(source, os.O_RDONLY), "rb", closefd=True) as reader:
        with open(
            _open_private(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND), "ab", closefd=True
        ) as writer:
            os.fchmod(writer.fileno(), FILE_MODE)
            shutil.copyfileobj(reader, writer, _COPY_CHUNK)
            writer.flush()
            os.fsync(writer.fileno())


def _truncate_in_place(path: Path) -> None:
    descriptor = _open_private(path, os.O_WRONLY)
    try:
        os.ftruncate(descriptor, 0)
    finally:
        os.close(descriptor)


def create_empty_private(path: Path) -> None:
    """The new file at a rotated path: private (0600), never through a symlink."""
    os.close(_open_private(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND))
    ensure_private_file(path)


def rotate_by_rename(path: Path, day: date, *, keep_empty: bool = False) -> bool:
    """Rename ``path`` to its archive for ``day`` and create a new empty file in its place.

    For a file no other process holds open. An empty or missing file is left
    alone unless ``keep_empty``. An archive that already exists (a rotation
    repeated for the same day after a crash) gets the content appended, and
    the file is truncated. True when something was rotated.
    """
    size = _regular_size(path)
    if size is None or (size == 0 and not keep_empty):
        return False
    target = archive_path(path, day)
    if _regular_size(target) is not None:
        _append_copy(path, target)
        _truncate_in_place(path)
        return True
    os.rename(path, target)
    create_empty_private(path)
    return True


def rotate_by_copy_truncate(path: Path, day: date) -> bool:
    """Copy ``path`` into its archive for ``day``, then truncate it in place.

    For a file another process holds open with O_APPEND (launchd's own files,
    a running child's log): renaming it would leave that process writing into
    the archive. Lines written between the copy and the truncation are lost;
    the supervisor writes nothing while it rotates. True when something was rotated.
    """
    size = _regular_size(path)
    if not size:
        return False
    _append_copy(path, archive_path(path, day))
    _truncate_in_place(path)
    return True


def prune_archives(path: Path, day: date, *, keep_days: int = ROTATION_KEEP_DAYS) -> list[Path]:
    """Delete ``path``'s archives dated ``day - keep_days`` or earlier; the deleted paths."""
    if keep_days < 1:
        raise ValueError("at least one day of archives is kept")
    oldest_kept = day - timedelta(days=keep_days - 1)
    prefix = f"{path.name}."
    removed: list[Path] = []
    try:
        candidates = sorted(path.parent.iterdir())
    except FileNotFoundError:
        return removed
    for candidate in candidates:
        if not candidate.name.startswith(prefix):
            continue
        match = _ARCHIVE_DATE.match(candidate.name[len(prefix):])
        if match is None:
            continue
        try:
            dated = date.fromisoformat(match.group("day"))
        except ValueError:
            continue
        if dated < oldest_kept and not candidate.is_dir():
            candidate.unlink()
            removed.append(candidate)
    return removed


__all__ = [
    "ROTATION_KEEP_DAYS",
    "ROTATION_TIME",
    "ROTATION_TIMEZONE",
    "RedactingFormatter",
    "archive_path",
    "configure_shadow_logging",
    "create_empty_private",
    "latest_rotation_boundary",
    "next_rotation_boundary",
    "prune_archives",
    "rotate_by_copy_truncate",
    "rotate_by_rename",
    "rotation_boundary",
]
