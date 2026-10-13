"""Window research: the nightly replay's rules-only sweep of wider entry windows.

Owner decision "Both" (2026-10-13): the shadow simulations trade all session,
and the replay tests longer windows. For each strategy family the sweep walks
every stored symbol-day once, through the widest window of its candidates,
polling every completed bar (bar end + 5 s) exactly as ``replay.walk_day``
does but without stopping at the first setup. A rule's result at a poll
depends on its window only through whether the poll is inside it (the rules
are stateless; a test proves every sweep window equal to a direct replay with
that window), so each narrower window is read off the same walk: its first
setup, or terminal rejection, among its own polls. That setup becomes one
bracket with the replay's fill model, the window's own cutoff ending the
entry TTL.

Results are net R after costs (``costs.DEFAULT_COST_MODEL``). The widest
window's trades are also split by the New York hour of their signal bar
(the setups table's ``hour_bucket``), marked core when the V1 window would
have polled that bar and extra otherwise.

Rules-only, like the replay: no review, no sizing, no position cap.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal
import logging
from typing import Any

from trading.contracts import CompletedBar, RuleSetup
from trading.research.bars_store import NEW_YORK, BarsStore, BarsStoreError, StoredDay, previous_session
from trading.research.costs import DEFAULT_COST_MODEL, CostModel
from trading.research.replay import (
    EXTENDED_BAR_STRATEGIES,
    POLL_DELAY,
    UNREPLAYABLE,
    _candidate,
    no_extended_bars,
    flatten_at,
    r_multiple,
    simulate_bracket,
    synthetic_quote,
)
from trading.research.stats import StrategyStats, summarize
from trading.rules.all_session import ALL_SESSION_WINDOW, V1_WINDOWS, EntryWindow, windowed_strategy
from trading.rules.gap_go import GAP_GO_STRATEGY_KIND
from trading.rules.momo import MOMO_STRATEGY_KIND
from trading.rules.orb import NO_SIGNAL_YET, QUOTE_REQUIRED
from trading.rules.strategy import StrategyEvaluator, entry_window_utc, is_data_rejection
from trading.rules.vwap_pb import VWAP_PB_STRATEGY_KIND

logger = logging.getLogger("autotrader.shadow.research")

UTC = timezone.utc
_R_QUANTUM = Decimal("0.0001")
_HOUR = timedelta(hours=1)

# The windows each family is replayed through, narrowest (its V1 window) first
# and the all-session window last. Extra windows widen one side at a time.
SWEEP_WINDOWS: dict[str, tuple[EntryWindow, ...]] = {
    "orb": (
        V1_WINDOWS["orb"],
        EntryWindow(start=time(9, 50), cutoff=time(13, 0)),
        EntryWindow(start=time(9, 50), cutoff=time(14, 0)),
        EntryWindow(start=time(9, 50), cutoff=time(15, 0)),
        ALL_SESSION_WINDOW,
    ),
    VWAP_PB_STRATEGY_KIND: (
        V1_WINDOWS[VWAP_PB_STRATEGY_KIND],
        EntryWindow(start=time(9, 55), cutoff=time(14, 30), close_buffer=_HOUR),
        EntryWindow(start=time(10, 30), cutoff=time(15, 30)),
        ALL_SESSION_WINDOW,
    ),
    MOMO_STRATEGY_KIND: (
        V1_WINDOWS[MOMO_STRATEGY_KIND],
        EntryWindow(start=time(9, 35), cutoff=time(15, 0), close_buffer=_HOUR),
        EntryWindow(start=time(10, 0), cutoff=time(15, 30)),
        ALL_SESSION_WINDOW,
    ),
    GAP_GO_STRATEGY_KIND: (
        V1_WINDOWS[GAP_GO_STRATEGY_KIND],
        EntryWindow(start=time(9, 35), cutoff=time(12, 0)),
        ALL_SESSION_WINDOW,
    ),
}
# The registry strategy whose rules each family sweeps; its replayability
# (replay.UNREPLAYABLE) decides whether the family is swept.
FAMILY_REGISTRY_KEYS: dict[str, str] = {
    "orb": "orb",
    VWAP_PB_STRATEGY_KIND: "vwap",
    MOMO_STRATEGY_KIND: "momo",
    GAP_GO_STRATEGY_KIND: "gap",
}
FAMILY_LABELS: dict[str, str] = {
    "orb": "ORB",
    VWAP_PB_STRATEGY_KIND: "VWAP_PB",
    MOMO_STRATEGY_KIND: "MOMO_HOD",
    GAP_GO_STRATEGY_KIND: "GAP_GO",
}


@dataclass(frozen=True, slots=True)
class WindowTrade:
    """One swept bracket, in R after costs (``r_multiple``) and before (``gross_r``)."""

    session_date: date
    symbol: str
    con_id: int
    signal_at: datetime
    exit_at: datetime
    exit_reason: str
    gross_r: Decimal
    cost_r: Decimal
    r_multiple: Decimal
    hour_bucket: int
    core: bool


@dataclass(frozen=True, slots=True)
class WindowResult:
    window: EntryWindow
    core: bool
    trades: tuple[WindowTrade, ...]
    unfilled: int
    stats: StrategyStats

    @property
    def label(self) -> str:
        return self.window.label()


@dataclass(frozen=True, slots=True)
class HourBucket:
    hour: int
    trades: int
    core_trades: int
    expectancy_r: Decimal | None
    total_r: Decimal
    win_rate: Decimal | None

    @property
    def scope(self) -> str:
        if self.core_trades == self.trades:
            return "core"
        return "extra" if self.core_trades == 0 else "mixed"


@dataclass(frozen=True, slots=True)
class FamilyResearch:
    family: str
    status: str  # "swept" | "skipped"
    note: str = ""
    sessions: tuple[date, ...] = ()
    symbol_days: int = 0
    windows: tuple[WindowResult, ...] = ()
    hours: tuple[HourBucket, ...] = ()
    core_stats: StrategyStats | None = None
    extra_stats: StrategyStats | None = None
    outcomes: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _Event:
    bar_end: datetime
    now: datetime
    setup: RuleSetup | None
    terminal: bool


def widest_window(windows: Sequence[EntryWindow]) -> EntryWindow:
    """A window containing every candidate on every session."""
    return EntryWindow(
        start=min(window.start for window in windows),
        cutoff=max(window.cutoff for window in windows),
        close_buffer=min(window.close_buffer for window in windows),
    )


def _inputs(day: StoredDay, family: str) -> dict[str, Any]:
    return {
        "candidate": _candidate(day),
        "session_date": day.session_date,
        "run_id": f"research-window-{family}-{day.session_date.isoformat()}",
        "strategy_id": f"research-window-{family}",
        "strategy_version": 1,
        "reference_price": day.prior_close,
        "average_dollar_volume": day.avg_dollar_volume,
        "candidate_input_fingerprint": None,
    }


def iter_polls(
    strategy: StrategyEvaluator,
    *,
    day: StoredDay,
    prior_bars: Sequence[CompletedBar],
    session_bars: Sequence[CompletedBar],
    family: str,
) -> Iterator[_Event]:
    """Every poll of ``strategy``'s window, classified as replay.walk_day classifies it.

    Unlike walk_day it does not stop at a setup or a terminal rejection: a
    window that starts later still needs the polls after them.
    """
    start, cutoff = entry_window_utc(strategy, day.session_date)
    inputs = _inputs(day, family)
    history = tuple(prior_bars)
    for index, bar in enumerate(session_bars):
        if bar.interval_end_utc < start:
            continue
        now = bar.interval_end_utc + POLL_DELAY
        if now >= cutoff:
            return
        visible = (*history, *session_bars[: index + 1])
        result = strategy.evaluate(bars=visible, quote=None, now=now, **inputs)
        if result.setup is None and result.rejection_reason == QUOTE_REQUIRED:
            quote = synthetic_quote(con_id=day.con_id, close=bar.close, now=now)
            result = strategy.evaluate(bars=visible, quote=quote, now=now, **inputs)
        if result.setup is not None:
            yield _Event(bar.interval_end_utc, now, result.setup, terminal=False)
            continue
        reason = result.rejection_reason or "rejected"
        pending = (
            reason in (NO_SIGNAL_YET, QUOTE_REQUIRED)
            or is_data_rejection(reason)
            or result.retryable
        )
        yield _Event(bar.interval_end_utc, now, None, terminal=not pending)


def first_decisions(
    events: Iterable[_Event], ranges: Sequence[tuple[datetime, datetime]]
) -> dict[int, _Event]:
    """Each window's first setup or terminal rejection among its own polls.

    Stops reading ``events`` once every window has decided or closed, so the
    walk behind them evaluates no more polls than the windows need.
    """
    open_windows = set(range(len(ranges)))
    decided: dict[int, _Event] = {}
    for event in events:
        for index in sorted(open_windows):
            start, cutoff = ranges[index]
            if event.now >= cutoff:
                open_windows.discard(index)
            elif event.bar_end >= start and (event.setup is not None or event.terminal):
                decided[index] = event
                open_windows.discard(index)
        if not open_windows:
            break
    return decided


def _hour_bucket(signal_end: datetime) -> int:
    return (signal_end - timedelta(minutes=5)).astimezone(NEW_YORK).hour


def _quantize(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(_R_QUANTUM, rounding=ROUND_HALF_EVEN)


def _hours(trades: Sequence[WindowTrade]) -> tuple[HourBucket, ...]:
    grouped: dict[int, list[WindowTrade]] = defaultdict(list)
    for trade in trades:
        grouped[trade.hour_bucket].append(trade)
    buckets = []
    for hour in sorted(grouped):
        items = grouped[hour]
        total = sum((trade.r_multiple for trade in items), Decimal("0"))
        wins = sum(1 for trade in items if trade.r_multiple > 0)
        buckets.append(
            HourBucket(
                hour=hour,
                trades=len(items),
                core_trades=sum(1 for trade in items if trade.core),
                expectancy_r=_quantize(total / len(items)),
                total_r=total,
                win_rate=_quantize(Decimal(wins) / len(items)),
            )
        )
    return tuple(buckets)


def sweep_family(
    family: str,
    store: BarsStore,
    *,
    windows: Sequence[EntryWindow] | None = None,
    sessions: Iterable[date] | None = None,
    cost_model: CostModel = DEFAULT_COST_MODEL,
) -> FamilyResearch:
    """Replay one family's rules through every window of its sweep."""
    key = FAMILY_REGISTRY_KEYS[family]
    if key in UNREPLAYABLE:
        return FamilyResearch(family=family, status="skipped", note=UNREPLAYABLE[key].note)
    # The sweep replays regular-hours days only; GAP_GO's gap and trigger need
    # pre-market bars, so it is never swept on regular-hours bars alone.
    if key in EXTENDED_BAR_STRATEGIES:
        return FamilyResearch(family=family, status="skipped", note=no_extended_bars(key).note)
    candidates = tuple(windows or SWEEP_WINDOWS[family])
    core_window = V1_WINDOWS[family]
    wide = windowed_strategy(family, widest_window(candidates))
    evaluators = [windowed_strategy(family, window) for window in candidates]
    core_evaluator = windowed_strategy(family, core_window)
    wanted = None if sessions is None else frozenset(sessions)
    trades: dict[int, list[WindowTrade]] = {index: [] for index in range(len(candidates))}
    unfilled: Counter[int] = Counter()
    outcomes: Counter[str] = Counter()
    replayed: set[date] = set()
    symbol_days = 0
    prior_cache: dict[int, tuple[CompletedBar, ...] | None] = {}
    prior_cache_date: date | None = None

    for session_date in store.list_sessions():
        if wanted is not None and session_date not in wanted:
            continue
        prior_date = previous_session(session_date)
        cached = prior_cache if prior_cache_date == prior_date else {}
        today: dict[int, tuple[CompletedBar, ...] | None] = {}
        flatten = flatten_at(session_date)
        ranges = [entry_window_utc(evaluator, session_date) for evaluator in evaluators]
        core_start, core_cutoff = entry_window_utc(core_evaluator, session_date)
        for con_id in store.list_con_ids(session_date):
            try:
                day = store.read_day(session_date, con_id)
                session_bars = day.completed_bars()
            except (BarsStoreError, ValueError) as exc:
                logger.warning("unreadable research day %s/%s: %s", session_date, con_id, exc)
                outcomes["skipped:store_error"] += 1
                today[con_id] = None
                continue
            today[con_id] = session_bars
            if con_id in cached:
                prior_bars = cached[con_id]
            else:
                try:
                    prior_day = store.get_day(prior_date, con_id)
                    prior_bars = None if prior_day is None else prior_day.completed_bars()
                except (BarsStoreError, ValueError):
                    prior_bars = None
            if prior_bars is None:
                outcomes["skipped:prior_session_missing"] += 1
                continue
            if day.prior_close is None or day.avg_dollar_volume is None:
                outcomes["skipped:liquidity_unknown"] += 1
                continue
            try:
                decisions = first_decisions(
                    iter_polls(
                        wide,
                        day=day,
                        prior_bars=prior_bars,
                        session_bars=session_bars,
                        family=family,
                    ),
                    ranges,
                )
            except (ValueError, ArithmeticError) as exc:
                logger.warning("window sweep of %s %s failed: %s", day.symbol, session_date, exc)
                outcomes[f"error:{type(exc).__name__}"] += 1
                continue
            symbol_days += 1
            replayed.add(session_date)
            for index, (_start, cutoff) in enumerate(ranges):
                decision = decisions.get(index)
                if decision is None or decision.setup is None:
                    continue
                setup = decision.setup
                bracket = simulate_bracket(
                    entry=setup.entry_limit,
                    stop=setup.stop_price,
                    target=setup.target_price,
                    placed_after=decision.bar_end,
                    bars=session_bars,
                    entry_cutoff=cutoff,
                    flatten=flatten,
                )
                if bracket is None:
                    unfilled[index] += 1
                    continue
                gross = r_multiple(
                    entry=setup.entry_limit, stop=setup.stop_price, exit_price=bracket.exit_price
                )
                cost = cost_model.cost_r(
                    entry=setup.entry_limit, stop=setup.stop_price, exit_reason=bracket.exit_reason
                )
                trades[index].append(
                    WindowTrade(
                        session_date=session_date,
                        symbol=day.symbol,
                        con_id=con_id,
                        signal_at=setup.detected_at,
                        exit_at=bracket.exit_at,
                        exit_reason=bracket.exit_reason,
                        gross_r=gross,
                        cost_r=cost,
                        r_multiple=gross - cost,
                        hour_bucket=_hour_bucket(setup.detected_at),
                        core=core_start <= decision.bar_end and decision.now < core_cutoff,
                    )
                )
        prior_cache_date, prior_cache = session_date, today

    ordered_sessions = tuple(sorted(replayed))
    results = []
    for index, window in enumerate(candidates):
        items = tuple(
            sorted(trades[index], key=lambda trade: (trade.session_date, trade.signal_at, trade.symbol))
        )
        results.append(
            WindowResult(
                window=window,
                core=window == core_window,
                trades=items,
                unfilled=unfilled[index],
                stats=summarize(
                    window.label(),
                    items,
                    unfilled=unfilled[index],
                    symbol_days=symbol_days,
                    sessions=len(ordered_sessions),
                ),
            )
        )
    widest = max(results, key=lambda result: (result.window.cutoff, -_minutes(result.window.start)))
    return FamilyResearch(
        family=family,
        status="swept",
        sessions=ordered_sessions,
        symbol_days=symbol_days,
        windows=tuple(results),
        hours=_hours(widest.trades),
        core_stats=summarize("core", [trade for trade in widest.trades if trade.core]),
        extra_stats=summarize("extra", [trade for trade in widest.trades if not trade.core]),
        outcomes=dict(sorted(outcomes.items())),
    )


def _minutes(moment: time) -> int:
    return moment.hour * 60 + moment.minute


def sweep_all(
    store: BarsStore,
    *,
    families: Iterable[str] = tuple(SWEEP_WINDOWS),
    cost_model: CostModel = DEFAULT_COST_MODEL,
) -> tuple[FamilyResearch, ...]:
    return tuple(sweep_family(family, store, cost_model=cost_model) for family in families)


def _r(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN):+}R"


def _pf(value: Decimal | None, trades: int) -> str:
    if not trades:
        return "n/a"
    if value is None:
        return "n/a (no losses)"
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def window_lines(research: FamilyResearch) -> list[str]:
    """Plain text for the replay summary and the weekly message."""
    label = FAMILY_LABELS.get(research.family, research.family)
    if research.status != "swept":
        # The short reason only; windows.json keeps the whole note.
        return [f"{label}: not swept ({research.note.split(':')[0]})"]
    lines = [f"{label} ({len(research.sessions)} sessions, {research.symbol_days} symbol-days)"]
    for result in research.windows:
        stats = result.stats
        tag = " (V1)" if result.core else (" (V2)" if result.window == ALL_SESSION_WINDOW else "")
        if not stats.trades:
            lines.append(f"  {result.label}{tag}: no trades")
            continue
        lines.append(
            f"  {result.label}{tag}: {stats.trades} trades, {_r(stats.expectancy_r)}/trade,"
            f" PF {_pf(stats.profit_factor, stats.trades)}, total {_r(stats.total_r)}"
        )
    if research.hours:
        lines.append(
            "  by signal hour: "
            + " · ".join(
                f"{bucket.hour:02d}h {bucket.trades} {_r(bucket.expectancy_r)}"
                + ("*" if bucket.scope != "core" else "")
                for bucket in research.hours
            )
        )
        core, extra = research.core_stats, research.extra_stats
        if core is not None and extra is not None:
            lines.append(
                f"  core {_count(core)} | extra hours {_count(extra)}"
            )
    return lines


def _count(stats: StrategyStats) -> str:
    if not stats.trades:
        return "no trades"
    return f"{stats.trades} trades {_r(stats.expectancy_r)}/trade"


def research_payload(research: FamilyResearch) -> dict[str, Any]:
    def stats_view(stats: StrategyStats | None) -> dict[str, Any] | None:
        if stats is None:
            return None
        return {
            "trades": stats.trades,
            "expectancy_r": None if stats.expectancy_r is None else str(_quantize(stats.expectancy_r)),
            "total_r": str(_quantize(stats.total_r)),
            "profit_factor": None if stats.profit_factor is None else str(_quantize(stats.profit_factor)),
            "max_drawdown_r": str(_quantize(stats.max_drawdown_r)),
        }

    return {
        "family": research.family,
        "status": research.status,
        "note": research.note,
        "basis": "rules-only replay, net R after costs",
        "sessions": [day.isoformat() for day in research.sessions],
        "symbol_days": research.symbol_days,
        "windows": [
            {
                "window": result.window.payload(),
                "core": result.core,
                "unfilled": result.unfilled,
                **(stats_view(result.stats) or {}),
            }
            for result in research.windows
        ],
        "hours": [
            {
                "hour": bucket.hour,
                "scope": bucket.scope,
                "trades": bucket.trades,
                "expectancy_r": None if bucket.expectancy_r is None else str(bucket.expectancy_r),
                "total_r": str(_quantize(bucket.total_r)),
                "win_rate": None if bucket.win_rate is None else str(bucket.win_rate),
            }
            for bucket in research.hours
        ],
        "core": stats_view(research.core_stats),
        "extra_hours": stats_view(research.extra_stats),
        "outcomes": dict(research.outcomes),
    }


__all__ = [
    "FAMILY_LABELS",
    "FAMILY_REGISTRY_KEYS",
    "SWEEP_WINDOWS",
    "FamilyResearch",
    "HourBucket",
    "WindowResult",
    "WindowTrade",
    "research_payload",
    "sweep_all",
    "sweep_family",
    "first_decisions",
    "iter_polls",
    "widest_window",
    "window_lines",
]
