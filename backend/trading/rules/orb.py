"""Pure, long-only opening-range breakout rules for Autotrader V1."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from zoneinfo import ZoneInfo

from trading.contracts import (
    Candidate,
    CompletedBar,
    QuoteSnapshot,
    RuleSetup,
    RuleSetupProvenance,
    fingerprint_payload,
    require_aware,
)

NEW_YORK = ZoneInfo("America/New_York")
UTC = timezone.utc
QUOTE_MAX_AGE_SECONDS = Decimal("2")
NO_SIGNAL_YET = "NO_SIGNAL_YET"
_BPS = Decimal("10000")


class ORBError(ValueError):
    """Raised when an ORB setup cannot be built from the supplied evidence."""


@dataclass(frozen=True, slots=True)
class ORBConfig:
    opening_range_start: time = time(9, 30)
    opening_range_end: time = time(9, 45)
    entry_cutoff: time = time(12, 0)
    atr_period: int = 14
    stop_atr_multiple: Decimal = Decimal("1")
    target_reward_risk: Decimal = Decimal("2")
    quote_max_age_seconds: Decimal = QUOTE_MAX_AGE_SECONDS
    max_spread_bps: Decimal = Decimal("15")
    max_chase_bps: Decimal = Decimal("20")
    minimum_price: Decimal = Decimal("10")
    maximum_price: Decimal = Decimal("300")
    minimum_average_dollar_volume: Decimal = Decimal("20000000")

    def __post_init__(self) -> None:
        if self.opening_range_start != time(9, 30) or self.opening_range_end != time(9, 45):
            raise ValueError("V1 opening range must be 09:30 to 09:45 New York time")
        if self.opening_range_start >= self.opening_range_end:
            raise ValueError("opening range start must precede its end")
        if self.entry_cutoff <= self.opening_range_end:
            raise ValueError("entry cutoff must follow the opening range")
        if self.atr_period < 1:
            raise ValueError("atr_period must be positive")
        for name in (
            "stop_atr_multiple",
            "target_reward_risk",
            "quote_max_age_seconds",
            "max_spread_bps",
            "max_chase_bps",
            "minimum_price",
            "maximum_price",
            "minimum_average_dollar_volume",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.stop_atr_multiple <= 0 or self.target_reward_risk < 2:
            raise ValueError("stop multiple must be positive and target must be at least 2R")
        if self.minimum_price >= self.maximum_price:
            raise ValueError("minimum price must be below maximum price")


@dataclass(frozen=True, slots=True)
class ORBResult:
    setup: RuleSetup | None
    rejection_reason: str | None = None
    opening_range_high: Decimal | None = None
    opening_range_low: Decimal | None = None
    atr: Decimal | None = None
    breakout_bar: CompletedBar | None = None

    @property
    def accepted(self) -> bool:
        return self.setup is not None


def _as_decimal(value: Decimal | int | str, field_name: str) -> Decimal:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ORBError(f"{field_name} must be a valid decimal") from exc
    if not decimal_value.is_finite():
        raise ORBError(f"{field_name} must be finite")
    return decimal_value


def _session_boundary(session_date: date, boundary: time) -> datetime:
    return datetime.combine(session_date, boundary, tzinfo=NEW_YORK).astimezone(UTC)


def _ordered_bars(bars: Sequence[CompletedBar]) -> list[CompletedBar]:
    if not bars:
        raise ORBError("no bars supplied")
    ordered = sorted(bars, key=lambda bar: bar.interval_start_utc)
    seen_starts: set[datetime] = set()
    previous: CompletedBar | None = None
    for bar in ordered:
        if bar.interval_start_utc in seen_starts:
            raise ORBError("duplicate bar interval")
        seen_starts.add(bar.interval_start_utc)
        if not bar.completed:
            raise ORBError("incomplete bar supplied")
        if previous is not None and previous.interval_end_utc > bar.interval_start_utc:
            raise ORBError("overlapping bar intervals")
        previous = bar
    return ordered


def _round_to_tick(value: Decimal, tick: Decimal, rounding: str) -> Decimal:
    if not tick.is_finite() or tick <= 0:
        raise ORBError("quote min_tick must be positive and finite")
    return (value / tick).to_integral_value(rounding=rounding) * tick


def qualify_candidate(
    candidate: Candidate,
    *,
    reference_price: Decimal | int | str,
    average_dollar_volume: Decimal | int | str,
    config: ORBConfig = ORBConfig(),
) -> str | None:
    """Return a stable rejection code, or None for an admitted common stock."""
    if candidate.admission_status != "admitted":
        return "candidate_not_admitted"
    if candidate.currency != "USD" or candidate.security_type != "STK":
        return "candidate_not_usd_stock"
    if candidate.stock_type != "COMMON":
        return "candidate_not_common_stock"
    price = _as_decimal(reference_price, "reference_price")
    dollar_volume = _as_decimal(average_dollar_volume, "average_dollar_volume")
    if price < config.minimum_price or price > config.maximum_price:
        return "candidate_price_out_of_range"
    if dollar_volume < config.minimum_average_dollar_volume:
        return "candidate_dollar_volume_too_low"
    return None


def wilder_atr(bars: Sequence[CompletedBar], period: int = 14) -> Decimal:
    """Calculate Wilder ATR from completed RTH bars in chronological order."""
    if period < 1:
        raise ORBError("ATR period must be positive")
    ordered = _ordered_bars(bars)
    if len(ordered) < period:
        raise ORBError("insufficient_atr_history")
    if any(not bar.rth for bar in ordered):
        raise ORBError("ATR history must contain RTH bars only")
    con_ids = {bar.con_id for bar in ordered}
    if len(con_ids) != 1:
        raise ORBError("ATR history contains multiple contracts")

    true_ranges: list[Decimal] = []
    previous_close: Decimal | None = None
    for bar in ordered:
        if previous_close is None:
            true_range = bar.high - bar.low
        else:
            true_range = max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        true_ranges.append(true_range)
        previous_close = bar.close

    average = sum(true_ranges[:period], Decimal("0")) / Decimal(period)
    for true_range in true_ranges[period:]:
        average = ((average * Decimal(period - 1)) + true_range) / Decimal(period)
    return average


def evaluate_orb(
    *,
    candidate: Candidate,
    bars: Sequence[CompletedBar],
    quote: QuoteSnapshot,
    session_date: date,
    run_id: str,
    strategy_id: str,
    strategy_version: int,
    reference_price: Decimal | int | str,
    average_dollar_volume: Decimal | int | str,
    candidate_input_fingerprint: str | None = None,
    now: datetime | None = None,
    config: ORBConfig = ORBConfig(),
) -> ORBResult:
    try:
        rejection = qualify_candidate(
            candidate,
            reference_price=reference_price,
            average_dollar_volume=average_dollar_volume,
            config=config,
        )
    except ORBError as exc:
        return ORBResult(setup=None, rejection_reason=str(exc))
    if rejection:
        return ORBResult(setup=None, rejection_reason=rejection)
    if quote.con_id != candidate.con_id:
        return ORBResult(setup=None, rejection_reason="quote_contract_mismatch")

    try:
        now_utc = require_aware(now or datetime.now(UTC))
        quote.require_fresh(now=now_utc, max_age_seconds=config.quote_max_age_seconds)
    except (ValueError, TypeError):
        return ORBResult(setup=None, rejection_reason="quote_stale")
    if quote.spread_bps > config.max_spread_bps:
        return ORBResult(setup=None, rejection_reason="quote_spread_too_wide")

    try:
        ordered = _ordered_bars(bars)
    except ORBError as exc:
        return ORBResult(setup=None, rejection_reason=str(exc))
    if any(bar.con_id != candidate.con_id for bar in ordered):
        return ORBResult(setup=None, rejection_reason="bar_contract_mismatch")

    opening_start = _session_boundary(session_date, config.opening_range_start)
    opening_end = _session_boundary(session_date, config.opening_range_end)
    opening_bars = [
        bar
        for bar in ordered
        if opening_start <= bar.interval_start_utc < opening_end
    ]
    expected_slots = [
        _session_boundary(session_date, time(9, 30)),
        _session_boundary(session_date, time(9, 35)),
        _session_boundary(session_date, time(9, 40)),
    ]
    opening_by_start = {bar.interval_start_utc: bar for bar in opening_bars}
    if len(opening_bars) != 3 or any(slot not in opening_by_start for slot in expected_slots):
        return ORBResult(setup=None, rejection_reason="opening_range_requires_three_bars")
    opening = [opening_by_start[slot] for slot in expected_slots]
    if any(
        not bar.rth
        or bar.interval_end_utc != bar.interval_start_utc + timedelta(minutes=5)
        for bar in opening
    ):
        return ORBResult(setup=None, rejection_reason="opening_range_bar_invalid")

    opening_high = max(bar.high for bar in opening)
    opening_low = min(bar.low for bar in opening)
    cutoff = _session_boundary(session_date, config.entry_cutoff)
    signal_bars = [
        bar
        for bar in ordered
        if bar.rth
        and opening_end <= bar.interval_start_utc
        and bar.interval_end_utc <= cutoff
        and bar.interval_end_utc <= now_utc
        and bar.interval_start_utc.astimezone(NEW_YORK).date() == session_date
    ]
    breakout = next((bar for bar in signal_bars if bar.close > opening_high), None)
    if breakout is None:
        return ORBResult(
            setup=None,
            rejection_reason=(
                NO_SIGNAL_YET if now_utc < cutoff else "no_breakout_before_cutoff"
            ),
            opening_range_high=opening_high,
            opening_range_low=opening_low,
        )

    history = [
        bar
        for bar in ordered
        if bar.rth and bar.interval_end_utc <= breakout.interval_end_utc
    ]
    try:
        atr = wilder_atr(history, period=config.atr_period)
        entry_raw = max(breakout.close, quote.ask)
        entry_limit = _round_to_tick(entry_raw, quote.min_tick, ROUND_CEILING)
        max_entry = breakout.close * (Decimal("1") + config.max_chase_bps / _BPS)
        if entry_limit > max_entry:
            return ORBResult(
                setup=None,
                rejection_reason="breakout_chase_too_wide",
                opening_range_high=opening_high,
                opening_range_low=opening_low,
                atr=atr,
                breakout_bar=breakout,
            )
        stop_price = _round_to_tick(
            entry_limit - (atr * config.stop_atr_multiple),
            quote.min_tick,
            ROUND_FLOOR,
        )
        if stop_price <= 0 or stop_price >= entry_limit:
            raise ORBError("stop_price_not_positive")
        risk_per_share = entry_limit - stop_price
        target_price = _round_to_tick(
            entry_limit + (risk_per_share * config.target_reward_risk),
            quote.min_tick,
            ROUND_CEILING,
        )
        opening_fingerprints = tuple(bar.fingerprint() for bar in opening)
        atr_fingerprints = tuple(bar.fingerprint() for bar in history)
        breakout_fingerprint = breakout.fingerprint()
        quote_fingerprint = quote.fingerprint()
        input_fingerprint = candidate_input_fingerprint or fingerprint_payload(
            {
                "candidate": fingerprint_payload(candidate),
                "reference_price": str(reference_price),
                "average_dollar_volume": str(average_dollar_volume),
            }
        )
        causal_bars = tuple(
            dict.fromkeys((*opening_fingerprints, *atr_fingerprints, breakout_fingerprint))
        )
        setup = RuleSetup(
            run_id=run_id,
            candidate_id=candidate.id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            symbol=candidate.symbol,
            opening_range_high=opening_high,
            opening_range_low=opening_low,
            atr=atr,
            entry_limit=entry_limit,
            stop_price=stop_price,
            target_price=target_price,
            bar_fingerprints=causal_bars,
            quote_fingerprint=quote_fingerprint,
            causal_provenance=RuleSetupProvenance(
                opening_bar_fingerprints=opening_fingerprints,
                atr_bar_fingerprints=atr_fingerprints,
                breakout_bar_fingerprint=breakout_fingerprint,
                quote_fingerprint=quote_fingerprint,
                candidate_input_fingerprint=input_fingerprint,
            ),
            detected_at=breakout.interval_end_utc,
        )
    except (ORBError, ValueError, InvalidOperation) as exc:
        return ORBResult(
            setup=None,
            rejection_reason=str(exc),
            opening_range_high=opening_high,
            opening_range_low=opening_low,
            atr=locals().get("atr"),
            breakout_bar=breakout,
        )
    return ORBResult(
        setup=setup,
        opening_range_high=opening_high,
        opening_range_low=opening_low,
        atr=atr,
        breakout_bar=breakout,
    )


def build_orb_setup(**kwargs: object) -> RuleSetup:
    result = evaluate_orb(**kwargs)
    if result.setup is None:
        raise ORBError(result.rejection_reason or "ORB setup unavailable")
    return result.setup


__all__ = [
    "NO_SIGNAL_YET",
    "ORBConfig",
    "ORBError",
    "ORBResult",
    "build_orb_setup",
    "evaluate_orb",
    "qualify_candidate",
    "wilder_atr",
]
