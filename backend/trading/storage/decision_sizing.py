"""Every decision's effective size multiplier, kept beside the database (T-8).

``size_multiplier_effective = identity factor x managed factor`` is the factor
the risk engine applied to the policy quantity for one decision: the
identity's size for the session (``trading.risk.identity_sizing``: read once
at the session's start from the size store, else the reviewed baseline; 1 for
a shadow name) times the AI day manager's narrowing for that setup (1 when the
pipeline is unmanaged, when the review did not PROPOSE, or when no setup
formed). A sendable identity with no size for the session (no baseline row, no
ceiling, an unreadable size store) has no factor (``None``): its decisions are
blocked with ``identity_sizing_missing``.

Version 2 (P3-F, owner decision D3) adds the decision's realism, read from the
same inputs the pipeline assessed (``CandidateInput.bars`` and ``.quote``):
the trade intent's ``quantity``, the ``bar_volume`` of the newest completed
bar, ``size_pct_of_bar_volume`` (quantity / bar volume x 100) and the quote's
``spread_bps``, with two flags at pinned thresholds (open item O4):
``size_above_bar_volume_pct`` (above ``SIZE_ABOVE_BAR_VOLUME_PCT`` = 1 %) and
``spread_above_bps`` (above ``SPREAD_ABOVE_BPS`` = 50 bps). A field the inputs
do not give (no intent, no bar, no quote) is ``None``, never guessed. Readers
accept version 1 lines (no realism fields) and version 2 lines.

Why a sidecar and not a field of the decision: a decision's stored payload is
fingerprinted content. The golden decisions (``tests/test_setup_persistence.py``)
digest the whole payload, and recorded days are compared with their replays;
one more key, even a null one, moves every golden digest and makes every
recorded day differ from its replay. A new table is a schema migration, which
the operator contract stops on (a deploy that migrates cannot be rolled back
with ``git reset`` alone). So the pipeline appends one JSON line per decision,
after the decision is recorded, to ``<database stem>-decision-sizing/<run>.jsonl``
next to the database whose decisions it annotates, keyed by decision id.

The record is evidence only. Nothing reads it back into a decision, an order
or an authority, and no gate reads a realism flag; a failed write is logged by
the pipeline and never blocks or alters a decision. Decisions recorded before
the sidecar existed derive their multiplier from their run's ``source_sha``, or,
for a run at or after the size store's install, from the store's events
(``trading.research.size_history``).
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from functools import partial
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import threading
from typing import Any, TypeVar

from trading.contracts import CompletedBar, Decision, QuoteSnapshot
from trading.operations.paths import FILE_MODE, ensure_private_directory

SIZING_SCHEMA = "autotrader-v1/decision-sizing"
# Version 2 (P3-F) adds the realism fields; a reader accepts every version
# listed here, and a line of any other version is not evidence.
SIZING_VERSION = 2
SIZING_READ_VERSIONS = frozenset({1, 2})
# Realism flags (owner decision D3, defaults O4 of the P2/P3 plan), pinned by a
# test: evidence for the nightly reports, never a gate.
SIZE_ABOVE_BAR_VOLUME_PCT = Decimal("1")  # the order is more than 1 % of its bar's volume
SPREAD_ABOVE_BPS = Decimal("50")  # the quote's spread is wider than 50 basis points
_PCT_QUANTUM = Decimal("0.0001")
_BPS_QUANTUM = Decimal("0.01")
_REALISM_FIELDS = ("quantity", "bar_volume", "spread_bps")
# Derived keys a v2 line must agree with, recomputed from its own fields.
_DERIVED_V2 = ("size_pct_of_bar_volume", "size_above_bar_volume_pct", "spread_above_bps")
SIZING_DIRECTORY_SUFFIX = "-decision-sizing"
SIZING_FILE_SUFFIX = ".jsonl"
# A run's file holds one short line per decision; anything larger is not ours.
MAX_SIZING_FILE_BYTES = 8 * 1024 * 1024
# The pipeline waits at most this long for one sidecar line (N15a MEDIUM-1). The
# median append is well under a millisecond, so a write still running after two
# seconds is a fault (a FIFO or a hung volume at the path), never load: the
# pipeline then turns the sidecar off for the run, and decisions go on unchanged.
SIZING_RECORD_DEADLINE_S = 2.0
# Logged once, after the first timeout or OS error, when the pipeline stops writing.
SIZING_SIDECAR_DISABLED = "sizing_sidecar_disabled"
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_ZERO = Decimal("0")
_ONE = Decimal("1")
_T = TypeVar("_T")


class SizingFileRefused(OSError):
    """The run's sidecar path is not a regular file (a FIFO, a device, a directory or
    a symlink): never opened, never written. An ``OSError``, so the pipeline turns the
    sidecar off for the run instead of trying it again for every decision."""


def _plain(value: Decimal) -> str:
    """Decimal text without an exponent or trailing zeros: 0.25, 0.5, 1, 0."""
    text = format(value.normalize(), "f")
    return "0" if Decimal(text) == 0 else text


def _factor(value: object, name: str, *, optional: bool = False) -> Decimal | None:
    if value is None and optional:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} is not a decimal") from exc
    if not result.is_finite() or not _ZERO <= result <= _ONE:
        raise ValueError(f"{name} must be a finite factor in [0, 1]")
    return result


def _measure(value: object, name: str) -> Decimal | None:
    """A non-negative finite Decimal (a volume, a spread), or None when not recorded."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} is not a decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} is not a decimal") from exc
    if not result.is_finite() or result < _ZERO:
        raise ValueError(f"{name} must be a finite decimal >= 0")
    return result


def _quantity(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("quantity must be a positive whole number of shares")
    return value


@dataclass(frozen=True, slots=True)
class DecisionSizing:
    """One decision's size factors, exactly as the pipeline applied them, and its realism.

    ``quantity`` (the trade intent's shares), ``bar_volume`` (the newest
    completed bar's volume, in shares) and ``spread_bps`` (the quote's spread,
    in basis points of its midpoint) are version 2 fields; a version 1 record
    has none of them.
    """

    decision_id: str
    run_id: str
    candidate_id: str
    strategy_id: str
    strategy_version: int
    strategy_name: str
    sendable: bool
    outcome: str
    identity_factor: Decimal | None
    managed_factor: Decimal
    decided_at: datetime
    quantity: int | None = None
    bar_volume: Decimal | None = None
    spread_bps: Decimal | None = None
    version: int = SIZING_VERSION

    def __post_init__(self) -> None:
        for name in ("decision_id", "run_id", "candidate_id", "strategy_id", "strategy_name", "outcome"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.identity_factor is not None:
            _factor(self.identity_factor, "identity_factor")
        _factor(self.managed_factor, "managed_factor")
        if self.decided_at.tzinfo is None or self.decided_at.utcoffset() is None:
            raise ValueError("decided_at must be timezone-aware")
        if type(self.version) is not int or self.version not in SIZING_READ_VERSIONS:
            raise ValueError(f"decision-sizing version {self.version!r} is not one this code reads")
        if self.version < 2 and any(getattr(self, name) is not None for name in _REALISM_FIELDS):
            raise ValueError("a version 1 record has no realism fields")
        _quantity(self.quantity)
        _measure(self.bar_volume, "bar_volume")
        _measure(self.spread_bps, "spread_bps")

    @property
    def size_multiplier_effective(self) -> Decimal | None:
        """identity x managed; ``None`` for a sendable identity with no sizing row."""
        if self.identity_factor is None:
            return None
        return self.identity_factor * self.managed_factor

    @property
    def size_pct_of_bar_volume(self) -> Decimal | None:
        """The order's shares as a percentage of its bar's volume (shares / volume x 100).

        ``None`` without an intent, without a bar, or on a bar with no volume.
        """
        if self.quantity is None or self.bar_volume is None or self.bar_volume == _ZERO:
            return None
        return (Decimal(self.quantity) * 100 / self.bar_volume).quantize(_PCT_QUANTUM, rounding=ROUND_HALF_EVEN)

    @property
    def size_above_bar_volume_pct(self) -> bool | None:
        """More than ``SIZE_ABOVE_BAR_VOLUME_PCT`` of the bar's volume; any order is, on a bar with
        no volume at all. ``None`` without an intent or a bar."""
        if self.quantity is None or self.bar_volume is None:
            return None
        share = self.size_pct_of_bar_volume
        return True if share is None else share > SIZE_ABOVE_BAR_VOLUME_PCT

    @property
    def spread_above_bps(self) -> bool | None:
        """A spread wider than ``SPREAD_ABOVE_BPS``; ``None`` without a quote."""
        if self.spread_bps is None:
            return None
        return self.spread_bps > SPREAD_ABOVE_BPS

    def payload(self) -> dict[str, object]:
        effective = self.size_multiplier_effective
        record: dict[str, object] = {
            "schema": SIZING_SCHEMA,
            "version": self.version,
            "decision_id": self.decision_id,
            "run_id": self.run_id,
            "candidate_id": self.candidate_id,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_name": self.strategy_name,
            "sendable": self.sendable,
            "outcome": self.outcome,
            "identity_factor": None if self.identity_factor is None else _plain(self.identity_factor),
            "managed_factor": _plain(self.managed_factor),
            "size_multiplier_effective": None if effective is None else _plain(effective),
            "decided_at": self.decided_at.isoformat(),
        }
        if self.version >= 2:
            share = self.size_pct_of_bar_volume
            record |= {
                "quantity": self.quantity,
                "bar_volume": None if self.bar_volume is None else _plain(self.bar_volume),
                "size_pct_of_bar_volume": None if share is None else _plain(share),
                "size_above_bar_volume_pct": self.size_above_bar_volume_pct,
                "spread_bps": None if self.spread_bps is None else _plain(self.spread_bps),
                "spread_above_bps": self.spread_above_bps,
            }
        return record

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> DecisionSizing:
        """The record a line holds; a line that is not exactly one raises ``ValueError``.

        Version 1 lines (factors only) and version 2 lines (factors and realism)
        are read; any other version, or a bool or float where the version should
        be, is not evidence.
        """
        line_version = payload.get("version")
        if (
            payload.get("schema") != SIZING_SCHEMA
            or type(line_version) is not int
            or line_version not in SIZING_READ_VERSIONS
        ):
            raise ValueError("not a decision-sizing record of a version this code reads")
        version = payload.get("strategy_version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("strategy_version must be a positive integer")
        sendable = payload.get("sendable")
        if not isinstance(sendable, bool):
            raise ValueError("sendable must be a boolean")
        try:
            decided_at = datetime.fromisoformat(str(payload.get("decided_at")))
        except ValueError as exc:
            raise ValueError("decided_at is not a timestamp") from exc
        record = cls(
            decision_id=str(payload.get("decision_id") or ""),
            run_id=str(payload.get("run_id") or ""),
            candidate_id=str(payload.get("candidate_id") or ""),
            strategy_id=str(payload.get("strategy_id") or ""),
            strategy_version=version,
            strategy_name=str(payload.get("strategy_name") or ""),
            sendable=sendable,
            outcome=str(payload.get("outcome") or ""),
            identity_factor=_factor(payload.get("identity_factor"), "identity_factor", optional=True),
            managed_factor=_factor(payload.get("managed_factor"), "managed_factor"),  # type: ignore[arg-type]
            decided_at=decided_at,
            version=line_version,
            **(
                {
                    "quantity": _quantity(payload.get("quantity")),
                    "bar_volume": _measure(payload.get("bar_volume"), "bar_volume"),
                    "spread_bps": _measure(payload.get("spread_bps"), "spread_bps"),
                }
                if line_version >= 2
                else {}
            ),
        )
        # The stored product must be the product of the stored factors, and a v2
        # line's share of volume and flags what its own fields give: a line that
        # disagrees with itself is not evidence.
        mine = record.payload()
        if mine["size_multiplier_effective"] != payload.get("size_multiplier_effective"):
            raise ValueError("size_multiplier_effective is not identity x managed")
        for key in _DERIVED_V2 if line_version >= 2 else ():
            if mine[key] != payload.get(key) or type(mine[key]) is not type(payload.get(key)):
                raise ValueError(f"{key} is not what the record's own fields give")
        return record


def decision_sizing(
    decision: Decision,
    *,
    strategy_name: str,
    sendable: bool,
    identity_factor: Decimal | None,
    managed_factor: Decimal,
    bars: Sequence[CompletedBar] = (),
    quote: QuoteSnapshot | None = None,
) -> DecisionSizing:
    """The sidecar record of ``decision``: its factors and the realism of its inputs.

    ``bars`` and ``quote`` are the inputs the pipeline assessed
    (``CandidateInput.bars`` and ``.quote``); the decision itself is only read.
    """
    intent = decision.risk.intent if decision.risk is not None else None
    newest = max(bars, key=lambda bar: bar.interval_end_utc) if bars else None
    return DecisionSizing(
        decision_id=decision.id,
        run_id=decision.run_id,
        candidate_id=decision.candidate_id,
        strategy_id=decision.strategy_id,
        strategy_version=decision.strategy_version,
        strategy_name=strategy_name,
        sendable=sendable,
        outcome=decision.outcome.value,
        identity_factor=identity_factor,
        managed_factor=managed_factor,
        decided_at=decision.created_at,
        quantity=None if intent is None else intent.quantity,
        bar_volume=None if newest is None else newest.volume,
        # One implementation of the spread: the quote's own (ask - bid) / midpoint x 10000.
        spread_bps=None if quote is None else quote.spread_bps.quantize(_BPS_QUANTUM, rounding=ROUND_HALF_EVEN),
    )


# --- where the sidecar lives -------------------------------------------------------


def sizing_directory(database: str | Path) -> Path:
    """``<stem>-decision-sizing`` beside the database: a copy of the database for a
    rehearsal takes its sizing with it, and a test database gets its own."""
    path = Path(database).expanduser()
    return path.with_name(f"{path.stem}{SIZING_DIRECTORY_SUFFIX}")


def sizing_file(directory: Path, run_id: str) -> Path:
    """One file per run (one writer: the run's lease holder)."""
    stem = run_id if _SAFE_RUN_ID.fullmatch(run_id) else f"run-{sha256(run_id.encode('utf-8')).hexdigest()[:32]}"
    return directory / f"{stem}{SIZING_FILE_SUFFIX}"


def append_decision_sizing(directory: Path, row: DecisionSizing) -> Path:
    """Append ``row`` as one line, durably, to its run's private file.

    Only a regular file is ever opened, and never through a symlink: an existing
    path is checked with ``lstat`` before the open (opening a FIFO for writing
    blocks until a reader appears, N15a MEDIUM-1), the open itself does not block
    (``O_NONBLOCK``) or follow a link (``O_NOFOLLOW``), and the descriptor is
    checked again (the path could change between the two).
    """
    ensure_private_directory(directory)
    target = sizing_file(directory, row.run_id)
    line = (json.dumps(row.payload(), sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        existing = os.lstat(target)
    except FileNotFoundError:
        existing = None
    if existing is not None and not stat.S_ISREG(existing.st_mode):
        raise SizingFileRefused(f"{target.name} is not a regular file")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, flags, FILE_MODE)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise SizingFileRefused(f"{target.name} is not a regular file")
        os.fchmod(descriptor, FILE_MODE)
        written = 0
        while written < len(line):
            written += os.write(descriptor, line[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return target


async def _on_own_thread(function: Callable[..., _T], *args: object) -> _T:
    """``function(*args)`` on a daemon thread of its own.

    A write that never returns (a hung volume) then parks only that thread, and
    the process can still exit: ``asyncio.to_thread``'s default executor is
    joined by ``asyncio.run`` at exit, which would wait for it until SIGKILL.
    The pipeline's deadline cancels the wait; the result is then dropped.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[_T] = loop.create_future()

    def settle(outcome: Callable[[], None]) -> None:
        if not future.done():
            outcome()

    def work() -> None:
        try:
            result = function(*args)
        except Exception as exc:  # noqa: BLE001 - handed to the waiting task
            deliver = partial(settle, partial(future.set_exception, exc))
        else:
            deliver = partial(settle, partial(future.set_result, result))
        try:
            loop.call_soon_threadsafe(deliver)
        except RuntimeError:
            pass  # the loop has closed: nobody waits for this line any more

    threading.Thread(target=work, name="decision-sizing", daemon=True).start()
    return await future


async def record_decision_sizing(row: DecisionSizing) -> Path | None:
    """The pipeline's default recorder: beside the configured database.

    An in-memory database has no directory, so nothing is written.
    """
    from config import cfg

    if cfg.DB_PATH == ":memory:":
        return None
    return await _on_own_thread(append_decision_sizing, sizing_directory(cfg.DB_PATH), row)


# --- reading it back (research, read-only) -----------------------------------------


@dataclass(frozen=True, slots=True)
class SizingIndex:
    """Every readable record by decision id, and what could not be trusted."""

    records: Mapping[str, DecisionSizing] = field(default_factory=dict)
    # "<file>" or "<file>:<line>" (or "<decision id>" for a conflicting duplicate).
    unreadable: tuple[str, ...] = ()

    def by_run(self) -> dict[str, tuple[DecisionSizing, ...]]:
        grouped: dict[str, list[DecisionSizing]] = {}
        for record in self.records.values():
            grouped.setdefault(record.run_id, []).append(record)
        return {run_id: tuple(items) for run_id, items in grouped.items()}


def read_decision_sizing(directory: Path) -> SizingIndex:
    """Every ``*.jsonl`` record under ``directory``; a missing directory is an empty index.

    Read-only. A line that is not exactly one record, a file that is not a
    regular file or is too large, and two different records for one decision
    are named in ``unreadable`` and left out, never guessed.
    """
    if directory.is_symlink() or not directory.is_dir():
        return SizingIndex()
    records: dict[str, DecisionSizing] = {}
    conflicts: set[str] = set()
    unreadable: list[str] = []
    for path in sorted(directory.iterdir()):
        if path.suffix != SIZING_FILE_SUFFIX:
            continue
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError("not a regular file")
            with path.open("rb") as stream:
                raw = stream.read(MAX_SIZING_FILE_BYTES + 1)
            if len(raw) > MAX_SIZING_FILE_BYTES:
                raise ValueError("larger than a run's sizing file")
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            unreadable.append(path.name)
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError("not an object")
                record = DecisionSizing.from_payload(payload)
            except (TypeError, ValueError):
                unreadable.append(f"{path.name}:{number}")
                continue
            known = records.get(record.decision_id)
            if known is not None and known != record:
                conflicts.add(record.decision_id)
            records.setdefault(record.decision_id, record)
    for decision_id in sorted(conflicts):
        records.pop(decision_id, None)
        unreadable.append(decision_id)
    return SizingIndex(records=records, unreadable=tuple(unreadable))


__all__ = [
    "MAX_SIZING_FILE_BYTES",
    "SIZE_ABOVE_BAR_VOLUME_PCT",
    "SIZING_DIRECTORY_SUFFIX",
    "SIZING_READ_VERSIONS",
    "SIZING_RECORD_DEADLINE_S",
    "SIZING_SCHEMA",
    "SIZING_SIDECAR_DISABLED",
    "SIZING_VERSION",
    "SPREAD_ABOVE_BPS",
    "DecisionSizing",
    "SizingFileRefused",
    "SizingIndex",
    "append_decision_sizing",
    "decision_sizing",
    "read_decision_sizing",
    "record_decision_sizing",
    "sizing_directory",
    "sizing_file",
]
