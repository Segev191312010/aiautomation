"""Pure, long-only gap-and-go rules (GAP_GO_V1), a SHADOW strategy.

A stock that opens at least 3% above the prior close, after real pre-market
participation (pre-market share volume of at least 2% of its average daily
share volume), signals when a completed five-minute regular-session bar is the
first of the day to close above the trigger: the higher of the pre-market high
and the first five-minute bar's high. Only that first breakout may signal, and
only while it is the latest completed bar: it is priced at its own close or
never, and a later re-break is ignored.

The stop is the higher of the first bar's low and one ATR(14) below the entry,
rounded down to the tick; a stop closer than 0.5% or wider than 2.0% of the
entry is skipped. The target is 2R on the rounded stop. Signal bars must end
between 09:35 and 10:30 ET.

Inputs: earlier sessions' RTH bars (ATR history) plus every bar of the session
day including pre-market; ``gap_go_bars`` builds that from the two IBKR
requests. Integration note: candidates come from the 09:30 screener snapshot
rows with gap_pct >= 3 (the pre-market sweep).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import date, datetime, time, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, InvalidOperation

from trading.contracts import (
    Candidate,
    CompletedBar,
    QuoteSnapshot,
    RuleCondition,
    RuleSetup,
    RuleSetupProvenance,
    ScannerUniverse,
    StrategySpec,
    fingerprint_payload,
    require_aware,
)
from trading.rules.orb import (
    NEW_YORK,
    NO_SIGNAL_YET,
    QUOTE_MAX_AGE_SECONDS,
    QUOTE_REQUIRED,
    ORBConfig,
    ORBError,
    _ordered_bars,
    _round_to_tick,
    qualify_candidate,
    wilder_atr,
)
from trading.rules.strategy import (
    BAR_INTERVAL,
    NO_BARS_SUPPLIED,
    StrategyResult,
    is_data_rejection,
)

GAP_GO_STRATEGY_KIND = "gap_go"
# The rule-set identity behind ``GapGoConfig.fingerprint()`` and the catalogue name.
GAP_GO_RULES_VERSION = "GAP_GO_V1"
GAP_REVIEW_PROMPT_ID = "gap-review-v1"
NO_SIGNAL_BEFORE_CUTOFF = "no_signal_before_cutoff"
# Terminal: the open and the pre-market are over, so neither can improve.
GAP_TOO_SMALL = "gap_too_small"
PREMARKET_VOLUME_TOO_LOW = "premarket_volume_too_low"
# Retryable until the cutoff: the extended-hours bars may still arrive.
NO_PREMARKET_DATA = "no_premarket_data"
# Retryable skips of a priced signal; the candidate stays pending to the cutoff.
STOP_TOO_TIGHT = "stop_too_tight"
STOP_TOO_WIDE = "stop_too_wide"
UTC = timezone.utc
_PREMARKET_EARLIEST = time(4, 0)
_REGULAR_OPEN = time(9, 30)
_FIRST_BAR_CLOSE = time(9, 35)
# The earliest scheduled XNYS close (half days), so the window fits every session.
_EARLIEST_CLOSE = time(13, 0)
_BPS = Decimal("10000")
_PERCENT = Decimal("100")
_GAP_QUANTUM = Decimal("0.01")
_SHARE_QUANTUM = Decimal("1")


@dataclass(frozen=True, slots=True)
class GapGoConfig:
    window_start: time = _FIRST_BAR_CLOSE
    entry_cutoff: time = time(10, 30)
    premarket_start: time = _PREMARKET_EARLIEST
    # (first regular-session open - prior close) / prior close, in percent.
    min_gap_pct: Decimal = Decimal("3")
    # Pre-market shares as a percent of average daily shares
    # (average_dollar_volume / reference_price).
    min_premarket_volume_pct: Decimal = Decimal("2")
    atr_period: int = 14
    # Stop = max(first bar low, entry - stop_atr_multiple x ATR), floored to the
    # tick; a rounded distance outside [min_stop_pct, max_stop_pct] % of entry
    # is skipped.
    stop_atr_multiple: Decimal = Decimal("1")
    min_stop_pct: Decimal = Decimal("0.5")
    max_stop_pct: Decimal = Decimal("2.0")
    target_reward_risk: Decimal = Decimal("2")
    max_spread_bps: Decimal = Decimal("15")
    max_chase_bps: Decimal = Decimal("20")
    quote_max_age_seconds: Decimal = QUOTE_MAX_AGE_SECONDS
    # TWS reports intraday US-stock bar volume in round lots of 100 shares.
    volume_multiplier: Decimal = Decimal("100")
    minimum_price: Decimal = Decimal("10")
    maximum_price: Decimal = Decimal("300")
    minimum_average_dollar_volume: Decimal = Decimal("20000000")

    def __post_init__(self) -> None:
        if not _FIRST_BAR_CLOSE <= self.window_start < self.entry_cutoff <= _EARLIEST_CLOSE:
            raise ValueError("gap-and-go window must lie between 09:35 and 13:00 ET")
        if not _PREMARKET_EARLIEST <= self.premarket_start < _REGULAR_OPEN:
            raise ValueError("pre-market must start between 04:00 and 09:30 ET")
        if self.atr_period < 1:
            raise ValueError("atr_period must be positive")
        for name in (
            "min_gap_pct",
            "min_premarket_volume_pct",
            "stop_atr_multiple",
            "min_stop_pct",
            "max_stop_pct",
            "target_reward_risk",
            "max_spread_bps",
            "max_chase_bps",
            "quote_max_age_seconds",
            "volume_multiplier",
            "minimum_price",
            "maximum_price",
            "minimum_average_dollar_volume",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.min_gap_pct <= 0 or self.min_premarket_volume_pct <= 0:
            raise ValueError("a gap and pre-market participation are required")
        if self.stop_atr_multiple <= 0 or self.target_reward_risk < 2:
            raise ValueError("stop multiple must be positive and target must be at least 2R")
        if not 0 < self.min_stop_pct < self.max_stop_pct < _PERCENT:
            raise ValueError("stop bounds must satisfy 0 < min_stop_pct < max_stop_pct < 100")
        if (
            self.volume_multiplier <= 0
            or self.minimum_price <= 0
            or self.minimum_average_dollar_volume <= 0
        ):
            raise ValueError("volume multiplier, minimum price and dollar volume must be positive")
        if self.minimum_price >= self.maximum_price:
            raise ValueError("minimum price must be below maximum price")

    def fingerprint(self) -> str:
        """Identity of the exact rule parameters, for a run's policy fingerprints."""
        return fingerprint_payload(
            {
                "strategy_kind": GAP_GO_STRATEGY_KIND,
                "rules_version": GAP_GO_RULES_VERSION,
                **{item.name: str(getattr(self, item.name)) for item in fields(self)},
            }
        )


@dataclass(frozen=True, slots=True)
class _GapLevels:
    """Everything known once the first bar and the pre-market are complete."""

    first_bar: CompletedBar
    gap_pct: Decimal
    premarket_bars: tuple[CompletedBar, ...]
    premarket_high: Decimal
    # Shares (bar volume x volume_multiplier).
    premarket_volume: Decimal
    trigger: Decimal


@dataclass(frozen=True, slots=True)
class GapGoResult:
    """Evaluation of the latest completed session bar, with its gap inputs."""

    setup: RuleSetup | None
    rejection_reason: str | None = None
    retryable: bool = False
    evaluated_bar: CompletedBar | None = None
    breakout_bar: CompletedBar | None = None
    first_bar: CompletedBar | None = None
    gap_pct: Decimal | None = None
    premarket_high: Decimal | None = None
    premarket_volume: Decimal | None = None
    trigger: Decimal | None = None
    atr: Decimal | None = None

    @property
    def accepted(self) -> bool:
        return self.setup is not None

    def as_strategy_result(self) -> StrategyResult:
        return StrategyResult(
            setup=self.setup,
            rejection_reason=self.rejection_reason,
            retryable=self.retryable,
        )


def _at(session_date: date, local_time: time) -> datetime:
    return datetime.combine(session_date, local_time, tzinfo=NEW_YORK).astimezone(UTC)


def _local_date(bar: CompletedBar) -> date:
    return bar.interval_start_utc.astimezone(NEW_YORK).date()


def gap_go_entry_window(
    session_date: date, config: GapGoConfig = GapGoConfig()
) -> tuple[datetime, datetime]:
    """Return ``(start, cutoff)`` in UTC: signal bars must end inside it."""
    return _at(session_date, config.window_start), _at(session_date, config.entry_cutoff)


def gap_go_bars(
    regular: Sequence[CompletedBar],
    extended: Sequence[CompletedBar],
    *,
    session_date: date,
) -> tuple[CompletedBar, ...]:
    """The bar input GAP_GO_V1 expects, from the two IBKR requests.

    The all-hours request (``PaperIBKRMarketData.extended_bars``, "1 D")
    returns only the session day, so ATR(14) history before it comes from
    the RTH request (``completed_bars``, "2 D"). Every bar of
    ``session_date`` comes from the all-hours request alone, so the
    pre-market and regular-session bars are one response's view.
    """
    earlier = [bar for bar in regular if bar.rth and _local_date(bar) < session_date]
    today = [bar for bar in extended if _local_date(bar) == session_date]
    return tuple(sorted((*earlier, *today), key=lambda bar: bar.interval_start_utc))


def _qualification_config(config: GapGoConfig) -> ORBConfig:
    return ORBConfig(
        minimum_price=config.minimum_price,
        maximum_price=config.maximum_price,
        minimum_average_dollar_volume=config.minimum_average_dollar_volume,
    )


def _session_bars(
    completed: Sequence[CompletedBar], *, session_date: date, session_open: datetime
) -> list[CompletedBar]:
    return [
        bar
        for bar in completed
        if bar.rth and bar.interval_start_utc >= session_open and _local_date(bar) == session_date
    ]


def _premarket_bars(
    completed: Sequence[CompletedBar], *, premarket_open: datetime, session_open: datetime
) -> tuple[CompletedBar, ...]:
    return tuple(
        bar
        for bar in completed
        if not bar.rth
        and premarket_open <= bar.interval_start_utc
        and bar.interval_end_utc <= session_open
    )


def _is_contiguous(session_bars: Sequence[CompletedBar], session_open: datetime) -> bool:
    return all(
        bar.interval_start_utc == session_open + BAR_INTERVAL * index
        for index, bar in enumerate(session_bars)
    )


def _latest_bar_end(now: datetime, session_open: datetime) -> datetime:
    return session_open + BAR_INTERVAL * ((now - session_open) // BAR_INTERVAL)


def _rejected(
    reason: str,
    *,
    retryable: bool,
    bar: CompletedBar | None = None,
    first_bar: CompletedBar | None = None,
    gap_pct: Decimal | None = None,
    levels: _GapLevels | None = None,
    breakout: CompletedBar | None = None,
    atr: Decimal | None = None,
) -> GapGoResult:
    if levels is not None:
        first_bar, gap_pct = levels.first_bar, levels.gap_pct
    return GapGoResult(
        setup=None,
        rejection_reason=reason,
        retryable=retryable,
        evaluated_bar=bar,
        breakout_bar=breakout,
        first_bar=first_bar,
        gap_pct=gap_pct,
        premarket_high=None if levels is None else levels.premarket_high,
        premarket_volume=None if levels is None else levels.premarket_volume,
        trigger=None if levels is None else levels.trigger,
        atr=atr,
    )


def evaluate_gap_go(
    *,
    candidate: Candidate,
    bars: Sequence[CompletedBar],
    quote: QuoteSnapshot | None,
    session_date: date,
    run_id: str,
    strategy_id: str,
    strategy_version: int,
    reference_price: Decimal | int | str,
    average_dollar_volume: Decimal | int | str,
    candidate_input_fingerprint: str | None = None,
    now: datetime | None = None,
    config: GapGoConfig = GapGoConfig(),
) -> GapGoResult:
    """Evaluate the latest completed bar; a quote is consulted only on a signal."""
    try:
        rejection = qualify_candidate(
            candidate,
            reference_price=reference_price,
            average_dollar_volume=average_dollar_volume,
            config=_qualification_config(config),
        )
    except ORBError as exc:
        return _rejected(str(exc), retryable=False)
    if rejection:
        return _rejected(rejection, retryable=False)
    # Both are finite and positive once the candidate qualifies.
    prior_close = Decimal(str(reference_price))
    average_shares = Decimal(str(average_dollar_volume)) / prior_close
    now_utc = require_aware(now or datetime.now(UTC))
    window_start, cutoff = gap_go_entry_window(session_date, config)
    before_cutoff = now_utc < cutoff
    no_signal_reason = NO_SIGNAL_YET if before_cutoff else NO_SIGNAL_BEFORE_CUTOFF

    if not bars:
        return _rejected(NO_BARS_SUPPLIED, retryable=before_cutoff)
    try:
        ordered = _ordered_bars(bars)
    except ORBError:
        return _rejected("bar_sequence_invalid", retryable=before_cutoff)
    if any(bar.con_id != candidate.con_id for bar in ordered):
        return _rejected("bar_contract_mismatch", retryable=before_cutoff)

    completed = [bar for bar in ordered if bar.interval_end_utc <= now_utc]
    session_open = _at(session_date, _REGULAR_OPEN)
    session_bars = _session_bars(completed, session_date=session_date, session_open=session_open)
    if not session_bars:
        if now_utc < session_open + BAR_INTERVAL:
            return _rejected(no_signal_reason, retryable=before_cutoff)
        return _rejected("bar_session_incomplete", retryable=before_cutoff)
    # "First breakout" is only knowable from every bar since the open.
    if not _is_contiguous(session_bars, session_open):
        return _rejected("bar_session_incomplete", retryable=before_cutoff)
    latest = session_bars[-1]
    if before_cutoff and latest.interval_end_utc != _latest_bar_end(now_utc, session_open):
        # A lagging series would let an older bar signal as if it were current.
        return _rejected("bar_session_stale", retryable=True, bar=latest)

    first_bar = session_bars[0]
    gap_pct = (first_bar.open - prior_close) / prior_close * _PERCENT
    if gap_pct < config.min_gap_pct:
        return _rejected(
            GAP_TOO_SMALL, retryable=False, bar=latest, first_bar=first_bar, gap_pct=gap_pct
        )
    premarket = _premarket_bars(
        completed,
        premarket_open=_at(session_date, config.premarket_start),
        session_open=session_open,
    )
    if not premarket:
        return _rejected(
            NO_PREMARKET_DATA,
            retryable=before_cutoff,
            bar=latest,
            first_bar=first_bar,
            gap_pct=gap_pct,
        )
    premarket_high = max(bar.high for bar in premarket)
    levels = _GapLevels(
        first_bar=first_bar,
        gap_pct=gap_pct,
        premarket_bars=premarket,
        premarket_high=premarket_high,
        premarket_volume=sum((bar.volume for bar in premarket), Decimal("0"))
        * config.volume_multiplier,
        trigger=max(premarket_high, first_bar.high),
    )
    if levels.premarket_volume * _PERCENT < config.min_premarket_volume_pct * average_shares:
        return _rejected(PREMARKET_VOLUME_TOO_LOW, retryable=False, bar=latest, levels=levels)

    breakout = next((bar for bar in session_bars if bar.close > levels.trigger), None)
    if (
        breakout is None
        or breakout.interval_start_utc != latest.interval_start_utc
        or not window_start <= latest.interval_end_utc <= cutoff
    ):
        # No breakout yet, or the first one was an earlier bar: priced then or never.
        return _rejected(
            no_signal_reason, retryable=before_cutoff, bar=latest, levels=levels, breakout=breakout
        )
    if not before_cutoff:
        # Quotes are withheld at the cutoff: the signal was never priced in the window.
        return _rejected(
            QUOTE_REQUIRED if quote is None else "signal_after_cutoff",
            retryable=False,
            bar=latest,
            levels=levels,
            breakout=latest,
        )
    if quote is None:
        return _rejected(QUOTE_REQUIRED, retryable=True, bar=latest, levels=levels, breakout=latest)
    return _price_signal(
        candidate=candidate,
        signal=latest,
        levels=levels,
        completed=completed,
        quote=quote,
        run_id=run_id,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        reference_price=reference_price,
        average_dollar_volume=average_dollar_volume,
        candidate_input_fingerprint=candidate_input_fingerprint,
        now=now_utc,
        config=config,
    )


def _price_signal(
    *,
    candidate: Candidate,
    signal: CompletedBar,
    levels: _GapLevels,
    completed: Sequence[CompletedBar],
    quote: QuoteSnapshot,
    run_id: str,
    strategy_id: str,
    strategy_version: int,
    reference_price: Decimal | int | str,
    average_dollar_volume: Decimal | int | str,
    candidate_input_fingerprint: str | None,
    now: datetime,
    config: GapGoConfig,
) -> GapGoResult:
    """Turn the first breakout inside the window into a setup, or a reason there is none."""

    def skipped(reason: str, *, retryable: bool = True, atr: Decimal | None = None) -> GapGoResult:
        return _rejected(
            reason, retryable=retryable, bar=signal, levels=levels, breakout=signal, atr=atr
        )

    if quote.con_id != candidate.con_id:
        return skipped("quote_contract_mismatch")
    try:
        quote.require_fresh(now=now, max_age_seconds=config.quote_max_age_seconds)
    except (ValueError, TypeError):
        return skipped("quote_stale")
    if quote.spread_bps > config.max_spread_bps:
        return skipped("quote_spread_too_wide")

    atr: Decimal | None = None
    try:
        entry_limit = _round_to_tick(max(signal.close, quote.ask), quote.min_tick, ROUND_CEILING)
        if entry_limit > signal.close * (Decimal("1") + config.max_chase_bps / _BPS):
            return skipped("breakout_chase_too_wide")
        history = [
            bar
            for bar in completed
            if bar.rth and bar.interval_end_utc <= signal.interval_end_utc
        ]
        atr = wilder_atr(history, period=config.atr_period)
        stop_price = _round_to_tick(
            max(levels.first_bar.low, entry_limit - atr * config.stop_atr_multiple),
            quote.min_tick,
            ROUND_FLOOR,
        )
        stop_distance = entry_limit - stop_price
        # Bounds apply to the rounded stop that the bracket will carry.
        if stop_distance < entry_limit * config.min_stop_pct / _PERCENT:
            return skipped(STOP_TOO_TIGHT, atr=atr)
        if stop_distance > entry_limit * config.max_stop_pct / _PERCENT:
            return skipped(STOP_TOO_WIDE, atr=atr)
        target_price = _round_to_tick(
            entry_limit + stop_distance * config.target_reward_risk,
            quote.min_tick,
            ROUND_CEILING,
        )
        # Causal bars: the pre-market behind the trigger and volume, and every
        # RTH bar behind ATR (which includes the first bar and the signal).
        premarket_fingerprints = tuple(bar.fingerprint() for bar in levels.premarket_bars)
        atr_fingerprints = tuple(bar.fingerprint() for bar in history)
        signal_fingerprint = signal.fingerprint()
        quote_fingerprint = quote.fingerprint()
        input_fingerprint = candidate_input_fingerprint or fingerprint_payload(
            {
                "candidate": fingerprint_payload(candidate),
                "reference_price": str(reference_price),
                "average_dollar_volume": str(average_dollar_volume),
            }
        )
        setup = RuleSetup(
            run_id=run_id,
            candidate_id=candidate.id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            strategy_kind=GAP_GO_STRATEGY_KIND,
            symbol=candidate.symbol,
            atr=atr,
            entry_limit=entry_limit,
            stop_price=stop_price,
            target_price=target_price,
            levels={
                "gap_pct": levels.gap_pct.quantize(_GAP_QUANTUM, rounding=ROUND_HALF_EVEN),
                "premarket_high": levels.premarket_high,
                "premarket_volume": levels.premarket_volume.quantize(
                    _SHARE_QUANTUM, rounding=ROUND_HALF_EVEN
                ),
                "first_bar_high": levels.first_bar.high,
                "first_bar_low": levels.first_bar.low,
                "trigger": levels.trigger,
                "stop_distance": stop_distance,
            },
            bar_fingerprints=tuple(
                dict.fromkeys((*premarket_fingerprints, *atr_fingerprints, signal_fingerprint))
            ),
            quote_fingerprint=quote_fingerprint,
            causal_provenance=RuleSetupProvenance(
                atr_bar_fingerprints=atr_fingerprints,
                breakout_bar_fingerprint=signal_fingerprint,
                quote_fingerprint=quote_fingerprint,
                candidate_input_fingerprint=input_fingerprint,
            ),
            detected_at=signal.interval_end_utc,
        )
    except (ORBError, ValueError, InvalidOperation) as exc:
        reason = str(exc) or "gap_go_setup_invalid"
        return skipped(reason, retryable=is_data_rejection(reason), atr=atr)
    return GapGoResult(
        setup=setup,
        evaluated_bar=signal,
        breakout_bar=signal,
        first_bar=levels.first_bar,
        gap_pct=levels.gap_pct,
        premarket_high=levels.premarket_high,
        premarket_volume=levels.premarket_volume,
        trigger=levels.trigger,
        atr=atr,
    )


class GapGoStrategy:
    """GAP_GO_V1 behind the strategy boundary.

    The window ends by 13:00 ET (enforced by ``GapGoConfig``), so a half day
    needs no calendar lookup.
    """

    __slots__ = ("_config",)

    def __init__(self, config: GapGoConfig = GapGoConfig()) -> None:
        self._config = config

    @property
    def strategy_kind(self) -> str:
        return GAP_GO_STRATEGY_KIND

    @property
    def config(self) -> GapGoConfig:
        return self._config

    def entry_window(self, session_date: date) -> tuple[datetime, datetime]:
        return gap_go_entry_window(session_date, self._config)

    def evaluate(
        self,
        *,
        candidate: Candidate,
        bars: Sequence[CompletedBar],
        quote: QuoteSnapshot | None,
        session_date: date,
        run_id: str,
        strategy_id: str,
        strategy_version: int,
        reference_price: Decimal | int | str,
        average_dollar_volume: Decimal | int | str,
        candidate_input_fingerprint: str | None,
        now: datetime,
    ) -> StrategyResult:
        return evaluate_gap_go(
            candidate=candidate,
            bars=bars,
            quote=quote,
            session_date=session_date,
            run_id=run_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            reference_price=reference_price,
            average_dollar_volume=average_dollar_volume,
            candidate_input_fingerprint=candidate_input_fingerprint,
            now=now,
            config=self._config,
        ).as_strategy_result()


def gap_go_strategy_spec() -> StrategySpec:
    """GAP_GO_V1's catalogue entry; the executable rules are ``GapGoConfig``."""
    return StrategySpec(
        name=GAP_GO_RULES_VERSION,
        universe=ScannerUniverse(
            scanner_policy_id="screener-premarket-gappers",
            scanner_policy_version=1,
            scanner_policy_hash=fingerprint_payload(
                {"source": "screener_snapshot", "at": "09:30", "min_gap_pct": "3"}
            ),
        ),
        conditions=(RuleCondition(feature="close", operator="gte", value=Decimal("10")),),
        rationale=(
            "Long-only gap and go: the first 5-minute bar opens at least 3% above the "
            "prior close after pre-market volume of at least 2% of average daily shares; "
            "the first 5-minute close above the higher of the pre-market high and the "
            "first bar's high signals, for bars ending 09:35-10:30 ET, and a later "
            "re-break never does. Stop: the higher of the first bar's low and one "
            "ATR(14) below entry, skipped when under 0.5% or over 2.0% of entry; 2R "
            "target. SHADOW only. Authoritative rules: trading/rules/gap_go.py GapGoConfig."
        ),
    )


# Registered by the integrator in trading/intelligence/prompts.py under
# GAP_REVIEW_PROMPT_ID; changed wording needs a new prompt id, never an edit.
GAP_REVIEW_V1 = """\
You review one long-only gap-and-go setup (strategy_kind "gap_go", rules \
GAP_GO_V1) for a US common stock early in regular trading hours. \
Deterministic code has already detected the signal: the session's first \
five-minute bar opened at least 3% above the prior close, pre-market volume \
(04:00 to 09:30 ET) was at least 2% of the stock's average daily share volume, \
and the latest completed five-minute bar, ending between 09:35 and 10:30 ET, is \
the first bar of the session to close above the trigger, the higher of the \
pre-market high and the first five-minute bar's high. A later re-break never \
signals. Risk sizing, account limits, and order submission are owned by \
deterministic code and are not your concern.

Code computed the entry limit as the higher of the signal bar's close and the \
ask, rounded up to the tick and at most 0.2% above that close. The stop is the \
higher of the first five-minute bar's low and the entry minus one ATR(14) on \
five-minute bars, rounded down to the tick, and the target sits twice the \
rounded stop distance above the entry (2R). Code skips any signal whose stop \
distance is below 0.5% or above 2.0% of the entry, so every setup you see \
passed both checks.

This setup has no opening range. Its levels are reference values only: gap_pct \
is the first bar's open above the prior close, in percent; premarket_high is \
the highest pre-market trade; premarket_volume is the shares traded before the \
open; first_bar_high and first_bar_low are the first five-minute bar's range; \
trigger is the breakout level; stop_distance is entry_limit minus stop_price, \
per share.

The request may also carry market_context, a snapshot of the broad market at \
review time: percent changes on the day for SPY, QQQ, IWM and DIA, breadth \
(the share of stocks trading up, advancers, decliners, and the median change), \
the leading and lagging sectors, a regime of risk_on, neutral or risk_off, and \
a one-line summary. Treat a weak or risk-off market as a reason for caution: \
when the setup is marginal, ABSTAIN. When market_context is absent or \
{"available": false}, judge the setup on its own.

Decide PROPOSE if the setup is a reasonable trade as specified, or ABSTAIN if \
anything about it looks unsound: for example a gap so large that the target \
needs an implausible further move, an entry stretched far above the trigger, \
pre-market volume too thin to support the gap, a first bar whose range dwarfs \
the stop distance, or internally inconsistent prices or levels. When the \
evidence is ambiguous, ABSTAIN.

You cannot change any price, quantity, symbol, or order field. Respond only with \
the requested JSON: decision, confidence between 0 and 1, and a short rationale \
that names the specific values behind your decision."""


__all__ = [
    "GAP_GO_RULES_VERSION",
    "GAP_GO_STRATEGY_KIND",
    "GAP_REVIEW_PROMPT_ID",
    "GAP_REVIEW_V1",
    "GAP_TOO_SMALL",
    "NO_PREMARKET_DATA",
    "NO_SIGNAL_BEFORE_CUTOFF",
    "PREMARKET_VOLUME_TOO_LOW",
    "STOP_TOO_TIGHT",
    "STOP_TOO_WIDE",
    "GapGoConfig",
    "GapGoResult",
    "GapGoStrategy",
    "evaluate_gap_go",
    "gap_go_bars",
    "gap_go_entry_window",
    "gap_go_strategy_spec",
]
