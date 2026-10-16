"""Every decision's effective size multiplier, kept beside the database (T-8).

``size_multiplier_effective = identity factor x managed factor`` is the factor
the risk engine applied to the policy quantity for one decision: the
identity's fixed factor (``trading.risk.identity_sizing``; 1 for a shadow
name) times the AI day manager's narrowing for that setup (1 when the
pipeline is unmanaged, when the review did not PROPOSE, or when no setup
formed). A sendable identity without a sizing row has no factor (``None``):
its decisions are blocked with ``identity_sizing_missing``.

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
or an authority; a failed write is logged by the pipeline and never blocks or
alters a decision. Decisions recorded before the sidecar existed derive their
multiplier from their run's ``source_sha`` (``trading.research.size_history``).
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from trading.contracts import Decision
from trading.operations.paths import FILE_MODE, PathSecurityError, ensure_private_directory

SIZING_SCHEMA = "autotrader-v1/decision-sizing"
SIZING_VERSION = 1
SIZING_DIRECTORY_SUFFIX = "-decision-sizing"
SIZING_FILE_SUFFIX = ".jsonl"
# A run's file holds one short line per decision; anything larger is not ours.
MAX_SIZING_FILE_BYTES = 8 * 1024 * 1024
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_ZERO = Decimal("0")
_ONE = Decimal("1")


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


@dataclass(frozen=True, slots=True)
class DecisionSizing:
    """One decision's size factors, exactly as the pipeline applied them."""

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

    @property
    def size_multiplier_effective(self) -> Decimal | None:
        """identity x managed; ``None`` for a sendable identity with no sizing row."""
        if self.identity_factor is None:
            return None
        return self.identity_factor * self.managed_factor

    def payload(self) -> dict[str, object]:
        effective = self.size_multiplier_effective
        return {
            "schema": SIZING_SCHEMA,
            "version": SIZING_VERSION,
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

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> DecisionSizing:
        """The record a line holds; a line that is not exactly one raises ``ValueError``."""
        if payload.get("schema") != SIZING_SCHEMA or payload.get("version") != SIZING_VERSION:
            raise ValueError("not a decision-sizing record of this version")
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
        )
        # The stored product must be the product of the stored factors: a line
        # that disagrees with itself is not evidence.
        if record.payload()["size_multiplier_effective"] != payload.get("size_multiplier_effective"):
            raise ValueError("size_multiplier_effective is not identity x managed")
        return record


def decision_sizing(
    decision: Decision,
    *,
    strategy_name: str,
    sendable: bool,
    identity_factor: Decimal | None,
    managed_factor: Decimal,
) -> DecisionSizing:
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
    """Append ``row`` as one line, durably, to its run's private file (never through a symlink)."""
    ensure_private_directory(directory)
    target = sizing_file(directory, row.run_id)
    line = (json.dumps(row.payload(), sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, flags, FILE_MODE)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PathSecurityError("a decision-sizing file must be a regular file")
        os.fchmod(descriptor, FILE_MODE)
        written = 0
        while written < len(line):
            written += os.write(descriptor, line[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return target


async def record_decision_sizing(row: DecisionSizing) -> Path | None:
    """The pipeline's default recorder: beside the configured database.

    An in-memory database has no directory, so nothing is written.
    """
    from config import cfg

    if cfg.DB_PATH == ":memory:":
        return None
    return await asyncio.to_thread(append_decision_sizing, sizing_directory(cfg.DB_PATH), row)


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
    "SIZING_DIRECTORY_SUFFIX",
    "SIZING_SCHEMA",
    "SIZING_VERSION",
    "DecisionSizing",
    "SizingIndex",
    "append_decision_sizing",
    "decision_sizing",
    "read_decision_sizing",
    "record_decision_sizing",
    "sizing_directory",
    "sizing_file",
]
