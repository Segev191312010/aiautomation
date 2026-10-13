"""Nightly signal-outcome tracker: every saved setup, simulated forward (plan Design F).

After the replay, each setup the shadow pipeline saved in
``autotrader_manager_setups`` is simulated on the research store's recorded
bars with the replay's own fill model (``replay.simulate_bracket``): a limit
entry placed when the signal bar closed, filled inside its 10-minute TTL (and
before the strategy's cutoff) or never, then the stop, the target or the
15:45 flatten. The outcome (filled, exit reason, R multiple, costs in R) is
written to ``autotrader_manager_signal_outcomes`` once per (run, setup
fingerprint, fill model):

* idempotent: a rerun, a restart or tomorrow's run writes only what is
  missing; a new fill model id simulates every setup again under that id;
* append-only: a setup whose session the store does not hold yet stays
  pending and is tried again the next night. Nothing is guessed, because a
  written outcome can never be corrected.

The tracker reads the setups and the recorded bars and writes only its own
table. It never touches a decision, an order or an authority, and a
database that is not exactly this code's schema is left alone.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Literal
from urllib.parse import quote

import aiosqlite

from trading.research.bars_store import BarsStore, BarsStoreError, StoredDay
from trading.research.catalogue import entry_cutoff
from trading.research.costs import DEFAULT_COST_MODEL, CostModel
from trading.research.replay import FILL_MODEL_ID, flatten_at, r_multiple, simulate_bracket
from trading.storage.migrations import SCHEMA_READ_TIMEOUT_SECONDS, inspect_schema

logger = logging.getLogger("autotrader.shadow.research")

UTC = timezone.utc
SETUPS_TABLE = "autotrader_manager_setups"
OUTCOMES_TABLE = "autotrader_manager_signal_outcomes"
NOT_FILLED = "not_filled"
# Why the tracker left a database alone (besides the schema gate's tokens).
NO_DATABASE = "no_database"
OUTCOMES_TABLE_ABSENT = "signal_outcomes_table_absent"
SETUPS_TABLE_ABSENT = "setups_table_absent"
_BUSY_TIMEOUT_MS = 10_000
_R_TEXT_QUANTUM = Decimal("0.0001")

_PENDING_SETUPS = f"""
    SELECT s.run_id, s.setup_fingerprint, s.strategy_name, s.strategy_kind, s.symbol, s.con_id,
           s.session_date, s.detected_at, s.hour_bucket, s.entry_price, s.stop_price,
           s.target_price
    FROM {SETUPS_TABLE} s
    LEFT JOIN {OUTCOMES_TABLE} o
      ON o.run_id = s.run_id AND o.setup_fingerprint = s.setup_fingerprint
     AND o.fill_model_id = ?
    WHERE o.id IS NULL
    ORDER BY s.session_date, s.detected_at, s.id
"""
_INSERT_OUTCOME = f"""
    INSERT INTO {OUTCOMES_TABLE}
    (run_id, setup_fingerprint, fill_model_id, simulated_at, filled, exit_reason, r_multiple,
     cost_r, details_json)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(run_id, setup_fingerprint, fill_model_id) DO NOTHING
"""


def _plain(value: Decimal) -> str:
    """Plain decimal text (no exponent) and never a negative zero."""
    text = format(value, "f")
    return text[1:] if text.startswith("-") and Decimal(text) == 0 else text


@dataclass(frozen=True, slots=True)
class SavedSetup:
    """The fields of one ``autotrader_manager_setups`` row the simulation needs."""

    run_id: str
    setup_fingerprint: str
    strategy_name: str
    strategy_kind: str
    symbol: str
    con_id: int
    session_date: date
    detected_at: datetime
    hour_bucket: int
    entry: Decimal
    stop: Decimal
    target: Decimal

    @classmethod
    def from_row(cls, row: Sequence[Any]) -> SavedSetup:
        detected_at = datetime.fromisoformat(str(row[7]))
        if detected_at.tzinfo is None:
            raise ValueError("detected_at must be timezone-aware")
        return cls(
            run_id=str(row[0]),
            setup_fingerprint=str(row[1]),
            strategy_name=str(row[2]),
            strategy_kind=str(row[3]),
            symbol=str(row[4]),
            con_id=int(row[5]),
            session_date=date.fromisoformat(str(row[6])),
            detected_at=detected_at.astimezone(UTC),
            hour_bucket=int(row[8]),
            entry=Decimal(str(row[9])),
            stop=Decimal(str(row[10])),
            target=Decimal(str(row[11])),
        )


@dataclass(frozen=True, slots=True)
class SignalOutcome:
    """One ``autotrader_manager_signal_outcomes`` row."""

    run_id: str
    setup_fingerprint: str
    fill_model_id: str
    filled: bool
    exit_reason: str
    r_multiple: Decimal | None
    cost_r: Decimal
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.filled != (self.r_multiple is not None):
            raise ValueError("a filled outcome carries an R multiple, an unfilled one none")
        if self.filled == (self.exit_reason == NOT_FILLED):
            raise ValueError("exactly the unfilled outcomes exit as not_filled")
        if self.cost_r < 0:
            raise ValueError("costs are never credited")

    @property
    def net_r(self) -> Decimal | None:
        return None if self.r_multiple is None else self.r_multiple - self.cost_r

    def parameters(self, simulated_at: datetime) -> tuple[object, ...]:
        return (
            self.run_id,
            self.setup_fingerprint,
            self.fill_model_id,
            simulated_at.astimezone(UTC).isoformat(),
            int(self.filled),
            self.exit_reason,
            None if self.r_multiple is None else _plain(self.r_multiple),
            _plain(self.cost_r),
            json.dumps(dict(self.details), sort_keys=True, separators=(",", ":")),
        )


@dataclass(frozen=True, slots=True)
class TrackerReport:
    status: Literal["tracked", "skipped"]
    fill_model_id: str
    reason: str | None = None
    considered: int = 0
    written: int = 0
    filled: int = 0
    pending: int = 0
    oldest_pending: date | None = None
    errors: int = 0

    def summary_line(self) -> str:
        if self.status == "skipped":
            return f"signal outcomes: not tracked ({self.reason})"
        line = (
            f"signal outcomes ({self.fill_model_id}): {self.written} new"
            f" ({self.filled} filled) of {self.considered} untracked setups"
        )
        if self.pending:
            line += (
                f"; {self.pending} wait for recorded bars"
                f" (oldest {self.oldest_pending.isoformat() if self.oldest_pending else '?'})"
            )
        if self.errors:
            line += f"; {self.errors} could not be simulated"
        return line

    def to_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "fill_model_id": self.fill_model_id,
            "considered": self.considered,
            "written": self.written,
            "filled": self.filled,
            "pending": self.pending,
            "oldest_pending": None if self.oldest_pending is None else self.oldest_pending.isoformat(),
            "errors": self.errors,
        }


def simulate_setup(
    setup: SavedSetup,
    day: StoredDay,
    *,
    cost_model: CostModel = DEFAULT_COST_MODEL,
    fill_model_id: str = FILL_MODEL_ID,
) -> SignalOutcome:
    """The replay's bracket for ``setup`` on its recorded session."""
    if day.session_date != setup.session_date or day.con_id != setup.con_id:
        raise ValueError("the recorded day is not the setup's session and contract")
    cutoff, cutoff_source = entry_cutoff(setup.strategy_name, setup.session_date)
    flatten = flatten_at(setup.session_date)
    bracket = simulate_bracket(
        entry=setup.entry,
        stop=setup.stop,
        target=setup.target,
        placed_after=setup.detected_at,
        bars=day.completed_bars(),
        entry_cutoff=cutoff,
        flatten=flatten,
    )
    details: dict[str, object] = {
        "strategy_name": setup.strategy_name,
        "strategy_kind": setup.strategy_kind,
        "symbol": setup.symbol,
        "session_date": setup.session_date.isoformat(),
        "hour_bucket": setup.hour_bucket,
        "entry_cutoff": cutoff.isoformat(),
        "cutoff_source": cutoff_source,
        "flatten_at": flatten.isoformat(),
        "cost_model": cost_model.model_id,
    }
    if bracket is None:
        return SignalOutcome(
            run_id=setup.run_id,
            setup_fingerprint=setup.setup_fingerprint,
            fill_model_id=fill_model_id,
            filled=False,
            exit_reason=NOT_FILLED,
            r_multiple=None,
            cost_r=Decimal("0"),
            details=details,
        )
    gross = r_multiple(entry=setup.entry, stop=setup.stop, exit_price=bracket.exit_price)
    cost = cost_model.cost_r(entry=setup.entry, stop=setup.stop, exit_reason=bracket.exit_reason)
    details.update(
        {
            "filled_at": bracket.filled_at.isoformat(),
            "exit_at": bracket.exit_at.isoformat(),
            "exit_price": _plain(bracket.exit_price),
            "bars_held": bracket.bars_held,
            "net_r": _plain((gross - cost).quantize(_R_TEXT_QUANTUM)),
        }
    )
    return SignalOutcome(
        run_id=setup.run_id,
        setup_fingerprint=setup.setup_fingerprint,
        fill_model_id=fill_model_id,
        filled=True,
        exit_reason=bracket.exit_reason,
        r_multiple=gross,
        cost_r=cost,
        details=details,
    )


def _tables(database: Path) -> set[str]:
    uri = f"file:{quote(str(database.resolve()))}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=SCHEMA_READ_TIMEOUT_SECONDS)) as db:
        return {
            str(name)
            for (name,) in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?)",
                (SETUPS_TABLE, OUTCOMES_TABLE),
            )
        }


def tracker_readiness(database: str | Path) -> str | None:
    """``None`` when the tracker may run; otherwise the named reason it may not."""
    status = inspect_schema(database)
    if not status.exists:
        return NO_DATABASE
    if not status.current:
        return status.token or "schema_not_current"
    try:
        tables = _tables(Path(database).expanduser())
    except sqlite3.Error:
        return "schema_unreadable"
    if SETUPS_TABLE not in tables:
        return SETUPS_TABLE_ABSENT
    if OUTCOMES_TABLE not in tables:
        return OUTCOMES_TABLE_ABSENT
    return None


async def track_outcomes(
    *,
    store: BarsStore,
    database: str | Path,
    now: datetime | None = None,
    cost_model: CostModel = DEFAULT_COST_MODEL,
    fill_model_id: str = FILL_MODEL_ID,
) -> TrackerReport:
    """Simulate every saved setup without an outcome under ``fill_model_id``."""
    reason = tracker_readiness(database)
    if reason is not None:
        logger.info("signal outcomes not tracked: %s", reason)
        return TrackerReport(status="skipped", fill_model_id=fill_model_id, reason=reason)
    simulated_at = now or datetime.now(UTC)
    considered = written = filled = pending = errors = 0
    oldest_pending: date | None = None
    stored_sessions = set(store.list_sessions())
    async with aiosqlite.connect(str(Path(database).expanduser())) as db:
        await db.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        await db.execute("PRAGMA foreign_keys=ON")
        async with db.execute(_PENDING_SETUPS, (fill_model_id,)) as cursor:
            rows = await cursor.fetchall()
        outcomes: list[SignalOutcome] = []
        for row in rows:
            considered += 1
            try:
                setup = SavedSetup.from_row(row)
            except (TypeError, ValueError, InvalidOperation) as exc:
                logger.warning("unreadable saved setup %s: %s", row[1], type(exc).__name__)
                errors += 1
                continue
            day: StoredDay | None = None
            if setup.session_date in stored_sessions:
                try:
                    day = store.get_day(setup.session_date, setup.con_id)
                except (BarsStoreError, ValueError) as exc:
                    logger.warning(
                        "unreadable research day %s/%s: %s",
                        setup.session_date,
                        setup.con_id,
                        type(exc).__name__,
                    )
            if day is None:
                pending += 1
                oldest_pending = min(oldest_pending or setup.session_date, setup.session_date)
                continue
            try:
                outcome = simulate_setup(
                    setup, day, cost_model=cost_model, fill_model_id=fill_model_id
                )
            except (ValueError, ArithmeticError) as exc:
                logger.warning(
                    "setup %s %s not simulated: %s", setup.symbol, setup.session_date, exc
                )
                errors += 1
                continue
            outcomes.append(outcome)
        if outcomes:
            await db.execute("BEGIN IMMEDIATE")
            try:
                for outcome in outcomes:
                    cursor = await db.execute(_INSERT_OUTCOME, outcome.parameters(simulated_at))
                    if cursor.rowcount == 1:
                        written += 1
                        filled += int(outcome.filled)
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
    report = TrackerReport(
        status="tracked",
        fill_model_id=fill_model_id,
        considered=considered,
        written=written,
        filled=filled,
        pending=pending,
        oldest_pending=oldest_pending,
        errors=errors,
    )
    logger.info(report.summary_line())
    return report


__all__ = [
    "NOT_FILLED",
    "NO_DATABASE",
    "OUTCOMES_TABLE",
    "OUTCOMES_TABLE_ABSENT",
    "SETUPS_TABLE",
    "SETUPS_TABLE_ABSENT",
    "SavedSetup",
    "SignalOutcome",
    "TrackerReport",
    "simulate_setup",
    "track_outcomes",
    "tracker_readiness",
]
