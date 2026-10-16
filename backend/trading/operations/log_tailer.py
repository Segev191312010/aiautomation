"""Follow a log by byte offset across the daily rotation, and the rotation's one carried-line rule.

Two followers share this module: the notifier (``notify_service``, 07:55-16:15
ET on session days) and the Telegram child's after-hours forwarder
(``overnight_watch``, outside that window). It reads files only: no database,
no network, no clock.

The 18:35 ET rotation (``trading.operations.logging``, T-3) renames
``supervisor.log`` (or copies then truncates a log another process holds),
then writes the newest state lines of the old file, verbatim with their
original times, at the top of the new one, followed by
``<ISO> LOG_ROTATED date=<D> renamed=<n> truncated=<n> pruned=<n> carried=<n>``.
Those carried lines were news when first written: a follower never forwards
them again and never counts them twice. ``rotation_carried`` (one poll's
batch) and ``RotationCarried`` (lines after a marker seen in an earlier poll)
are that one rule; ``without_carried`` applies it to a whole file.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
import logging
import os
from pathlib import Path
import re
import stat
from typing import BinaryIO
import zlib

logger = logging.getLogger("autotrader.shadow.log_tailer")




# Checkpoint for a log that did not exist: whatever appears later is all new.
MISSING_FILE_CHECKPOINT = {"dev": 0, "ino": 0, "offset": 0}
# A file's first bytes are its fingerprint: copied then truncated in place
# (same inode), a log can grow past the read offset before the next poll, so
# a shorter size alone misses it; different first bytes do not.
HEAD_BYTES = 64


class LogTailer:
    """Follow one log by byte offset across rotation, truncation, and restarts.

    Only complete lines are returned. A file that exists at the first start of
    a session date is followed from its end (older lines belong to earlier
    runs); a file that appears later, or that replaced the one being
    followed, is read from its beginning. The 18:35 ET rotation (T-3) either
    renames the log and creates a new one (a new file identity: the old one's
    unread tail is read once, then the new file from its start) or copies it
    and truncates it in place (the same identity, shorter than the offset or
    no longer starting with the bytes it started with: read again from its
    start). The renamed or copied archive is never read.
    """

    def __init__(
        self,
        path: Path,
        *,
        max_read_bytes: int = 1 << 20,
        max_line_bytes: int = 16 << 10,
    ) -> None:
        self.path = path
        self._max_read = max_read_bytes
        self._max_line = max_line_bytes
        self._handle: BinaryIO | None = None
        self._identity: tuple[int, int] | None = None
        self._unreadable: tuple[int, int] | None = None
        self._offset = 0
        self._partial = b""
        self._head = b""

    def start(self, checkpoint: Mapping[str, int] | None = None) -> None:
        """Resume at ``checkpoint`` while it names this same file, else follow from the end."""
        if not self._open():
            return
        assert self._handle is not None
        size = os.fstat(self._handle.fileno()).st_size
        if checkpoint is None:
            offset = size
        elif (
            (checkpoint.get("dev"), checkpoint.get("ino")) == self._identity
            and 0 <= checkpoint.get("offset", -1) <= size
            and self._same_head(checkpoint)
        ):
            offset = checkpoint["offset"]
        else:
            # Rotated or truncated while the notifier was down: all of it is new.
            offset = 0
        self._handle.seek(offset)
        self._offset = offset
        self._remember_head()

    def checkpoint(self) -> dict[str, int]:
        if self._identity is None or self._handle is None:
            return dict(MISSING_FILE_CHECKPOINT)
        return {
            "dev": self._identity[0],
            "ino": self._identity[1],
            "offset": self._offset - len(self._partial),
            "head": zlib.crc32(self._head),
            "head_len": len(self._head),
        }

    def poll(self) -> list[str]:
        lines: list[str] = []
        if self._handle is not None:
            if self._rewritten():
                # Copied and truncated in place, and already longer than the
                # offset again: the same file, new content from its start.
                self._rewind()
            # Drain the file being followed first: after a rotation this is
            # the tail end of the old file.
            lines.extend(self._drain())
        try:
            status = self.path.stat()
        except OSError:
            return lines
        if not stat.S_ISREG(status.st_mode):
            return lines
        identity = (status.st_dev, status.st_ino)
        if identity != self._identity:
            # First appearance or rotation: everything in the new file is new.
            if self._open(identity):
                lines.extend(self._drain())
        elif status.st_size < self._offset:
            self._rewind()
            lines.extend(self._drain())
        return lines

    def _rewind(self) -> None:
        assert self._handle is not None
        self._handle.seek(0)
        self._offset = 0
        self._partial = b""
        self._head = b""

    def _read_head(self, size: int = HEAD_BYTES) -> bytes | None:
        assert self._handle is not None
        try:
            return os.pread(self._handle.fileno(), size, 0)
        except OSError:
            return None

    def _remember_head(self) -> None:
        if self._handle is not None and len(self._head) < HEAD_BYTES:
            head = self._read_head()
            if head is not None and head.startswith(self._head):
                self._head = head

    def _rewritten(self) -> bool:
        """The followed file no longer starts with the bytes it started with."""
        if not self._head:
            return False
        current = self._read_head(len(self._head))
        return current is not None and len(current) == len(self._head) and current != self._head

    def _same_head(self, checkpoint: Mapping[str, int]) -> bool:
        """A checkpoint's fingerprint (absent in older state files) still matches the file."""
        length = checkpoint.get("head_len")
        if length is None or checkpoint.get("head") is None:
            return True
        current = self._read_head(length)
        return current is not None and len(current) == length and zlib.crc32(current) == checkpoint["head"]

    def close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
        self._handle = None
        self._identity = None
        self._offset = 0
        self._partial = b""
        self._head = b""

    def _open(self, expected: tuple[int, int] | None = None) -> bool:
        self.close()
        try:
            handle = self.path.open("rb")
        except FileNotFoundError:
            return False
        except OSError as exc:
            # Retried every poll (it may be fixed), reported once per file.
            if expected is None or expected != self._unreadable:
                logger.warning("cannot read %s: %s", self.path.name, type(exc).__name__)
            self._unreadable = expected
            return False
        status = os.fstat(handle.fileno())
        if not stat.S_ISREG(status.st_mode):
            handle.close()
            return False
        self._handle = handle
        self._identity = (status.st_dev, status.st_ino)
        self._unreadable = None
        return True

    def _drain(self) -> list[str]:
        assert self._handle is not None
        try:
            chunk = self._handle.read(self._max_read)
        except OSError as exc:
            logger.warning("cannot read %s: %s", self.path.name, type(exc).__name__)
            return []
        if not chunk:
            return []
        self._offset += len(chunk)
        self._remember_head()
        *complete, partial = (self._partial + chunk).split(b"\n")
        if len(partial) > self._max_line:
            # A runaway line is emitted rather than buffered without bound.
            complete.append(partial)
            partial = b""
        self._partial = partial
        lines: list[str] = []
        for raw in complete:
            text = raw.decode("utf-8", "replace").rstrip("\r")
            if text.strip():
                lines.append(text)
        return lines


# --------------------------------------------------------------------------
# The rotation's carried lines
# --------------------------------------------------------------------------

LOG_ROTATED = re.compile(r"\bLOG_ROTATED\b(?:.*?\bcarried=(?P<carried>\d+))?")


def parse_iso(value: object) -> datetime | None:
    """A timezone-aware ISO-8601 time, else None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def line_time(line: str) -> datetime | None:
    """The leading ISO time of a ``supervisor.log`` line, else None."""
    return parse_iso(line.strip().partition(" ")[0])


def rotation_carried(lines: Sequence[str]) -> dict[int, tuple[tuple[int, ...], tuple[int, ...]]]:
    """Each ``LOG_ROTATED`` marker's index in ``supervisor.log`` lines, with the
    indices of the lines the rotation carried next to it: up to ``carried`` lines
    on either side, none newer than the marker (T-3 writes them just before it).
    The one rule for the OPS counts and for what the notifier forwards."""
    markers: dict[int, tuple[tuple[int, ...], tuple[int, ...]]] = {}
    for index, line in enumerate(lines):
        rotated = LOG_ROTATED.search(line)
        if rotated is None:
            continue
        at = line_time(line)
        count = int(rotated.group("carried") or 0)
        if at is None:
            markers[index] = ((), ())
            continue
        markers[index] = (
            _dated_by(lines, range(max(index - count, 0), index), at, inclusive=True),
            _dated_by(lines, range(index + 1, min(index + 1 + count, len(lines))), at, inclusive=False),
        )
    return markers


def _dated_by(lines: Sequence[str], indices: range, at: datetime, *, inclusive: bool) -> tuple[int, ...]:
    """The indices whose line is dated before ``at`` (or at it, if ``inclusive``)."""
    kept: list[int] = []
    for index in indices:
        stamp = line_time(lines[index])
        if stamp is not None and (stamp < at or (inclusive and stamp == at)):
            kept.append(index)
    return tuple(kept)


def without_carried(lines: Sequence[str]) -> list[str]:
    """``supervisor.log`` lines less each ``LOG_ROTATED`` marker and the lines the
    rotation carried next to it (``rotation_carried``): their originals are in the
    archive."""
    dropped = {
        index
        for marker, (before, after) in rotation_carried(lines).items()
        for index in (marker, *before, *after)
    }
    return [line for index, line in enumerate(lines) if index not in dropped]


class RotationCarried:
    """A follower's memory of the latest ``LOG_ROTATED`` marker, across polls.

    T-3 writes the carried state lines just before its marker, in one instant,
    so they come in the marker's poll: ``rotation_carried`` drops them there.
    Lines dated before the marker that come after it (in that poll or a later
    one) are carried too, up to the marker's count less those already dropped
    before it. ``fresh`` is the whole rule for one poll's lines;
    ``is_carried`` and ``marker_counted`` are its two steps, for a follower
    that handles each line as it goes (the notifier).
    """

    def __init__(self) -> None:
        self._rotated_at: datetime | None = None
        self._carried_left = 0

    def is_carried(self, line: str, *, stale: Callable[[datetime], bool] | None = None) -> bool:
        """The marker itself, a dated line ``stale`` says is not news (the
        notifier: dated before its session), or a carried copy after a marker."""
        at = line_time(line)
        rotated = LOG_ROTATED.search(line)
        if rotated is not None:
            self._rotated_at = at
            self._carried_left = int(rotated.group("carried") or 0)
            return True
        if at is None:
            return False  # a format drift is still forwarded, never dropped
        if stale is not None and stale(at):
            return True
        if self._carried_left and self._rotated_at is not None and at < self._rotated_at:
            self._carried_left -= 1
            return True
        return False

    def marker_counted(self, before: int) -> None:
        """The marker's count covers the ``before`` lines dropped ahead of it: fewer can follow it."""
        self._carried_left = max(self._carried_left - before, 0)

    def fresh(self, lines: Sequence[str]) -> list[str]:
        """One poll's ``supervisor.log`` lines less the markers and every carried line, in order."""
        markers = rotation_carried(lines)
        before = {index for carried, _after in markers.values() for index in carried}
        kept: list[str] = []
        for index, line in enumerate(lines):
            if index in before:
                continue
            if not self.is_carried(line):
                kept.append(line)
            if index in markers:
                self.marker_counted(len(markers[index][0]))
        return kept


__all__ = [
    "HEAD_BYTES",
    "LOG_ROTATED",
    "LogTailer",
    "MISSING_FILE_CHECKPOINT",
    "RotationCarried",
    "line_time",
    "parse_iso",
    "rotation_carried",
    "without_carried",
]
