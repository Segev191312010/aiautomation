"""Every setup the rules form, kept as evidence (plan Design F).

The shadow pipeline records a setup here after the setup's decision is
recorded, whatever that decision was and whether or not Claude was asked.
Nothing here feeds back into a decision: the row is evidence for the nightly
tracker and the AI scorecard, written once per (run, setup fingerprint).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from typing import Any

from db.core import get_db
from trading.contracts import Candidate, CompletedBar, RuleSetup, StrategyRecord
from trading.scheduler.sessions import MARKET_TIMEZONE

UTC = timezone.utc
# Every completed bar is five minutes long (CompletedBar validates it).
BAR_INTERVAL = timedelta(minutes=5)
# Evidence never holds the decision path up for long: a locked database
# fails this write after two seconds (the decision is already recorded).
SETUP_WRITE_BUSY_TIMEOUT_MS = 2000

_INSERT = """
    INSERT INTO autotrader_manager_setups
    (run_id, setup_fingerprint, candidate_id, strategy_id, strategy_version, strategy_name,
     strategy_kind, spec_fingerprint, symbol, con_id, session_date, bar_ts, detected_at,
     hour_bucket, entry_price, stop_price, target_price, atr, levels_json, setup_json,
     created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(run_id, setup_fingerprint) DO NOTHING
"""


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _plain(value: Decimal) -> str:
    return format(value, "f")


@dataclass(frozen=True, slots=True)
class FormedSetup:
    """One ``autotrader_manager_setups`` row."""

    run_id: str
    setup_fingerprint: str
    candidate_id: str
    strategy_id: str
    strategy_version: int
    strategy_name: str
    strategy_kind: str
    spec_fingerprint: str
    symbol: str
    con_id: int
    session_date: date
    bar_ts: datetime
    detected_at: datetime
    hour_bucket: int
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    atr: Decimal
    # Exactly what the reviewer receives: the setup's JSON dump and its levels.
    setup_payload: Mapping[str, Any] = field(repr=False)
    levels: Mapping[str, Any] = field(repr=False)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        for name in ("bar_ts", "detected_at", "created_at"):
            _aware(getattr(self, name), name)
        if not 0 <= self.hour_bucket <= 23:
            raise ValueError("hour_bucket must be an hour of the day")

    def parameters(self) -> tuple[object, ...]:
        return (
            self.run_id,
            self.setup_fingerprint,
            self.candidate_id,
            self.strategy_id,
            self.strategy_version,
            self.strategy_name,
            self.strategy_kind,
            self.spec_fingerprint,
            self.symbol,
            self.con_id,
            self.session_date.isoformat(),
            _utc_text(self.bar_ts),
            _utc_text(self.detected_at),
            self.hour_bucket,
            _plain(self.entry_price),
            _plain(self.stop_price),
            _plain(self.target_price),
            _plain(self.atr),
            json.dumps(dict(self.levels), sort_keys=True, separators=(",", ":")),
            json.dumps(dict(self.setup_payload), sort_keys=True, separators=(",", ":")),
            _utc_text(self.created_at),
        )


def signal_bar_start(setup: RuleSetup, bars: Sequence[CompletedBar]) -> datetime:
    """When the bar that formed the setup began.

    The bar the setup's causal provenance names; without one (or if it is
    not among ``bars``), the bar that closed at ``detected_at``: every
    strategy dates its setup by its signal bar's close.
    """
    provenance = setup.causal_provenance
    if provenance is not None:
        for bar in reversed(bars):
            fingerprint = getattr(bar, "fingerprint", None)
            if callable(fingerprint) and fingerprint() == provenance.breakout_bar_fingerprint:
                return bar.interval_start_utc
    return setup.detected_at - BAR_INTERVAL


def formed_setup(
    *,
    setup: RuleSetup,
    strategy: StrategyRecord,
    candidate: Candidate,
    bars: Sequence[CompletedBar],
    session_date: date,
    now: datetime | None = None,
) -> FormedSetup:
    """The evidence row for ``setup``; the hour bucket is its bar's ET hour."""
    payload = setup.model_dump(mode="json")
    bar_ts = _aware(signal_bar_start(setup, bars), "bar_ts")
    return FormedSetup(
        run_id=setup.run_id,
        setup_fingerprint=setup.fingerprint(),
        candidate_id=setup.candidate_id,
        strategy_id=setup.strategy_id,
        strategy_version=setup.strategy_version,
        strategy_name=strategy.spec.name,
        strategy_kind=setup.strategy_kind,
        spec_fingerprint=strategy.content_hash,
        symbol=candidate.symbol,
        con_id=candidate.con_id,
        session_date=session_date,
        bar_ts=bar_ts,
        detected_at=setup.detected_at,
        hour_bucket=bar_ts.astimezone(MARKET_TIMEZONE).hour,
        entry_price=setup.entry_limit,
        stop_price=setup.stop_price,
        target_price=setup.target_price,
        atr=setup.atr,
        setup_payload=payload,
        levels=payload["levels"],
        created_at=_aware(now, "now") if now is not None else datetime.now(UTC),
    )


async def record_formed_setup(row: FormedSetup) -> bool:
    """Insert once per (run, fingerprint); True when this call inserted it."""
    async with get_db() as db:
        await db.execute(f"PRAGMA busy_timeout={int(SETUP_WRITE_BUSY_TIMEOUT_MS)}")
        cursor = await db.execute(_INSERT, row.parameters())
        await db.commit()
        return cursor.rowcount == 1


__all__ = [
    "BAR_INTERVAL",
    "FormedSetup",
    "SETUP_WRITE_BUSY_TIMEOUT_MS",
    "formed_setup",
    "record_formed_setup",
    "signal_bar_start",
]
