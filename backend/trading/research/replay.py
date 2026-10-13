"""Deterministic strategy replay over stored research days.

Each stored session is walked bar by bar through the strategy's entry window
exactly as the live pipeline polls it: at every bar end + 5 s the strategy
sees only the bars completed by then (the previous session plus today, like
the live two-session request), a signal that asks for a quote gets a
synthetic one, and the first accepted setup per symbol and day becomes one
simulated bracket. Non-retryable rejections end the symbol's day, as a
terminal decision does live.

This is a rule-only baseline. Claude's review, the position cap, the daily
loss stop and sizing are not simulated, quotes are synthetic (spread and
chase always pass), and every stored contract is evaluated, not only the
live hot list. Fills and exits are conservative (see ``simulate_bracket``).

GAP_GO's gap, pre-market volume and trigger come from pre-market bars, so it
replays on the extended-hours days the recorder stores for each session's
gap candidates (``bars_store``: pre-market from 04:00 ET through the close),
with the earlier sessions' regular-hours days as its ATR history, exactly as
``gap_go_bars`` assembles the live input. Until a first extended day is
stored its report says so (``needs_extended_bars``) instead of faking it.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal
import json
import logging
import sys
from typing import TYPE_CHECKING, Any, Literal

from trading.contracts import Candidate, CompletedBar, QuoteSnapshot, RuleSetup
from trading.operations.paths import atomic_private_write
from trading.research.bars_store import (
    NEW_YORK,
    BarsStore,
    BarsStoreError,
    ResearchPaths,
    StoredDay,
    previous_session,
)
from trading.research.stats import (
    StrategyStats,
    format_text_report,
    stats_payload,
    summarize,
)
from trading.rules.gap_go import GAP_GO_RULES_VERSION, GapGoStrategy
from trading.rules.all_session import (
    GAP_GO_V2_NAME,
    ORB_V1_RULES_VERSION,
    ORB_V2_NAME,
    VWAP_PB_V2_NAME,
    orb_v2_strategy,
    vwap_pb_v2_strategy,
)
from trading.rules.momo import MOMO_RULES_VERSION, MomoStrategy
from trading.rules.orb import NO_SIGNAL_YET, QUOTE_REQUIRED
from trading.rules.strategy import (
    ORBStrategy,
    StrategyEvaluator,
    entry_window_utc,
    is_data_rejection,
)
from trading.rules.vwap_pb import VWAP_PB_RULES_VERSION, VwapPbStrategy
from trading.scheduler.sessions import market_session

if TYPE_CHECKING:
    from trading.research.nightly import NightlyEnvironment
    from trading.research.report_notify import NotifyMode

logger = logging.getLogger("autotrader.shadow.research")

UTC = timezone.utc
REPLAY_SCHEMA = "autotrader-v1/research-replay"
REPLAY_VERSION = 1
# The live pipeline evaluates a bar once it has completed; quotes arrive then.
POLL_DELAY = timedelta(seconds=5)
SYNTHETIC_TICK = Decimal("0.01")
# Mirrors the paper bridge: an unfilled entry is cancelled after 10 minutes
# (the two bars after the signal) or at the strategy cutoff, and every
# position is flattened at 15:45 ET (15 minutes before an early close).
ENTRY_TTL = timedelta(minutes=10)
FLATTEN_TIME = time(15, 45)
FLATTEN_BEFORE_CLOSE = timedelta(minutes=15)
# The bar fill model above and in ``simulate_bracket``, by name: the nightly
# tracker records every outcome under it, so a change to the model needs a new
# id (and every saved setup is simulated again under it).
FILL_MODEL_ID = "replay-bracket-v1"
_R_QUANTUM = Decimal("0.0001")

STRATEGY_FACTORIES: dict[str, Callable[[], StrategyEvaluator]] = {
    "orb": ORBStrategy,
    "momo": MomoStrategy,
    "vwap": VwapPbStrategy,
    "orb-v2": orb_v2_strategy,
    "vwap-v2": vwap_pb_v2_strategy,
}
RULES_VERSIONS = {
    "orb": ORB_V1_RULES_VERSION,
    "momo": MOMO_RULES_VERSION,
    "vwap": VWAP_PB_RULES_VERSION,
    "gap": GAP_GO_RULES_VERSION,
    "orb-v2": ORB_V2_NAME,
    "vwap-v2": VWAP_PB_V2_NAME,
    "gap-v2": GAP_GO_V2_NAME,
}
# Every strategy the live pipeline registers (shadow_service.STRATEGIES; a
# test pins the two), in report order. Kept literal so a replay never loads
# the TWS and Claude stack that module imports.
REGISTERED_STRATEGIES = ("orb", "momo", "vwap", "gap", "orb-v2", "vwap-v2", "gap-v2")
NEEDS_EXTENDED_BARS = "needs_extended_bars"

ExitReason = Literal["stop", "target", "time", "data_end"]

REPLAY_NOTES = (
    "Rule-only baseline: Claude's review is not simulated.",
    "Synthetic quotes: bid = bar close - 0.01, ask = bar close, observed 5 s after the bar.",
    "Every stored contract is evaluated (not only the live hot list); no position cap,"
    " daily loss stop, or sizing.",
    "Universe: the contracts stored (today's qualified watchlist) replayed over earlier"
    " sessions, so the sample carries the watchlist's selection bias.",
    "Entry: limit at the setup price, filled only if one of the next two bars trades at or"
    " below it (10-minute TTL, never after the strategy cutoff), at the limit price.",
    "Exit: stop before target when one bar touches both; a gap through the stop exits at the"
    " open; still open at 15:45 ET exits at that bar's close (time); data_end = bars ended"
    " before 15:45 and the trade is marked at the last close.",
)


class UnreplayableStrategyError(ValueError):
    """A registered strategy whose inputs the research store cannot supply."""


@dataclass(frozen=True, slots=True)
class SkippedStrategy:
    """A registered strategy the replay declines to simulate, with a named reason."""

    strategy: str
    reason: str
    note: str

    def to_payload(self, *, generated_at: datetime) -> dict[str, Any]:
        return {
            "schema": REPLAY_SCHEMA,
            "version": REPLAY_VERSION,
            "generated_at": generated_at.astimezone(UTC).isoformat(),
            "status": "skipped",
            "strategy": self.strategy,
            "rules_version": RULES_VERSIONS.get(self.strategy, "custom"),
            "reason": self.reason,
            "note": self.note,
        }


# Strategies replayed on extended-hours days (pre-market + regular session)
# for the session itself, with regular-hours days as earlier history.
EXTENDED_BAR_STRATEGIES: dict[str, Callable[[], StrategyEvaluator]] = {"gap": GapGoStrategy}
GAP_REPLAY_NOTE = (
    "GAP_GO replays only the recorded gap candidates (watchlist and scanner names whose"
    " official gap reached 3% in the first hour), from the first night extended-hours bars"
    " were recorded; the sample is gappers by construction."
)


def no_extended_bars(strategy: str) -> SkippedStrategy:
    """The named skip of an extended-bar strategy while no extended day is stored."""
    return SkippedStrategy(
        strategy=strategy,
        reason=NEEDS_EXTENDED_BARS,
        note=(
            "needs extended bars: GAP_GO's gap, pre-market volume and trigger come from"
            " pre-market bars, and no extended-hours day is recorded yet"
        ),
    )


# Registered strategies the store cannot replay honestly. GAP_GO_V2's
# all-session window is not wired to the extended-hours replay yet.
UNREPLAYABLE: dict[str, SkippedStrategy] = {
    "gap-v2": SkippedStrategy(
        strategy="gap-v2",
        reason=NEEDS_EXTENDED_BARS,
        note="needs extended bars: GAP_GO_V2's all-session replay on extended-hours days is not wired yet",
    ),
}


@dataclass(frozen=True, slots=True)
class BracketOutcome:
    filled_at: datetime
    exit_at: datetime
    exit_price: Decimal
    exit_reason: ExitReason
    bars_held: int


@dataclass(frozen=True, slots=True)
class SimulatedTrade:
    session_date: date
    symbol: str
    con_id: int
    strategy: str
    signal_at: datetime
    entry: Decimal
    stop: Decimal
    target: Decimal
    filled_at: datetime
    exit_at: datetime
    exit: Decimal
    exit_reason: ExitReason
    r_multiple: Decimal
    bars_held: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "date": self.session_date.isoformat(),
            "symbol": self.symbol,
            "con_id": self.con_id,
            "strategy": self.strategy,
            "signal_at_et": _et_clock(self.signal_at),
            "entry": str(self.entry),
            "stop": str(self.stop),
            "target": str(self.target),
            "filled_bar_et": _et_clock(self.filled_at),
            "exit_at_et": _et_clock(self.exit_at),
            "exit": str(self.exit),
            "exit_reason": self.exit_reason,
            "r_multiple": str(self.r_multiple),
            "bars_held": self.bars_held,
        }


@dataclass(frozen=True, slots=True)
class UnfilledSetup:
    session_date: date
    symbol: str
    con_id: int
    strategy: str
    signal_at: datetime
    entry: Decimal
    stop: Decimal
    target: Decimal

    def to_payload(self) -> dict[str, Any]:
        return {
            "date": self.session_date.isoformat(),
            "symbol": self.symbol,
            "con_id": self.con_id,
            "strategy": self.strategy,
            "signal_at_et": _et_clock(self.signal_at),
            "entry": str(self.entry),
            "stop": str(self.stop),
            "target": str(self.target),
        }


@dataclass(frozen=True, slots=True)
class ReplayResult:
    strategy: str
    strategy_kind: str
    sessions: tuple[date, ...]
    symbols: int
    symbol_days: int
    trades: tuple[SimulatedTrade, ...]
    unfilled: tuple[UnfilledSetup, ...]
    outcomes: Mapping[str, int]
    # Symbol-days on which each pending rejection occurred at least once.
    skip_reasons: Mapping[str, int]
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _Walk:
    setup: RuleSetup | None = None
    placed_after: datetime | None = None
    cutoff: datetime | None = None
    outcome: str | None = None
    # Pending rejections other than "no signal yet" seen during the day, e.g.
    # MOMO signals skipped as extended above VWAP or with too wide a stop.
    skips: frozenset[str] = frozenset()


def _et_clock(value: datetime) -> str:
    return value.astimezone(NEW_YORK).strftime("%H:%M")


def synthetic_quote(*, con_id: int, close: Decimal, now: datetime) -> QuoteSnapshot:
    """One tick wide, ask at the bar close, observed at the poll moment."""
    return QuoteSnapshot(
        con_id=con_id,
        bid=close - SYNTHETIC_TICK,
        ask=close,
        observed_at=now,
        min_tick=SYNTHETIC_TICK,
    )


def flatten_at(session_date: date) -> datetime:
    regular = datetime.combine(session_date, FLATTEN_TIME, tzinfo=NEW_YORK).astimezone(UTC)
    session = market_session(session_date)
    if session is None:
        return regular
    return min(regular, session.closes_at.astimezone(UTC) - FLATTEN_BEFORE_CLOSE)


def r_multiple(*, entry: Decimal, stop: Decimal, exit_price: Decimal) -> Decimal:
    risk = entry - stop
    if risk <= 0:
        raise ValueError("a long bracket needs its stop below its entry")
    return ((exit_price - entry) / risk).quantize(_R_QUANTUM, rounding=ROUND_HALF_EVEN)


def simulate_bracket(
    *,
    entry: Decimal,
    stop: Decimal,
    target: Decimal,
    placed_after: datetime,
    bars: Sequence[CompletedBar],
    entry_cutoff: datetime,
    flatten: datetime,
    entry_ttl: timedelta = ENTRY_TTL,
) -> BracketOutcome | None:
    """Simulate one long limit bracket on five-minute bars; ``None`` if unfilled.

    Conservative where bars hide the order of events:
    - the entry fills at its limit (never better) on the first bar starting
      inside the TTL and before the cutoff whose low reaches the limit;
    - on the fill bar a stop counts, but a target counts only when the bar
      opened at or below the limit (the fill came first; otherwise the high
      may have printed before the fill);
    - a bar touching both stop and target is a stop, and a bar opening below
      the stop exits at its open;
    - a position still open at ``flatten`` exits at the close of the last bar
      ending by then (``time``); if the session's data ends before the
      flatten it is marked at the last close (``data_end``).
    """
    if not stop < entry < target:
        raise ValueError("a long bracket needs stop < entry < target")
    ordered = sorted(bars, key=lambda bar: bar.interval_start_utc)
    after = [
        bar
        for bar in ordered
        if bar.interval_start_utc >= placed_after and bar.interval_end_utc <= flatten
    ]
    deadline = min(placed_after + entry_ttl, entry_cutoff)
    fill_index: int | None = None
    for index, bar in enumerate(after):
        if bar.interval_start_utc >= deadline:
            break
        if bar.low <= entry:
            fill_index = index
            break
    if fill_index is None:
        return None
    fill_bar = after[fill_index]
    filled_at_open = fill_bar.open <= entry
    for index in range(fill_index, len(after)):
        bar = after[index]
        held = index - fill_index + 1
        if bar.low <= stop:
            return BracketOutcome(
                filled_at=fill_bar.interval_start_utc,
                exit_at=bar.interval_end_utc,
                exit_price=min(stop, bar.open),
                exit_reason="stop",
                bars_held=held,
            )
        if bar.high >= target and (index > fill_index or filled_at_open):
            return BracketOutcome(
                filled_at=fill_bar.interval_start_utc,
                exit_at=bar.interval_end_utc,
                exit_price=target,
                exit_reason="target",
                bars_held=held,
            )
    last = after[-1]
    # The flatten trades at the last price before 15:45, even when a quiet
    # contract printed no bar ending exactly then; only missing data is data_end.
    reached_flatten = ordered[-1].interval_end_utc >= flatten
    return BracketOutcome(
        filled_at=fill_bar.interval_start_utc,
        exit_at=last.interval_end_utc,
        exit_price=last.close,
        exit_reason="time" if reached_flatten else "data_end",
        bars_held=len(after) - fill_index,
    )


def _candidate(day: StoredDay) -> Candidate:
    return Candidate(
        id=f"research-{day.session_date.isoformat()}-{day.con_id}",
        scanner_run_id=f"research-{day.session_date.isoformat()}",
        con_id=day.con_id,
        symbol=day.symbol,
        primary_exchange=day.primary_exchange or "SMART",
        rank=1,
        admission_status="admitted",
    )


def walk_day(
    strategy: StrategyEvaluator,
    *,
    day: StoredDay,
    prior_bars: Sequence[CompletedBar],
    session_bars: Sequence[CompletedBar],
    strategy_name: str,
) -> _Walk:
    """Poll the strategy once per completed bar inside its entry window."""
    if day.prior_close is None or day.avg_dollar_volume is None:
        return _Walk(outcome="skipped:liquidity_unknown")
    start, cutoff = entry_window_utc(strategy, day.session_date)
    inputs: dict[str, Any] = {
        "candidate": _candidate(day),
        "session_date": day.session_date,
        "run_id": f"research-{strategy_name}-{day.session_date.isoformat()}",
        "strategy_id": f"research-{strategy_name}",
        "strategy_version": 1,
        "reference_price": day.prior_close,
        "average_dollar_volume": day.avg_dollar_volume,
        "candidate_input_fingerprint": None,
    }
    last_reason = "no_poll_inside_window"
    skips: set[str] = set()
    history = tuple(prior_bars)
    for index, bar in enumerate(session_bars):
        if bar.interval_end_utc < start:
            continue
        now = bar.interval_end_utc + POLL_DELAY
        if now >= cutoff:
            break
        visible = (*history, *session_bars[: index + 1])
        result = strategy.evaluate(bars=visible, quote=None, now=now, **inputs)
        if result.setup is None and result.rejection_reason == QUOTE_REQUIRED:
            quote = synthetic_quote(con_id=day.con_id, close=bar.close, now=now)
            result = strategy.evaluate(bars=visible, quote=quote, now=now, **inputs)
        if result.setup is not None:
            return _Walk(
                setup=result.setup,
                placed_after=bar.interval_end_utc,
                cutoff=cutoff,
                skips=frozenset(skips),
            )
        reason = result.rejection_reason or "rejected"
        # The pipeline's pending rules: data problems, no signal yet, and
        # retryable rejections wait for the next poll; anything else is final.
        if (
            reason in (NO_SIGNAL_YET, QUOTE_REQUIRED)
            or is_data_rejection(reason)
            or result.retryable
        ):
            last_reason = reason
            if reason not in (NO_SIGNAL_YET, QUOTE_REQUIRED):
                skips.add(reason)
            continue
        return _Walk(outcome=f"rejected:{reason}", skips=frozenset(skips))
    return _Walk(outcome=f"no_setup:{last_reason}", skips=frozenset(skips))


def replay_strategy(
    strategy_name: str,
    store: BarsStore,
    *,
    sessions: Iterable[date] | None = None,
    strategy: StrategyEvaluator | None = None,
) -> ReplayResult:
    """Replay one strategy over every stored session that has its prior session.

    An extended-bar strategy (GAP_GO) walks the stored extended-hours days;
    its prior session always comes from the regular-hours store, never from
    another extended day, so ATR history is regular-session bars only.
    """
    name = strategy_name.strip().lower()
    if name in UNREPLAYABLE:
        raise UnreplayableStrategyError(f"{name} is not replayed: {UNREPLAYABLE[name].note}")
    extended = name in EXTENDED_BAR_STRATEGIES
    if strategy is None:
        factory = EXTENDED_BAR_STRATEGIES.get(name) or STRATEGY_FACTORIES.get(name)
        if factory is None:
            raise ValueError(f"unknown strategy {strategy_name!r}")
        strategy = factory()
    wanted = None if sessions is None else frozenset(sessions)
    dates = [
        day for day in store.list_sessions(extended=extended) if wanted is None or day in wanted
    ]
    trades: list[SimulatedTrade] = []
    unfilled: list[UnfilledSetup] = []
    outcomes: Counter[str] = Counter()
    skip_reasons: Counter[str] = Counter()
    replayed_sessions: set[date] = set()
    symbols: set[int] = set()
    symbol_days = 0
    cache_date: date | None = None
    cache: dict[int, tuple[CompletedBar, ...] | None] = {}

    for session_date in dates:
        prior_date = previous_session(session_date)
        # An extended day is never another day's history: its pre-market bars
        # would enter the ATR. Extended walks always read the RTH prior day.
        prior_cache = cache if cache_date == prior_date and not extended else {}
        today_cache: dict[int, tuple[CompletedBar, ...] | None] = {}
        flatten = flatten_at(session_date)
        for con_id in store.list_con_ids(session_date, extended=extended):
            try:
                day = store.read_day(session_date, con_id, extended=extended)
                session_bars = day.completed_bars()
            except (BarsStoreError, ValueError) as exc:
                logger.warning("unreadable research day %s/%s: %s", session_date, con_id, exc)
                outcomes["skipped:store_error"] += 1
                today_cache[con_id] = None
                continue
            today_cache[con_id] = session_bars
            if con_id in prior_cache:
                prior_bars = prior_cache[con_id]
            else:
                try:
                    prior_day = store.get_day(prior_date, con_id)
                    prior_bars = None if prior_day is None else prior_day.completed_bars()
                except (BarsStoreError, ValueError):
                    prior_bars = None
            if prior_bars is None:
                # Live rules always see the previous session (ATR, prior high).
                outcomes["skipped:prior_session_missing"] += 1
                continue
            try:
                walk = walk_day(
                    strategy,
                    day=day,
                    prior_bars=prior_bars,
                    session_bars=session_bars,
                    strategy_name=name,
                )
            except (ValueError, ArithmeticError) as exc:
                logger.warning("replay of %s %s failed: %s", day.symbol, session_date, exc)
                outcomes[f"error:{type(exc).__name__}"] += 1
                continue
            if walk.outcome is not None and walk.outcome.startswith("skipped:"):
                outcomes[walk.outcome] += 1
                continue
            symbol_days += 1
            symbols.add(con_id)
            replayed_sessions.add(session_date)
            skip_reasons.update(walk.skips)
            if walk.setup is None:
                outcomes[walk.outcome or "no_setup"] += 1
                continue
            setup = walk.setup
            if walk.placed_after is None or walk.cutoff is None:
                raise RuntimeError("an accepted walk must carry its poll and cutoff")
            bracket = simulate_bracket(
                entry=setup.entry_limit,
                stop=setup.stop_price,
                target=setup.target_price,
                placed_after=walk.placed_after,
                bars=session_bars,
                entry_cutoff=walk.cutoff,
                flatten=flatten,
            )
            if bracket is None:
                outcomes["setup:unfilled"] += 1
                unfilled.append(
                    UnfilledSetup(
                        session_date=session_date,
                        symbol=day.symbol,
                        con_id=con_id,
                        strategy=strategy.strategy_kind,
                        signal_at=setup.detected_at,
                        entry=setup.entry_limit,
                        stop=setup.stop_price,
                        target=setup.target_price,
                    )
                )
                continue
            outcomes[f"trade:{bracket.exit_reason}"] += 1
            trades.append(
                SimulatedTrade(
                    session_date=session_date,
                    symbol=day.symbol,
                    con_id=con_id,
                    strategy=strategy.strategy_kind,
                    signal_at=setup.detected_at,
                    entry=setup.entry_limit,
                    stop=setup.stop_price,
                    target=setup.target_price,
                    filled_at=bracket.filled_at,
                    exit_at=bracket.exit_at,
                    exit=bracket.exit_price,
                    exit_reason=bracket.exit_reason,
                    r_multiple=r_multiple(
                        entry=setup.entry_limit,
                        stop=setup.stop_price,
                        exit_price=bracket.exit_price,
                    ),
                    bars_held=bracket.bars_held,
                )
            )
        cache_date, cache = session_date, today_cache

    ordered_sessions = tuple(sorted(replayed_sessions))
    trades.sort(key=lambda trade: (trade.session_date, trade.signal_at, trade.symbol))
    unfilled.sort(key=lambda item: (item.session_date, item.signal_at, item.symbol))
    metadata: dict[str, Any] = {
        "replay_version": REPLAY_VERSION,
        "strategy": name,
        "strategy_kind": strategy.strategy_kind,
        "rules_version": RULES_VERSIONS.get(name, "custom"),
        "baseline": "rule-only (Claude review not simulated)",
        "quotes": "synthetic (bid = close - 0.01, ask = close, observed at bar end + 5 s)",
        "entry_ttl_minutes": int(ENTRY_TTL.total_seconds() // 60),
        "flatten_et": FLATTEN_TIME.strftime("%H:%M"),
        "notes": [*REPLAY_NOTES, *((GAP_REPLAY_NOTE,) if extended else ())],
        "bars": "extended hours (04:00 ET) for the session day" if extended else "regular hours",
        "sessions": [day.isoformat() for day in ordered_sessions],
    }
    return ReplayResult(
        strategy=name,
        strategy_kind=strategy.strategy_kind,
        sessions=ordered_sessions,
        symbols=len(symbols),
        symbol_days=symbol_days,
        trades=tuple(trades),
        unfilled=tuple(unfilled),
        outcomes=dict(sorted(outcomes.items())),
        skip_reasons=dict(sorted(skip_reasons.items(), key=lambda item: (-item[1], item[0]))),
        metadata=metadata,
    )


def result_stats(result: ReplayResult) -> StrategyStats:
    return summarize(
        result.strategy,
        result.trades,
        unfilled=len(result.unfilled),
        symbol_days=result.symbol_days,
        sessions=len(result.sessions),
    )


def result_payload(result: ReplayResult, *, generated_at: datetime) -> dict[str, Any]:
    return {
        "schema": REPLAY_SCHEMA,
        "version": REPLAY_VERSION,
        "generated_at": generated_at.astimezone(UTC).isoformat(),
        "status": "replayed",
        "metadata": dict(result.metadata),
        "coverage": {
            "sessions": len(result.sessions),
            "symbols": result.symbols,
            "symbol_days": result.symbol_days,
        },
        "stats": stats_payload(result_stats(result)),
        "outcomes": dict(result.outcomes),
        "skip_reasons": dict(result.skip_reasons),
        "trades": [trade.to_payload() for trade in result.trades],
        "unfilled": [item.to_payload() for item in result.unfilled],
    }


def _strategy_label(name: str) -> str:
    return f"{name.upper()} ({RULES_VERSIONS.get(name, '?')})"


def text_report(
    results: Sequence[ReplayResult], *, skipped: Sequence[SkippedStrategy] = ()
) -> str:
    sessions = sorted({day for result in results for day in result.sessions})
    span = f"{sessions[0].isoformat()}..{sessions[-1].isoformat()}" if sessions else "no sessions"
    symbols = max((result.symbols for result in results), default=0)
    labels = {result.strategy: _strategy_label(result.strategy) for result in results}
    extra = {
        result.strategy: (
            "  signals skipped (symbol-days): "
            + ", ".join(
                f"{reason} {count}" for reason, count in list(result.skip_reasons.items())[:4]
            ),
        )
        for result in results
        if result.skip_reasons
    }
    report = format_text_report(
        [result_stats(result) for result in results],
        title=f"Research replay {span}: {len(sessions)} sessions, {symbols} symbols",
        notes=(
            "Rule-only baseline: Claude review not simulated; synthetic one-tick quotes.",
            "All stored symbols, no position cap or loss stop; conservative bar fills;"
            " 15:45 ET flatten.",
        ),
        labels=labels,
        extra=extra,
    )
    sections = [report]
    for item in skipped:
        sections.append(f"{_strategy_label(item.strategy)}\n  not replayed: {item.note}")
    return "\n\n".join(sections)


def report_date(results: Sequence[ReplayResult], *, generated_at: datetime) -> date:
    """The reports' directory date: the newest replayed session."""
    sessions = sorted({day for result in results for day in result.sessions})
    return sessions[-1] if sessions else generated_at.date()


def write_reports(
    results: Sequence[ReplayResult],
    *,
    paths: ResearchPaths,
    generated_at: datetime,
    skipped: Sequence[SkippedStrategy] = (),
) -> tuple[str, ...]:
    """Private JSON per strategy plus one plain-text summary, under the newest session.

    A skipped strategy gets a ``"status": "skipped"`` JSON with its named
    reason, so its absence from the statistics is explicit, never silent.
    """
    as_of = report_date(results, generated_at=generated_at).isoformat()
    directory = paths.ensure_private(paths.reports_directory / as_of)
    written: list[str] = []
    payloads = [
        (result.strategy, result_payload(result, generated_at=generated_at))
        for result in results
    ]
    payloads += [
        (item.strategy, item.to_payload(generated_at=generated_at)) for item in skipped
    ]
    for strategy, payload in payloads:
        target = directory / f"replay-{strategy}.json"
        encoded = json.dumps(payload, indent=2, sort_keys=False)
        atomic_private_write(target, encoded.encode("utf-8"))
        written.append(str(target))
    summary = directory / "summary.txt"
    atomic_private_write(
        summary, (text_report(results, skipped=skipped) + "\n").encode("utf-8")
    )
    written.append(str(summary))
    return tuple(written)


def run_replay_cli(
    strategies: Sequence[str],
    *,
    notify: NotifyMode = "never",
    environment: NightlyEnvironment | None = None,
) -> int:
    """``autotraderctl research replay [--strategy ...] [--notify]``: rule-only replay.

    Without strategies every registered one is reported (GAP_GO as not
    replayable) and the nightly evidence run follows (``trading.research.nightly``:
    the signal-outcome tracker, the live R ledger, the paper promotion rule
    and the window research), its sections leading the summary. ``notify``
    hands the summary to ``report_notify`` after a successful replay
    ("weekly": only on the week's last session; "now": on demand). Telegram
    never changes a successful replay's exit code unless the owner asked for
    the message ("now") and it was not delivered.
    """
    full_run = not [name for name in strategies if name.strip()]
    names = [name.strip().lower() for name in strategies if name.strip()] or list(
        REGISTERED_STRATEGIES
    )
    known = (*STRATEGY_FACTORIES, *EXTENDED_BAR_STRATEGIES, *UNREPLAYABLE)
    unknown = sorted(set(names) - set(known))
    if unknown:
        print(
            f"error: unknown strategy {', '.join(unknown)}; choose from {', '.join(known)}",
            file=sys.stderr,
        )
        return 2
    store = BarsStore()
    stored = store.list_sessions()
    if not stored:
        print("error: no stored research bars; run the research backfill first", file=sys.stderr)
        return 1
    ordered = list(dict.fromkeys(names))
    has_extended = bool(store.list_sessions(extended=True))
    skipped = [
        UNREPLAYABLE[name] if name in UNREPLAYABLE else no_extended_bars(name)
        for name in ordered
        if name in UNREPLAYABLE or (name in EXTENDED_BAR_STRATEGIES and not has_extended)
    ]
    skipped_names = {item.strategy for item in skipped}
    results = [replay_strategy(name, store) for name in ordered if name not in skipped_names]
    generated_at = datetime.now(UTC)
    written = write_reports(results, paths=store.paths, generated_at=generated_at, skipped=skipped)
    report = text_report(results, skipped=skipped)
    summary = report
    if full_run:
        from trading.research import nightly, report_notify as summaries

        evidence = nightly.collect_evidence(
            results,
            skipped,
            store,
            environment=environment or nightly.NightlyEnvironment.current(),
        )
        summary = summaries.compose_summary(report, evidence)
        written += nightly.write_evidence(
            evidence,
            paths=store.paths,
            as_of=report_date(results, generated_at=generated_at),
            generated_at=generated_at,
            text=summaries.evidence_text(evidence),
        )
    print(summary)
    print("")
    print("reports:")
    for path in written:
        print(f"  {path}")
    if notify == "never":
        return 0
    from trading.research import report_notify

    outcome = report_notify.notify_summary(summary, mode=notify, newest_session=stored[-1])
    if outcome == report_notify.SENT:
        print("telegram: summary sent")
    elif notify == "now":
        print(
            f"telegram: summary NOT sent: {report_notify.OUTCOME_TEXT[outcome]}", file=sys.stderr
        )
        return 1
    elif outcome == report_notify.FAILED:
        # The replay itself succeeded: a restart would only redo it.
        print(
            f"telegram: weekly summary not sent: {report_notify.OUTCOME_TEXT[outcome]}",
            file=sys.stderr,
        )
    return 0


__all__ = [
    "ENTRY_TTL",
    "EXTENDED_BAR_STRATEGIES",
    "FILL_MODEL_ID",
    "FLATTEN_TIME",
    "GAP_REPLAY_NOTE",
    "NEEDS_EXTENDED_BARS",
    "POLL_DELAY",
    "REGISTERED_STRATEGIES",
    "REPLAY_NOTES",
    "RULES_VERSIONS",
    "STRATEGY_FACTORIES",
    "UNREPLAYABLE",
    "BracketOutcome",
    "ReplayResult",
    "SimulatedTrade",
    "SkippedStrategy",
    "UnfilledSetup",
    "UnreplayableStrategyError",
    "flatten_at",
    "no_extended_bars",
    "r_multiple",
    "replay_strategy",
    "report_date",
    "result_payload",
    "result_stats",
    "run_replay_cli",
    "simulate_bracket",
    "synthetic_quote",
    "text_report",
    "walk_day",
    "write_reports",
]
