"""The nightly evidence run that follows the full replay (plan Design F).

``autotraderctl research replay`` is the supervisor's run-once replay child
(17:35-18:30 ET). When it replays every registered strategy it then gathers
the evidence for the paper promotion rule, in this order:

1. the signal-outcome tracker: every saved setup simulated forward on the
   recorded bars (its own append-only table is the only database write);
2. the live R ledger from the PAPER bridge's state files (read-only);
3. the paper promotion rule for every registered strategy (reports only);
4. the window research sweep, unless it would start after
   ``SWEEP_LATEST_START`` inside the replay window: the supervisor stops the
   child at 18:30, and the weekly message must not be cut off with it.

The reports land next to the replay's (``evidence.json``, ``windows.json``,
``live-r.json``, ``evidence.txt``). Nothing here can place an order or change
an authority; a database that is not this code's schema is left alone.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
import logging
from pathlib import Path

from trading.operations.paths import atomic_private_write
from trading.research.bars_store import NEW_YORK, BarsStore, ResearchPaths
from trading.research.live_ledger import LiveLedger, bridge_state_directory, read_live_ledger
from trading.research.outcomes import TrackerReport, track_outcomes
from trading.research.paper_promotion import (
    LiveEvidence,
    PromotionReport,
    evaluate_registry,
    read_live_evidence,
)
from trading.research.replay import ReplayResult, SkippedStrategy
from trading.research.windows import FamilyResearch, research_payload, sweep_all
from trading.scheduler.sessions import market_session

logger = logging.getLogger("autotrader.shadow.research")

UTC = timezone.utc
# Inside the supervised replay window (17:35-18:30 ET) the sweep starts no
# later than this; outside it (a manual run) it always runs.
REPLAY_WINDOW = (time(17, 35), time(18, 30))
SWEEP_LATEST_START = time(18, 0)
_R_QUANTUM = Decimal("0.0001")

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class NightlyEnvironment:
    """Where the evidence run reads and writes; tests substitute every path."""

    database: Path
    bridge_directory: Path
    clock: Clock = _utc_now

    @classmethod
    def current(cls) -> NightlyEnvironment:
        from config import cfg

        return cls(database=Path(cfg.DB_PATH).expanduser(), bridge_directory=bridge_state_directory())


@dataclass(frozen=True, slots=True)
class LiveHour:
    hour: int
    trades: int
    total_r: Decimal

    @property
    def expectancy_r(self) -> Decimal:
        return self.total_r / self.trades


@dataclass(frozen=True, slots=True)
class NightlyEvidence:
    tracker: TrackerReport
    promotion: tuple[PromotionReport, ...]
    live_r: LiveLedger
    windows: tuple[FamilyResearch, ...] = ()
    windows_note: str = ""
    # Live (tracked) setups of the current identity by signal hour, per strategy.
    live_hours: Mapping[str, tuple[LiveHour, ...]] = field(default_factory=dict)
    week_start: date | None = None


def week_start(day: date) -> date:
    """The first XNYS session of ``day``'s ISO week."""
    monday = day - timedelta(days=day.weekday())
    for offset in range(7):
        candidate = monday + timedelta(days=offset)
        if market_session(candidate) is not None:
            return candidate
    return monday


def sweep_allowed(now: datetime) -> tuple[bool, str]:
    local = now.astimezone(NEW_YORK).time()
    if REPLAY_WINDOW[0] <= local < REPLAY_WINDOW[1] and local > SWEEP_LATEST_START:
        return False, (
            f"not swept tonight: started after {SWEEP_LATEST_START:%H:%M} ET"
            f" (the replay window ends {REPLAY_WINDOW[1]:%H:%M})"
        )
    return True, ""


def _live_hours(promotion: Sequence[PromotionReport], live: LiveEvidence) -> dict[str, tuple[LiveHour, ...]]:
    hours: dict[str, tuple[LiveHour, ...]] = {}
    for report in promotion:
        trades = live.trades.get(report.identity.fingerprint(), ())
        grouped: dict[int, list[Decimal]] = defaultdict(list)
        for trade in trades:
            if trade.hour_bucket is not None:
                grouped[trade.hour_bucket].append(trade.net_r)
        if grouped:
            hours[report.strategy] = tuple(
                LiveHour(hour=hour, trades=len(values), total_r=sum(values, Decimal("0")))
                for hour, values in sorted(grouped.items())
            )
    return hours


def collect_evidence(
    results: Sequence[ReplayResult],
    skipped: Sequence[SkippedStrategy],
    store: BarsStore,
    *,
    environment: NightlyEnvironment,
) -> NightlyEvidence:
    """Tracker, live R, promotion and (time permitting) the window sweep."""
    now = environment.clock()
    tracker = asyncio.run(track_outcomes(store=store, database=environment.database, now=now))
    live_r = read_live_ledger(environment.bridge_directory)
    live = read_live_evidence(environment.database)
    replays = {result.strategy: result for result in results}
    notes = {item.strategy: item.note for item in skipped}
    promotion = evaluate_registry(replays=replays, live=live, replay_notes=notes)
    allowed, note = sweep_allowed(environment.clock())
    windows = sweep_all(store) if allowed else ()
    if not allowed:
        logger.warning("window research %s", note)
    return NightlyEvidence(
        tracker=tracker,
        promotion=promotion,
        live_r=live_r,
        windows=windows,
        windows_note=note,
        live_hours=_live_hours(promotion, live),
        week_start=week_start(now.astimezone(NEW_YORK).date()),
    )


def write_evidence(
    evidence: NightlyEvidence,
    *,
    paths: ResearchPaths,
    as_of: date,
    generated_at: datetime,
    text: str,
) -> tuple[str, ...]:
    """Private JSON reports plus the plain-text evidence summary, beside the replay's."""
    directory = paths.ensure_private(paths.reports_directory / as_of.isoformat())
    stamp = generated_at.astimezone(UTC).isoformat()
    documents = {
        "evidence.json": {
            "schema": "autotrader-v1/research-evidence",
            "version": 1,
            "generated_at": stamp,
            "tracker": evidence.tracker.to_payload(),
            "promotion": [report.payload() for report in evidence.promotion],
            "live_hours": {
                name: [
                    {"hour": row.hour, "trades": row.trades, "total_r": str(row.total_r.quantize(_R_QUANTUM))}
                    for row in rows
                ]
                for name, rows in evidence.live_hours.items()
            },
        },
        "windows.json": {
            "schema": "autotrader-v1/research-windows",
            "version": 1,
            "generated_at": stamp,
            "note": evidence.windows_note,
            "families": [research_payload(item) for item in evidence.windows],
        },
        "live-r.json": {
            "schema": "autotrader-v1/research-live-r",
            "version": 1,
            "generated_at": stamp,
            **evidence.live_r.payload(),
        },
    }
    written = []
    for name, document in documents.items():
        target = directory / name
        atomic_private_write(target, json.dumps(document, indent=2).encode("utf-8"))
        written.append(str(target))
    summary = directory / "evidence.txt"
    atomic_private_write(summary, (text + "\n").encode("utf-8"))
    written.append(str(summary))
    return tuple(written)


__all__ = [
    "REPLAY_WINDOW",
    "SWEEP_LATEST_START",
    "LiveHour",
    "NightlyEnvironment",
    "NightlyEvidence",
    "collect_evidence",
    "sweep_allowed",
    "week_start",
    "write_evidence",
]
