"""Pure, long-only VWAP pullback rules on a trend day (VWAP_PB_V1).

A trend day is a session whose latest close is at least 1% above the previous
session's close and whose five-minute bars mostly (60%) closed above their
running VWAP. On such a day the strategy buys the reclaim after a pullback to
VWAP: within the four bars before the signal bar, the lowest low touched VWAP
without breaking down (no more than 0.5 ATR below and no more than 0.25 ATR
above the VWAP of the bar that made it), and the latest completed bar closes
above VWAP and above the previous bar's high on a bar-volume surge. Only that
latest bar may signal: a reclaim is priced at its own bar close or never.

The stop sits one tenth of an ATR below the pullback low, so a retest of the
low that holds does not stop the trade out. A stop nearer than 0.5% or wider
than 1.5% of entry is skipped for now, and a later bar may still qualify.

VWAP_PB_V1 is a shadow strategy: its catalogue name is not a MOMO_HOD name, so
the PAPER bridge never sends its orders.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import date, datetime, time, timedelta, timezone
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
from trading.rules.momo import (
    NO_SIGNAL_BEFORE_CUTOFF,
    SessionCloseLookup,
    _at,
    _is_contiguous,
    _latest_bar_end,
    _session_bars,
    xnys_session_close,
)
from trading.rules.orb import (
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

VWAP_PB_STRATEGY_KIND = "vwap_pb"
# The rule-set identity behind ``VwapPbConfig.fingerprint()``; the strategy
# catalogue names the strategy after it.
VWAP_PB_RULES_VERSION = "VWAP_PB_V1"
VWAP_PB_STRATEGY_NAME = VWAP_PB_RULES_VERSION
VWAP_REVIEW_PROMPT_ID = "vwap-review-v1"

# Retryable skips of a priced signal: a later bar may still qualify, so the
# candidate stays pending until the entry cutoff.
ENTRY_CHASE_TOO_WIDE = "entry_chase_too_wide"
STOP_TOO_TIGHT = "stop_too_tight"
STOP_TOO_WIDE = "stop_too_wide"

# Why the latest bar did not signal (``VwapPbResult.unmet``), in check order.
# The pipeline sees each of them only as NO_SIGNAL_YET (or, at the cutoff,
# NO_SIGNAL_BEFORE_CUTOFF).
OUTSIDE_ENTRY_WINDOW = "outside_entry_window"
DAY_CHANGE_TOO_LOW = "day_change_too_low"
TOO_FEW_BARS_ABOVE_VWAP = "too_few_bars_above_vwap"
CLOSE_NOT_ABOVE_VWAP = "close_not_above_vwap"
CLOSE_NOT_ABOVE_PREVIOUS_HIGH = "close_not_above_previous_high"
SIGNAL_VOLUME_TOO_LOW = "signal_volume_too_low"
PULLBACK_BROKE_DOWN = "pullback_broke_down"
PULLBACK_MISSED_VWAP = "pullback_missed_vwap"

UTC = timezone.utc
_REGULAR_OPEN = time(9, 30)
_REGULAR_CLOSE = time(16, 0)
# Any fixed day: config validation compares wall-clock times on it.
_ANCHOR_DAY = date(2000, 1, 3)
_BPS = Decimal("10000")
_PERCENT = Decimal("100")
_VWAP_QUANTUM = Decimal("0.0001")
_PCT_QUANTUM = Decimal("0.01")
_SHARE_QUANTUM = Decimal("1")


@dataclass(frozen=True, slots=True)
class VwapPbConfig:
    window_start: time = time(10, 30)
    entry_cutoff: time = time(14, 30)
    # On an early close the cutoff moves in so entries keep this much runway.
    close_buffer: timedelta = timedelta(hours=1)
    atr_period: int = 14
    # Trend day: the latest close at least min_day_change_pct % above the
    # reference (previous) close, and at least min_pct_bars_above_vwap % of
    # the session's bars closed above their running VWAP.
    min_day_change_pct: Decimal = Decimal("1")
    min_pct_bars_above_vwap: Decimal = Decimal("60")
    # Pullback: the lowest low of the pullback_bars bars before the signal bar
    # lies in [VWAP - floor x ATR, VWAP + ceiling x ATR], VWAP at that bar.
    pullback_bars: int = 4
    pullback_floor_atr: Decimal = Decimal("0.5")
    pullback_ceiling_atr: Decimal = Decimal("0.25")
    # Signal bar volume against the mean volume of the session's earlier bars.
    bar_volume_multiple: Decimal = Decimal("1.2")
    # Stop = pullback low - stop_buffer_atr x ATR, rounded down to the tick; a
    # stop distance outside [min_stop_pct, max_stop_pct] % of entry is skipped.
    stop_buffer_atr: Decimal = Decimal("0.1")
    min_stop_pct: Decimal = Decimal("0.5")
    max_stop_pct: Decimal = Decimal("1.5")
    target_reward_risk: Decimal = Decimal("2")
    max_spread_bps: Decimal = Decimal("15")
    max_chase_bps: Decimal = Decimal("20")
    quote_max_age_seconds: Decimal = QUOTE_MAX_AGE_SECONDS
    # TWS reports intraday US-stock bar volume in round lots of 100 shares;
    # VWAP is unaffected, the volume levels are reported in shares.
    volume_multiplier: Decimal = Decimal("100")
    minimum_price: Decimal = Decimal("10")
    maximum_price: Decimal = Decimal("300")
    minimum_average_dollar_volume: Decimal = Decimal("20000000")

    def __post_init__(self) -> None:
        if self.atr_period < 1 or self.pullback_bars < 1:
            raise ValueError("atr_period and pullback_bars must be positive")
        # The first signal bar needs its whole pullback lookback, plus the
        # opening bar, inside the session.
        earliest_signal = datetime.combine(_ANCHOR_DAY, _REGULAR_OPEN) + BAR_INTERVAL * (
            self.pullback_bars + 1
        )
        if not (
            earliest_signal
            <= datetime.combine(_ANCHOR_DAY, self.window_start)
            < datetime.combine(_ANCHOR_DAY, self.entry_cutoff)
            <= datetime.combine(_ANCHOR_DAY, _REGULAR_CLOSE)
        ):
            raise ValueError(
                "VWAP_PB window must leave room for the pullback lookback and start "
                "before its cutoff inside regular hours"
            )
        if self.close_buffer < timedelta(0):
            raise ValueError("close_buffer must not be negative")
        for name in (
            "min_day_change_pct",
            "min_pct_bars_above_vwap",
            "pullback_floor_atr",
            "pullback_ceiling_atr",
            "bar_volume_multiple",
            "stop_buffer_atr",
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
        if self.min_pct_bars_above_vwap > _PERCENT:
            raise ValueError("min_pct_bars_above_vwap must not exceed 100")
        if self.target_reward_risk < 2:
            raise ValueError("target must be at least 2R")
        # A positive floor keeps the stop below the entry; a cap below 100%
        # keeps it above zero; a floor at or above the cap skips every signal.
        if not Decimal("0") < self.min_stop_pct < self.max_stop_pct < _PERCENT:
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
                "strategy_kind": VWAP_PB_STRATEGY_KIND,
                "rules_version": VWAP_PB_RULES_VERSION,
                **{item.name: str(getattr(self, item.name)) for item in fields(self)},
            }
        )


@dataclass(frozen=True, slots=True)
class VwapPbResult:
    """Evaluation of the latest completed session bar, with its signal inputs."""

    setup: RuleSetup | None
    rejection_reason: str | None = None
    retryable: bool = False
    # The first rule the latest bar failed when it did not signal.
    unmet: str | None = None
    evaluated_bar: CompletedBar | None = None
    vwap: Decimal | None = None
    avg_bar_volume: Decimal | None = None
    day_change_pct: Decimal | None = None
    pct_bars_above_vwap: Decimal | None = None
    pullback_low: Decimal | None = None
    pullback_vwap: Decimal | None = None
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


@dataclass(frozen=True, slots=True)
class _SessionMetrics:
    vwap: Decimal
    earlier_volume: Decimal
    earlier_bars: int
    bars_above_vwap: int
    session_bars: int
    day_change_pct: Decimal
    pullback: CompletedBar
    pullback_vwap: Decimal

    @property
    def avg_bar_volume(self) -> Decimal:
        return self.earlier_volume / Decimal(self.earlier_bars)

    @property
    def pct_bars_above_vwap(self) -> Decimal:
        return Decimal(self.bars_above_vwap) * _PERCENT / Decimal(self.session_bars)


def vwap_pb_entry_window(
    session_date: date,
    config: VwapPbConfig = VwapPbConfig(),
    *,
    session_close: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Return ``(start, cutoff)`` in UTC: signal bars must end inside it."""
    start = _at(session_date, config.window_start)
    cutoff = _at(session_date, config.entry_cutoff)
    if session_close is not None:
        cutoff = min(cutoff, require_aware(session_close) - config.close_buffer)
    # An early close before the window opens leaves an empty window, never an inverted one.
    return start, max(start, cutoff)


def _qualification_config(config: VwapPbConfig) -> ORBConfig:
    return ORBConfig(
        minimum_price=config.minimum_price,
        maximum_price=config.maximum_price,
        minimum_average_dollar_volume=config.minimum_average_dollar_volume,
    )


def _running_vwap(session_bars: Sequence[CompletedBar]) -> list[Decimal | None]:
    """Session VWAP (typical price) at each bar's close; None before any volume."""
    values: list[Decimal | None] = []
    volume = Decimal("0")
    typical_volume = Decimal("0")
    for bar in session_bars:
        volume += bar.volume
        typical_volume += (bar.high + bar.low + bar.close) * bar.volume
        values.append(typical_volume / (Decimal("3") * volume) if volume > 0 else None)
    return values


def _session_metrics(
    session_bars: Sequence[CompletedBar], *, reference_price: Decimal, pullback_bars: int
) -> _SessionMetrics | None:
    """Trend, pullback and volume inputs through the latest bar; None without volume."""
    signal = session_bars[-1]
    earlier = session_bars[:-1]
    earlier_volume = sum((bar.volume for bar in earlier), Decimal("0"))
    running = _running_vwap(session_bars)
    # On a tie the most recent lowest low: the bar the signal reclaims from.
    lookback = range(len(session_bars) - 1 - pullback_bars, len(session_bars) - 1)
    pullback_index = max(lookback, key=lambda index: (-session_bars[index].low, index))
    vwap, pullback_vwap = running[-1], running[pullback_index]
    if earlier_volume <= 0 or vwap is None or pullback_vwap is None:
        return None
    return _SessionMetrics(
        vwap=vwap,
        earlier_volume=earlier_volume,
        earlier_bars=len(earlier),
        # A bar with no VWAP yet (no volume so far) never counts as above it.
        bars_above_vwap=sum(
            1
            for bar, bar_vwap in zip(session_bars, running)
            if bar_vwap is not None and bar.close > bar_vwap
        ),
        session_bars=len(session_bars),
        day_change_pct=(signal.close - reference_price) * _PERCENT / reference_price,
        pullback=session_bars[pullback_index],
        pullback_vwap=pullback_vwap,
    )


def _unmet_trend_or_trigger(
    signal: CompletedBar,
    previous: CompletedBar,
    metrics: _SessionMetrics,
    *,
    reference_price: Decimal,
    config: VwapPbConfig,
) -> str | None:
    """The first unmet trend-day or signal-bar rule.

    The day-change, trend-share and volume thresholds are cross-multiplied so
    a value exactly at a threshold is never misjudged by a rounded quotient.
    """
    if signal.close * _PERCENT < reference_price * (_PERCENT + config.min_day_change_pct):
        return DAY_CHANGE_TOO_LOW
    if Decimal(metrics.bars_above_vwap) * _PERCENT < config.min_pct_bars_above_vwap * Decimal(
        metrics.session_bars
    ):
        return TOO_FEW_BARS_ABOVE_VWAP
    if signal.close <= metrics.vwap:
        return CLOSE_NOT_ABOVE_VWAP
    if signal.close <= previous.high:
        return CLOSE_NOT_ABOVE_PREVIOUS_HIGH
    if signal.volume * Decimal(metrics.earlier_bars) < (
        config.bar_volume_multiple * metrics.earlier_volume
    ):
        return SIGNAL_VOLUME_TOO_LOW
    return None


def _unmet_pullback(metrics: _SessionMetrics, *, atr: Decimal, config: VwapPbConfig) -> str | None:
    low = metrics.pullback.low
    if low < metrics.pullback_vwap - atr * config.pullback_floor_atr:
        return PULLBACK_BROKE_DOWN
    if low > metrics.pullback_vwap + atr * config.pullback_ceiling_atr:
        return PULLBACK_MISSED_VWAP
    return None


def _result(
    *,
    setup: RuleSetup | None = None,
    reason: str | None = None,
    retryable: bool = False,
    unmet: str | None = None,
    bar: CompletedBar | None = None,
    metrics: _SessionMetrics | None = None,
    atr: Decimal | None = None,
) -> VwapPbResult:
    return VwapPbResult(
        setup=setup,
        rejection_reason=reason,
        retryable=retryable,
        unmet=unmet,
        evaluated_bar=bar,
        vwap=None if metrics is None else metrics.vwap,
        avg_bar_volume=None if metrics is None else metrics.avg_bar_volume,
        day_change_pct=None if metrics is None else metrics.day_change_pct,
        pct_bars_above_vwap=None if metrics is None else metrics.pct_bars_above_vwap,
        pullback_low=None if metrics is None else metrics.pullback.low,
        pullback_vwap=None if metrics is None else metrics.pullback_vwap,
        atr=atr,
    )


def _rejected(
    reason: str,
    *,
    retryable: bool,
    unmet: str | None = None,
    bar: CompletedBar | None = None,
    metrics: _SessionMetrics | None = None,
    atr: Decimal | None = None,
) -> VwapPbResult:
    return _result(
        reason=reason, retryable=retryable, unmet=unmet, bar=bar, metrics=metrics, atr=atr
    )


def evaluate_vwap_pb(
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
    config: VwapPbConfig = VwapPbConfig(),
    session_close: datetime | None = None,
) -> VwapPbResult:
    """Evaluate the latest completed bar; a quote is consulted only on a signal.

    ``reference_price`` is the previous session's close, so the trend-day rule
    measures the day's change against it.
    """
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
    now_utc = require_aware(now or datetime.now(UTC))
    window_start, cutoff = vwap_pb_entry_window(session_date, config, session_close=session_close)
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
    # VWAP, the trend share and the pullback are cumulative; a missing
    # interval would misstate all three.
    if not _is_contiguous(session_bars, session_open):
        return _rejected("bar_session_incomplete", retryable=before_cutoff)
    signal = session_bars[-1]
    if before_cutoff and signal.interval_end_utc != _latest_bar_end(now_utc, session_open):
        # A lagging series would let an older bar signal as if it were current.
        return _rejected("bar_session_stale", retryable=True, bar=signal)
    # The validated window always leaves the pullback lookback; the length
    # check only keeps a short series from ever shrinking it.
    if (
        not window_start <= signal.interval_end_utc <= cutoff
        or len(session_bars) <= config.pullback_bars
    ):
        return _rejected(
            no_signal_reason, retryable=before_cutoff, unmet=OUTSIDE_ENTRY_WINDOW, bar=signal
        )
    reference = Decimal(str(reference_price))
    metrics = _session_metrics(
        session_bars, reference_price=reference, pullback_bars=config.pullback_bars
    )
    if metrics is None:
        return _rejected("bar_volume_unavailable", retryable=before_cutoff, bar=signal)
    unmet = _unmet_trend_or_trigger(
        signal, session_bars[-2], metrics, reference_price=reference, config=config
    )
    if unmet is not None:
        return _rejected(
            no_signal_reason, retryable=before_cutoff, unmet=unmet, bar=signal, metrics=metrics
        )
    # The pullback band is measured in ATRs, so ATR is needed before any quote.
    # Every session bar behind VWAP, volume and the pullback is RTH history up
    # to the signal bar, together with the earlier sessions' bars.
    history = [
        bar for bar in completed if bar.rth and bar.interval_end_utc <= signal.interval_end_utc
    ]
    try:
        atr = wilder_atr(history, period=config.atr_period)
    except ORBError as exc:
        reason = str(exc) or "atr_unavailable"
        return _rejected(
            reason,
            retryable=before_cutoff and is_data_rejection(reason),
            bar=signal,
            metrics=metrics,
        )
    unmet = _unmet_pullback(metrics, atr=atr, config=config)
    if unmet is not None:
        return _rejected(
            no_signal_reason,
            retryable=before_cutoff,
            unmet=unmet,
            bar=signal,
            metrics=metrics,
            atr=atr,
        )
    if not before_cutoff:
        # Quotes are withheld at the cutoff: the signal was never priced in the window.
        return _rejected(
            QUOTE_REQUIRED if quote is None else "signal_after_cutoff",
            retryable=False,
            bar=signal,
            metrics=metrics,
            atr=atr,
        )
    if quote is None:
        return _rejected(QUOTE_REQUIRED, retryable=True, bar=signal, metrics=metrics, atr=atr)
    return _price_signal(
        candidate=candidate,
        signal=signal,
        metrics=metrics,
        atr=atr,
        history=history,
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
    metrics: _SessionMetrics,
    atr: Decimal,
    history: Sequence[CompletedBar],
    quote: QuoteSnapshot,
    run_id: str,
    strategy_id: str,
    strategy_version: int,
    reference_price: Decimal | int | str,
    average_dollar_volume: Decimal | int | str,
    candidate_input_fingerprint: str | None,
    now: datetime,
    config: VwapPbConfig,
) -> VwapPbResult:
    """Turn a signal inside the window into a setup, or a reason there is none."""

    def skip(reason: str, *, retryable: bool) -> VwapPbResult:
        return _rejected(reason, retryable=retryable, bar=signal, metrics=metrics, atr=atr)

    if quote.con_id != candidate.con_id:
        return skip("quote_contract_mismatch", retryable=True)
    try:
        quote.require_fresh(now=now, max_age_seconds=config.quote_max_age_seconds)
    except (ValueError, TypeError):
        return skip("quote_stale", retryable=True)
    if quote.spread_bps > config.max_spread_bps:
        return skip("quote_spread_too_wide", retryable=True)

    try:
        entry_limit = _round_to_tick(max(signal.close, quote.ask), quote.min_tick, ROUND_CEILING)
        if entry_limit > signal.close * (Decimal("1") + config.max_chase_bps / _BPS):
            # Retryable: a later bar can signal again from its own close.
            return skip(ENTRY_CHASE_TOO_WIDE, retryable=True)
        stop_price = _round_to_tick(
            metrics.pullback.low - atr * config.stop_buffer_atr, quote.min_tick, ROUND_FLOOR
        )
        # Both bounds apply to the tick-rounded distance the bracket would carry;
        # both skips are retryable because a later reclaim may size differently.
        stop_distance = entry_limit - stop_price
        if stop_distance < entry_limit * config.min_stop_pct / _PERCENT:
            return skip(STOP_TOO_TIGHT, retryable=True)
        if stop_distance > entry_limit * config.max_stop_pct / _PERCENT:
            return skip(STOP_TOO_WIDE, retryable=True)
        # 2R on the tick-rounded stop, so the bracket never falls below the
        # risk policy's minimum reward-to-risk after rounding.
        target_price = _round_to_tick(
            entry_limit + stop_distance * config.target_reward_risk,
            quote.min_tick,
            ROUND_CEILING,
        )
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
            strategy_kind=VWAP_PB_STRATEGY_KIND,
            symbol=candidate.symbol,
            atr=atr,
            entry_limit=entry_limit,
            stop_price=stop_price,
            target_price=target_price,
            levels={
                "vwap": metrics.vwap.quantize(_VWAP_QUANTUM, rounding=ROUND_HALF_EVEN),
                "pullback_low": metrics.pullback.low,
                "day_change_pct": metrics.day_change_pct.quantize(
                    _PCT_QUANTUM, rounding=ROUND_HALF_EVEN
                ),
                "pct_bars_above_vwap": metrics.pct_bars_above_vwap.quantize(
                    _PCT_QUANTUM, rounding=ROUND_HALF_EVEN
                ),
                "signal_bar_volume": signal.volume * config.volume_multiplier,
                "avg_bar_volume": (metrics.avg_bar_volume * config.volume_multiplier).quantize(
                    _SHARE_QUANTUM, rounding=ROUND_HALF_EVEN
                ),
                "stop_distance": stop_distance,
            },
            bar_fingerprints=tuple(dict.fromkeys((*atr_fingerprints, signal_fingerprint))),
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
        reason = str(exc) or "vwap_pb_setup_invalid"
        return skip(reason, retryable=is_data_rejection(reason))
    return _result(setup=setup, bar=signal, metrics=metrics, atr=atr)


class VwapPbStrategy:
    """VWAP_PB_V1 behind the strategy boundary.

    The XNYS calendar supplies the scheduled close by default, so a half day
    moves the cutoff in rather than letting entries run into the close.
    """

    __slots__ = ("_config", "_session_close")

    def __init__(
        self,
        config: VwapPbConfig = VwapPbConfig(),
        *,
        session_close: SessionCloseLookup = xnys_session_close,
    ) -> None:
        self._config = config
        self._session_close = session_close

    @property
    def strategy_kind(self) -> str:
        return VWAP_PB_STRATEGY_KIND

    @property
    def config(self) -> VwapPbConfig:
        return self._config

    def entry_window(self, session_date: date) -> tuple[datetime, datetime]:
        return vwap_pb_entry_window(
            session_date, self._config, session_close=self._session_close(session_date)
        )

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
        return evaluate_vwap_pb(
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
            session_close=self._session_close(session_date),
        ).as_strategy_result()


def vwap_pb_strategy_spec() -> StrategySpec:
    """VWAP_PB_V1's catalogue entry; the executable rules are ``VwapPbConfig``."""
    return StrategySpec(
        name=VWAP_PB_STRATEGY_NAME,
        universe=ScannerUniverse(
            scanner_policy_id="owner-watchlist-hotlist",
            scanner_policy_version=1,
            scanner_policy_hash=fingerprint_payload({"source": "screener_hotlist", "top": 35}),
        ),
        conditions=(RuleCondition(feature="close", operator="gte", value=Decimal("10")),),
        rationale=(
            "Shadow only. Long-only VWAP pullback on a trend day: the latest close is at "
            "least 1% above the previous close and at least 60% of the session's 5-minute "
            "bars closed above their running VWAP. Within the 4 bars before the signal bar "
            "the lowest low held between 0.5 ATR(14) below and 0.25 ATR above that bar's "
            "VWAP; the signal bar, ending 10:30-14:30 ET, closes above VWAP and the previous "
            "bar's high on at least 1.2x the session's average bar volume. Stop 0.1 ATR "
            "below the pullback low; a signal is skipped, and may retry on a later bar, when "
            "that stop is nearer than 0.5% or wider than 1.5% of entry; 2R target. "
            "Authoritative rules: trading/rules/vwap_pb.py VwapPbConfig."
        ),
    )


# Review prompt for VWAP_PB_V1 setups, registered as ``vwap-review-v1``. Once a
# recorded review names that id this text must never change; new wording
# needs a new prompt id.
VWAP_REVIEW_V1 = """\
You review one long-only intraday VWAP pullback setup (strategy_kind "vwap_pb", \
rules VWAP_PB_V1) for a US common stock during regular trading hours. \
Deterministic code has already detected the signal on the latest completed \
five-minute bar of a trend day: the signal bar closed at least 1% above the \
previous session's close, and at least 60% of the session's bars closed above \
the session VWAP as it stood at their close. Within the four bars before the \
signal bar, price pulled back to VWAP without breaking down: their lowest low \
sat no more than 0.5 ATR(14) below and no more than 0.25 ATR above the VWAP of \
the bar that made it. The signal bar then closed above VWAP and above the \
previous bar's high, on volume at least 1.2 times the mean volume of the \
session's earlier bars. Risk sizing, account limits, and order submission are \
owned by deterministic code and are not your concern.

Code computed the entry limit from the signal bar's close and the ask, then \
the stop one tenth of an ATR(14) on five-minute bars below the pullback low, \
rounded down to the tick, and the target twice the rounded stop distance above \
the entry (2R). Code skips any signal whose stop distance is below 0.5% or \
above 1.5% of the entry, so every setup you see passed both checks.

This setup has no opening range. Its levels are reference values only: vwap \
is the session VWAP through the signal bar; pullback_low is the lowest low of \
the four bars before the signal bar; day_change_pct is the signal bar's close \
against the previous session's close, in percent; pct_bars_above_vwap is the \
percentage of the session's bars, through the signal bar, that closed above \
VWAP; signal_bar_volume and avg_bar_volume are the signal bar's volume and the \
mean volume of the session's earlier bars, in shares; stop_distance is \
entry_limit minus stop_price, per share.

The request may also carry market_context, a snapshot of the broad market at \
review time: percent changes on the day for SPY, QQQ, IWM and DIA, breadth, the \
leading and lagging sectors, a regime of risk_on, neutral or risk_off, and a \
one-line summary. Treat a weak or risk-off market as a reason for caution: \
when the setup is marginal, ABSTAIN. When market_context is absent or \
{"available": false}, judge the setup on its own.

Decide PROPOSE if the setup is a reasonable trade as specified, or ABSTAIN if \
anything about it looks unsound: for example a trend day that barely \
qualifies, an entry stretched so far above vwap that it is no longer a \
pullback entry, a target that needs an implausibly large move for the \
session, a volume surge too thin to support the reclaim, or internally \
inconsistent prices or levels. When the evidence is ambiguous, ABSTAIN.

You cannot change any price, quantity, symbol, or order field. Respond only with \
the requested JSON: decision, confidence between 0 and 1, and a short rationale \
that names the specific values behind your decision."""


__all__ = [
    "CLOSE_NOT_ABOVE_PREVIOUS_HIGH",
    "CLOSE_NOT_ABOVE_VWAP",
    "DAY_CHANGE_TOO_LOW",
    "ENTRY_CHASE_TOO_WIDE",
    "OUTSIDE_ENTRY_WINDOW",
    "PULLBACK_BROKE_DOWN",
    "PULLBACK_MISSED_VWAP",
    "SIGNAL_VOLUME_TOO_LOW",
    "STOP_TOO_TIGHT",
    "STOP_TOO_WIDE",
    "TOO_FEW_BARS_ABOVE_VWAP",
    "VWAP_PB_RULES_VERSION",
    "VWAP_PB_STRATEGY_KIND",
    "VWAP_PB_STRATEGY_NAME",
    "VWAP_REVIEW_PROMPT_ID",
    "VWAP_REVIEW_V1",
    "VwapPbConfig",
    "VwapPbResult",
    "VwapPbStrategy",
    "evaluate_vwap_pb",
    "vwap_pb_entry_window",
    "vwap_pb_strategy_spec",
]
