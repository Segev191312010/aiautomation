"""Pure, long-only new-high-of-day momentum rules (MOMO_HOD_V2).

The latest completed five-minute RTH bar signals when it closes above every
earlier high of the session and above session VWAP, on a bar-volume surge,
while the session's relative volume is elevated. Only that latest bar may
signal: a breakout is priced at its own bar close or never.

V2 (after the 2026-10-12 PAPER day) sizes the stop to survive normal noise: a
1x ATR(14) five-minute stop on BF B was 8 cents (0.3%) and was hit within 90
seconds. The stop distance is now the largest of 1.5x ATR(14), 0.6% of entry,
and one tick below the signal bar's low; a signal whose stop would exceed 1.5%
of entry, or whose entry sits more than 4.5 ATR above VWAP (FIGS was 6.6), is
skipped for now and a later bar may still qualify.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, InvalidOperation
from functools import lru_cache

from trading.contracts import (
    Candidate,
    CompletedBar,
    QuoteSnapshot,
    RuleSetup,
    RuleSetupProvenance,
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

MOMO_STRATEGY_KIND = "momo_hod"
# The rule-set identity behind ``MomoConfig.fingerprint()``; the strategy
# catalogue names its MOMO strategy after it.
MOMO_RULES_VERSION = "MOMO_HOD_V2"
NO_SIGNAL_BEFORE_CUTOFF = "no_signal_before_cutoff"
# Retryable skips of a priced signal: a later bar may still qualify, so the
# candidate stays pending until the entry cutoff.
EXTENDED_ABOVE_VWAP = "extended_above_vwap"
STOP_TOO_WIDE = "stop_too_wide"
UTC = timezone.utc
_REGULAR_OPEN = time(9, 30)
_FIRST_BAR_CLOSE = time(9, 35)
_REGULAR_CLOSE = time(16, 0)
_REGULAR_SESSION_MINUTES = Decimal("390")
_BPS = Decimal("10000")
_PERCENT = Decimal("100")
_VWAP_QUANTUM = Decimal("0.0001")
_RVOL_QUANTUM = Decimal("0.01")
_EXTENSION_QUANTUM = Decimal("0.01")
_SHARE_QUANTUM = Decimal("1")

SessionCloseLookup = Callable[[date], datetime | None]


@dataclass(frozen=True, slots=True)
class MomoConfig:
    window_start: time = time(10, 0)
    entry_cutoff: time = time(15, 0)
    # On an early close the cutoff moves in so entries keep this much runway.
    close_buffer: timedelta = timedelta(hours=1)
    atr_period: int = 14
    # Stop distance = max(stop_atr_multiple x ATR, min_stop_pct % of entry,
    # entry - signal-bar low + one tick); wider than max_stop_pct % is skipped.
    stop_atr_multiple: Decimal = Decimal("1.5")
    min_stop_pct: Decimal = Decimal("0.6")
    max_stop_pct: Decimal = Decimal("1.5")
    # Entry minus session VWAP, in ATRs, above which a signal is a chase.
    max_vwap_extension_atr: Decimal = Decimal("4.5")
    target_reward_risk: Decimal = Decimal("2")
    min_rvol: Decimal = Decimal("2.0")
    bar_volume_multiple: Decimal = Decimal("1.5")
    min_prior_bars: int = 6
    max_spread_bps: Decimal = Decimal("15")
    max_chase_bps: Decimal = Decimal("20")
    quote_max_age_seconds: Decimal = QUOTE_MAX_AGE_SECONDS
    # TWS reports intraday US-stock bar volume in round lots of 100 shares,
    # while average_dollar_volume is already in dollars; rvol compares shares.
    volume_multiplier: Decimal = Decimal("100")
    minimum_price: Decimal = Decimal("10")
    maximum_price: Decimal = Decimal("300")
    minimum_average_dollar_volume: Decimal = Decimal("20000000")

    def __post_init__(self) -> None:
        if not _FIRST_BAR_CLOSE <= self.window_start < self.entry_cutoff <= _REGULAR_CLOSE:
            raise ValueError("MOMO window must start before its cutoff inside regular hours")
        if self.close_buffer < timedelta(0):
            raise ValueError("close_buffer must not be negative")
        if self.atr_period < 1 or self.min_prior_bars < 1:
            raise ValueError("atr_period and min_prior_bars must be positive")
        for name in (
            "stop_atr_multiple",
            "min_stop_pct",
            "max_stop_pct",
            "max_vwap_extension_atr",
            "target_reward_risk",
            "min_rvol",
            "bar_volume_multiple",
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
        if self.stop_atr_multiple <= 0 or self.target_reward_risk < 2:
            raise ValueError("stop multiple must be positive and target must be at least 2R")
        # A floor at or above the cap would skip every signal; a cap of 100%
        # or more would admit a stop at or below zero.
        if not self.min_stop_pct < self.max_stop_pct < _PERCENT:
            raise ValueError("stop bounds must satisfy min_stop_pct < max_stop_pct < 100")
        if self.max_vwap_extension_atr <= 0:
            raise ValueError("max_vwap_extension_atr must be positive")
        # rvol divides by price and average dollar volume; both must be positive.
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
                "strategy_kind": MOMO_STRATEGY_KIND,
                "rules_version": MOMO_RULES_VERSION,
                **{item.name: str(getattr(self, item.name)) for item in fields(self)},
            }
        )


@dataclass(frozen=True, slots=True)
class MomoResult:
    """Evaluation of the latest completed session bar, with its signal inputs."""

    setup: RuleSetup | None
    rejection_reason: str | None = None
    retryable: bool = False
    evaluated_bar: CompletedBar | None = None
    session_high: Decimal | None = None
    vwap: Decimal | None = None
    avg_bar_volume: Decimal | None = None
    rvol: Decimal | None = None
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
    session_high: Decimal
    vwap: Decimal
    avg_bar_volume: Decimal
    rvol: Decimal


def _at(session_date: date, local_time: time) -> datetime:
    return datetime.combine(session_date, local_time, tzinfo=NEW_YORK).astimezone(UTC)


def momo_entry_window(
    session_date: date,
    config: MomoConfig = MomoConfig(),
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


@lru_cache(maxsize=32)
def xnys_session_close(session_date: date) -> datetime | None:
    """Scheduled close from the pinned XNYS calendar; ``None`` on closed dates."""
    from trading.scheduler.sessions import market_session

    session = market_session(session_date)
    return None if session is None else session.closes_at


def _qualification_config(config: MomoConfig) -> ORBConfig:
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
        if bar.rth
        and bar.interval_start_utc >= session_open
        and bar.interval_start_utc.astimezone(NEW_YORK).date() == session_date
    ]


def _is_contiguous(session_bars: Sequence[CompletedBar], session_open: datetime) -> bool:
    return all(
        bar.interval_start_utc == session_open + BAR_INTERVAL * index
        for index, bar in enumerate(session_bars)
    )


def _latest_bar_end(now: datetime, session_open: datetime) -> datetime:
    return session_open + BAR_INTERVAL * ((now - session_open) // BAR_INTERVAL)


def _session_metrics(
    session_bars: Sequence[CompletedBar],
    *,
    session_open: datetime,
    reference_price: Decimal,
    average_dollar_volume: Decimal,
    volume_multiplier: Decimal,
) -> _SessionMetrics | None:
    """HOD before the latest bar, VWAP and rvol through it; None without volume."""
    latest = session_bars[-1]
    earlier = session_bars[:-1]
    earlier_volume = sum((bar.volume for bar in earlier), Decimal("0"))
    if earlier_volume <= 0:
        return None
    session_volume = earlier_volume + latest.volume
    typical_volume = sum(
        ((bar.high + bar.low + bar.close) * bar.volume for bar in session_bars),
        Decimal("0"),
    )
    elapsed_minutes = Decimal(int((latest.interval_end_utc - session_open).total_seconds()) // 60)
    # Shares traded so far against a straight-line share of the average day.
    rvol = (session_volume * volume_multiplier * reference_price * _REGULAR_SESSION_MINUTES) / (
        average_dollar_volume * elapsed_minutes
    )
    return _SessionMetrics(
        session_high=max(bar.high for bar in earlier),
        vwap=typical_volume / (Decimal("3") * session_volume),
        avg_bar_volume=earlier_volume / Decimal(len(earlier)),
        rvol=rvol,
    )


def _is_signal(bar: CompletedBar, metrics: _SessionMetrics, config: MomoConfig) -> bool:
    return (
        bar.close > metrics.session_high
        and bar.close > metrics.vwap
        and bar.volume >= config.bar_volume_multiple * metrics.avg_bar_volume
        and metrics.rvol >= config.min_rvol
    )


def _prior_session_high(completed: Sequence[CompletedBar], session_date: date) -> Decimal | None:
    by_day: dict[date, list[CompletedBar]] = {}
    for bar in completed:
        day = bar.interval_start_utc.astimezone(NEW_YORK).date()
        if bar.rth and day < session_date:
            by_day.setdefault(day, []).append(bar)
    if not by_day:
        return None
    return max(bar.high for bar in by_day[max(by_day)])


def _stop_distance(
    *, entry: Decimal, atr: Decimal, signal: CompletedBar, tick: Decimal, config: MomoConfig
) -> Decimal:
    """Per-share stop distance before tick rounding.

    The stop must clear normal five-minute noise (the ATR multiple), never be
    a token percentage of price, and sit below the bar that produced the
    signal, so a retest of that bar's low does not stop the trade out.
    """
    return max(
        atr * config.stop_atr_multiple,
        entry * config.min_stop_pct / _PERCENT,
        entry - signal.low + tick,
    )


def _rejected(
    reason: str,
    *,
    retryable: bool,
    bar: CompletedBar | None = None,
    metrics: _SessionMetrics | None = None,
    atr: Decimal | None = None,
) -> MomoResult:
    return MomoResult(
        setup=None,
        rejection_reason=reason,
        retryable=retryable,
        evaluated_bar=bar,
        session_high=None if metrics is None else metrics.session_high,
        vwap=None if metrics is None else metrics.vwap,
        avg_bar_volume=None if metrics is None else metrics.avg_bar_volume,
        rvol=None if metrics is None else metrics.rvol,
        atr=atr,
    )


def evaluate_momo(
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
    config: MomoConfig = MomoConfig(),
    session_close: datetime | None = None,
) -> MomoResult:
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
    now_utc = require_aware(now or datetime.now(UTC))
    window_start, cutoff = momo_entry_window(session_date, config, session_close=session_close)
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
    # HOD, VWAP and rvol are cumulative; a missing interval would misstate all three.
    if not _is_contiguous(session_bars, session_open):
        return _rejected("bar_session_incomplete", retryable=before_cutoff)
    latest = session_bars[-1]
    if before_cutoff and latest.interval_end_utc != _latest_bar_end(now_utc, session_open):
        # A lagging series would let an older bar signal as if it were current.
        return _rejected("bar_session_stale", retryable=True, bar=latest)

    if (
        not window_start <= latest.interval_end_utc <= cutoff
        or len(session_bars) - 1 < config.min_prior_bars
    ):
        return _rejected(no_signal_reason, retryable=before_cutoff, bar=latest)
    metrics = _session_metrics(
        session_bars,
        session_open=session_open,
        reference_price=Decimal(str(reference_price)),
        average_dollar_volume=Decimal(str(average_dollar_volume)),
        volume_multiplier=config.volume_multiplier,
    )
    if metrics is None:
        return _rejected("bar_volume_unavailable", retryable=before_cutoff, bar=latest)
    if not _is_signal(latest, metrics, config):
        return _rejected(no_signal_reason, retryable=before_cutoff, bar=latest, metrics=metrics)
    if not before_cutoff:
        # Quotes are withheld at the cutoff: the signal was never priced in the window.
        return _rejected(
            QUOTE_REQUIRED if quote is None else "signal_after_cutoff",
            retryable=False,
            bar=latest,
            metrics=metrics,
        )
    if quote is None:
        return _rejected(QUOTE_REQUIRED, retryable=True, bar=latest, metrics=metrics)
    return _price_signal(
        candidate=candidate,
        signal=latest,
        metrics=metrics,
        completed=completed,
        quote=quote,
        session_date=session_date,
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
    completed: Sequence[CompletedBar],
    quote: QuoteSnapshot,
    session_date: date,
    run_id: str,
    strategy_id: str,
    strategy_version: int,
    reference_price: Decimal | int | str,
    average_dollar_volume: Decimal | int | str,
    candidate_input_fingerprint: str | None,
    now: datetime,
    config: MomoConfig,
) -> MomoResult:
    """Turn a signal inside the window into a setup, or a reason there is none."""
    if quote.con_id != candidate.con_id:
        return _rejected("quote_contract_mismatch", retryable=True, bar=signal, metrics=metrics)
    try:
        quote.require_fresh(now=now, max_age_seconds=config.quote_max_age_seconds)
    except (ValueError, TypeError):
        return _rejected("quote_stale", retryable=True, bar=signal, metrics=metrics)
    if quote.spread_bps > config.max_spread_bps:
        return _rejected("quote_spread_too_wide", retryable=True, bar=signal, metrics=metrics)

    atr: Decimal | None = None
    try:
        entry_limit = _round_to_tick(max(signal.close, quote.ask), quote.min_tick, ROUND_CEILING)
        if entry_limit > signal.close * (Decimal("1") + config.max_chase_bps / _BPS):
            # Retryable: a later bar can signal again from its own close.
            return _rejected(
                "breakout_chase_too_wide", retryable=True, bar=signal, metrics=metrics
            )
        history = [
            bar
            for bar in completed
            if bar.rth and bar.interval_end_utc <= signal.interval_end_utc
        ]
        atr = wilder_atr(history, period=config.atr_period)
        prior_day_high = _prior_session_high(completed, session_date)
        if prior_day_high is None:
            return _rejected(
                "bar_prior_session_missing", retryable=True, bar=signal, metrics=metrics, atr=atr
            )
        # Both skips below are retryable: a later, calmer bar may still qualify.
        if entry_limit - metrics.vwap > atr * config.max_vwap_extension_atr:
            return _rejected(
                EXTENDED_ABOVE_VWAP, retryable=True, bar=signal, metrics=metrics, atr=atr
            )
        stop_distance = _stop_distance(
            entry=entry_limit, atr=atr, signal=signal, tick=quote.min_tick, config=config
        )
        if stop_distance > entry_limit * config.max_stop_pct / _PERCENT:
            return _rejected(STOP_TOO_WIDE, retryable=True, bar=signal, metrics=metrics, atr=atr)
        stop_price = _round_to_tick(entry_limit - stop_distance, quote.min_tick, ROUND_FLOOR)
        if stop_price <= 0:
            return _rejected(
                "stop_price_not_positive", retryable=False, bar=signal, metrics=metrics, atr=atr
            )
        if stop_price >= entry_limit:
            return _rejected(
                "stop_distance_not_positive", retryable=False, bar=signal, metrics=metrics, atr=atr
            )
        # 2R on the tick-rounded stop, so the bracket never falls below the
        # risk policy's minimum reward-to-risk after rounding.
        target_price = _round_to_tick(
            entry_limit + (entry_limit - stop_price) * config.target_reward_risk,
            quote.min_tick,
            ROUND_CEILING,
        )
        # Every session bar behind HOD, VWAP and volume, and every prior-session
        # bar behind prior_day_high, is RTH history up to the signal bar.
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
            strategy_kind=MOMO_STRATEGY_KIND,
            symbol=candidate.symbol,
            atr=atr,
            entry_limit=entry_limit,
            stop_price=stop_price,
            target_price=target_price,
            levels={
                "prior_day_high": prior_day_high,
                "vwap": metrics.vwap.quantize(_VWAP_QUANTUM, rounding=ROUND_HALF_EVEN),
                "signal_bar_volume": signal.volume * config.volume_multiplier,
                "avg_bar_volume": (metrics.avg_bar_volume * config.volume_multiplier).quantize(
                    _SHARE_QUANTUM, rounding=ROUND_HALF_EVEN
                ),
                "rvol": metrics.rvol.quantize(_RVOL_QUANTUM, rounding=ROUND_HALF_EVEN),
                "stop_distance": entry_limit - stop_price,
                "vwap_extension_atr": ((entry_limit - metrics.vwap) / atr).quantize(
                    _EXTENSION_QUANTUM, rounding=ROUND_HALF_EVEN
                ),
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
        reason = str(exc) or "momo_setup_invalid"
        return _rejected(
            reason, retryable=is_data_rejection(reason), bar=signal, metrics=metrics, atr=atr
        )
    return MomoResult(
        setup=setup,
        evaluated_bar=signal,
        session_high=metrics.session_high,
        vwap=metrics.vwap,
        avg_bar_volume=metrics.avg_bar_volume,
        rvol=metrics.rvol,
        atr=atr,
    )


class MomoStrategy:
    """MOMO_HOD_V2 behind the strategy boundary.

    The XNYS calendar supplies the scheduled close by default, so a half day
    moves the cutoff in rather than letting entries run into the close.
    """

    __slots__ = ("_config", "_session_close")

    def __init__(
        self,
        config: MomoConfig = MomoConfig(),
        *,
        session_close: SessionCloseLookup = xnys_session_close,
    ) -> None:
        self._config = config
        self._session_close = session_close

    @property
    def strategy_kind(self) -> str:
        return MOMO_STRATEGY_KIND

    @property
    def config(self) -> MomoConfig:
        return self._config

    def entry_window(self, session_date: date) -> tuple[datetime, datetime]:
        return momo_entry_window(
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
        return evaluate_momo(
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


__all__ = [
    "EXTENDED_ABOVE_VWAP",
    "MOMO_RULES_VERSION",
    "MOMO_STRATEGY_KIND",
    "NO_SIGNAL_BEFORE_CUTOFF",
    "STOP_TOO_WIDE",
    "MomoConfig",
    "MomoResult",
    "MomoStrategy",
    "evaluate_momo",
    "momo_entry_window",
    "xnys_session_close",
]
