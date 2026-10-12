"""VWAP_PB_V1: a long-only VWAP pullback on a trend day, run in shadow only.

The synthetic day prices every move around a 100.00 signal close with 0.40
ranges, so ATR(14) is exactly 0.40 and every level can be checked by hand.

The replays use the real IBKR PAPER bars for 2026-10-12 (tests/fixtures/momo).
VWAP_PB_V1 would not have traded either symbol that day. BF B was no trend day
for most of it: below +1% on every poll before MOMO's 11:50 ET breakout, and
under 60% of bars above VWAP until 13:15; after that no bar closed above the
previous high on 1.2x volume. FIGS trended all session but never came back to
VWAP: the bars that met every trend and trigger rule (12:35, 13:25, and 14:30
at the cutoff) all had their pullback low 0.28-0.40 above VWAP, against an
allowance of 0.25 ATR (about 0.03).
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from trading.contracts import (
    AIReview,
    Candidate,
    CompletedBar,
    DecisionOutcome,
    EngineMode,
    PortfolioSnapshot,
    QuoteSnapshot,
    RiskPolicy,
    RuleSetup,
    Run,
    ScannerRun,
    StrategyRecord,
    StrategyState,
    fingerprint_payload,
)
from trading.market.ibkr_boundary import DailyBarBatch, IBKRDailyBar, liquidity_profile
from trading.orchestration import CandidateInput, RepositoryBackedShadowRunner, ShadowPipeline
from trading.orchestration.pipeline import entry_cutoff_utc
from trading.orchestration.session_runner import ensure_strategy
from trading.rules import (
    NO_SIGNAL_BEFORE_CUTOFF,
    NO_SIGNAL_YET,
    QUOTE_REQUIRED,
    MomoConfig,
    StrategyEvaluator,
    entry_window_utc,
    is_data_rejection,
)
from trading.rules.vwap_pb import (
    CLOSE_NOT_ABOVE_PREVIOUS_HIGH,
    CLOSE_NOT_ABOVE_VWAP,
    DAY_CHANGE_TOO_LOW,
    ENTRY_CHASE_TOO_WIDE,
    OUTSIDE_ENTRY_WINDOW,
    PULLBACK_BROKE_DOWN,
    PULLBACK_MISSED_VWAP,
    SIGNAL_VOLUME_TOO_LOW,
    STOP_TOO_TIGHT,
    STOP_TOO_WIDE,
    TOO_FEW_BARS_ABOVE_VWAP,
    VWAP_PB_RULES_VERSION,
    VWAP_PB_STRATEGY_KIND,
    VWAP_PB_STRATEGY_NAME,
    VWAP_REVIEW_PROMPT_ID,
    VWAP_REVIEW_V1,
    VwapPbConfig,
    VwapPbResult,
    VwapPbStrategy,
    evaluate_vwap_pb,
    vwap_pb_strategy_spec,
)

NEW_YORK = ZoneInfo("America/New_York")
FIXTURES = Path(__file__).parent / "fixtures" / "momo"
SESSION = date(2026, 10, 12)
PRIOR = date(2026, 10, 9)
BAR = timedelta(minutes=5)
TICK = Decimal("0.01")
Q2 = Decimal("0.01")
Q4 = Decimal("0.0001")
# ibapi reports bar and daily volume in round lots of 100 shares.
VOLUME_MULTIPLIER = Decimal("100")
HASH = "f" * 64
RUN_ID = "run-vwap-pb"
SYN_CON_ID = 4343
SETUP_LEVELS = {
    "vwap",
    "pullback_low",
    "day_change_pct",
    "pct_bars_above_vwap",
    "signal_bar_volume",
    "avg_bar_volume",
    "stop_distance",
}


def _et(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=NEW_YORK).astimezone(
        timezone.utc
    )


# --- The synthetic trend day --------------------------------------------------

# (open, high, low, close) offsets from the 100.00 signal close. Every range is
# 0.40 and contains the previous close, so every true range, and ATR(14), is 0.40.
PIVOT = Decimal("100")
PRIOR_SHAPE = ("-2.10", "-1.90", "-2.30", "-2.10")  # Friday: flat at 97.90
TREND_DAY = (
    ("-2.00", "-1.70", "-2.10", "-1.70"),  # 09:30 the climb starts at Friday's close
    ("-1.70", "-1.30", "-1.70", "-1.30"),  # 09:35
    ("-1.30", "-0.90", "-1.30", "-0.90"),  # 09:40
    ("-0.90", "-0.50", "-0.90", "-0.50"),  # 09:45
    ("-0.50", "-0.10", "-0.50", "-0.10"),  # 09:50
    ("-0.10", "0.30", "-0.10", "0.30"),  # 09:55 high of day 100.30
    ("0.30", "0.30", "-0.10", "0.00"),  # 10:00 the pullback starts
    ("0.00", "0.00", "-0.40", "-0.30"),  # 10:05
    ("-0.30", "-0.20", "-0.60", "-0.50"),  # 10:10
    ("-0.50", "-0.40", "-0.80", "-0.70"),  # 10:15 low 99.20, close 99.30 under VWAP
    ("-0.70", "-0.30", "-0.70", "-0.40"),  # 10:20 high 99.70
    ("-0.40", "0.00", "-0.40", "0.00"),  # 10:25 the signal: closes 100.00 at 10:30
)
# Gap up, a fade under VWAP for five bars, then the same pullback and reclaim.
CHOPPY_DAY = (
    ("-0.50", "0.30", "-0.60", "0.20"),
    ("0.20", "0.30", "-0.30", "-0.20"),
    ("-0.20", "-0.10", "-0.60", "-0.50"),
    ("-0.50", "-0.40", "-0.80", "-0.70"),
    ("-0.70", "-0.50", "-0.90", "-0.60"),
    ("-0.60", "-0.30", "-0.70", "-0.40"),
    ("-0.40", "-0.10", "-0.50", "-0.20"),
    ("-0.20", "0.10", "-0.30", "0.00"),
    ("0.00", "0.10", "-0.30", "-0.20"),
    ("-0.20", "-0.10", "-0.45", "-0.40"),
    ("-0.40", "-0.10", "-0.40", "-0.20"),
    ("-0.20", "0.05", "-0.25", "0.00"),
)
# The trend day, but after 10:05 price only drifts along the top of its range.
SHALLOW_DAY = (
    *TREND_DAY[:7],
    ("0.00", "0.20", "-0.20", "0.10"),
    ("0.10", "0.20", "-0.20", "0.00"),
    ("0.00", "0.10", "-0.30", "-0.10"),
    ("-0.10", "-0.05", "-0.25", "-0.15"),
    ("-0.15", "0.10", "-0.20", "0.00"),
)
FLAT = ("0.00", "0.10", "-0.10", "0.00")


def _bar(
    start: datetime,
    shape: Sequence[str],
    *,
    volume: str = "1000",
    scale: Decimal = Decimal("1"),
) -> CompletedBar:
    open_, high, low, close = (
        (PIVOT + Decimal(offset) * scale).quantize(TICK) for offset in shape
    )
    return CompletedBar(
        con_id=SYN_CON_ID,
        interval_start_utc=start,
        interval_end_utc=start + BAR,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        rth=True,
        source="ibkr",
        data_mode="historical",
    )


def _session(
    shapes: Sequence[Sequence[str]] = TREND_DAY,
    *,
    scale: Decimal = Decimal("1"),
    signal_volume: str = "1500",
) -> list[CompletedBar]:
    """Friday's 78 quiet bars, then ``shapes`` from 09:30 ET; the last may signal.

    ``scale`` stretches every move about the 100.00 close, so ATR and the
    pullback scale with it while every rule's yes-or-no answer stays the same.
    """
    prior = [_bar(_et(PRIOR, 9, 30) + BAR * index, PRIOR_SHAPE, scale=scale) for index in range(78)]
    last = len(shapes) - 1
    today = [
        _bar(
            _et(SESSION, 9, 30) + BAR * index,
            shape,
            scale=scale,
            volume=signal_volume if index == last else "1000",
        )
        for index, shape in enumerate(shapes)
    ]
    return [*prior, *today]


def _reference(scale: Decimal = Decimal("1")) -> str:
    """Friday's close, the reference price the trend-day rule measures against."""
    return str((PIVOT + Decimal(PRIOR_SHAPE[3]) * scale).quantize(TICK))


def _syn_candidate() -> Candidate:
    return Candidate(
        id="candidate-syn",
        scanner_run_id="scanner-syn",
        con_id=SYN_CON_ID,
        symbol="SYN",
        primary_exchange="NYSE",
        rank=1,
        admission_status="admitted",
    )


def _syn_quote(
    now: datetime,
    *,
    bid: str = "99.99",
    ask: str = "100.00",
    con_id: int = SYN_CON_ID,
    age: timedelta = timedelta(0),
) -> QuoteSnapshot:
    return QuoteSnapshot(con_id=con_id, bid=bid, ask=ask, observed_at=now - age, min_tick=TICK)


def _after(bars: Sequence[CompletedBar]) -> datetime:
    return bars[-1].interval_end_utc + timedelta(seconds=5)


def _evaluate_synthetic(
    bars: Sequence[CompletedBar],
    *,
    quote: QuoteSnapshot | None = None,
    quoted: bool = True,
    reference_price: str = "97.90",
    now: datetime | None = None,
    config: VwapPbConfig = VwapPbConfig(),
) -> VwapPbResult:
    """Evaluate 5 s after the last bar closes; quoted at 99.99 x 100.00 by default."""
    observed = now or _after(bars)
    if quote is None and quoted:
        quote = _syn_quote(observed)
    return evaluate_vwap_pb(
        candidate=_syn_candidate(),
        bars=bars,
        quote=quote,
        session_date=SESSION,
        run_id=RUN_ID,
        strategy_id="strategy-vwap-pb",
        strategy_version=1,
        reference_price=reference_price,
        average_dollar_volume="25000000",
        now=observed,
        config=config,
    )


def _no_signal(result: VwapPbResult) -> tuple[str | None, str | None, bool]:
    return result.rejection_reason, result.unmet, result.retryable


# --- Rules on the synthetic day -----------------------------------------------


def test_a_trend_day_pullback_that_reclaims_vwap_builds_the_exact_setup():
    bars = _session()
    signal = bars[-1]
    # The signal bar ends at 10:30 ET, the first bar end the window admits.
    assert signal.interval_end_utc == _et(SESSION, 10, 30)

    unquoted = _evaluate_synthetic(bars, quoted=False)
    assert (unquoted.rejection_reason, unquoted.retryable) == (QUOTE_REQUIRED, True)
    assert unquoted.evaluated_bar == signal and unquoted.unmet is None

    result = _evaluate_synthetic(bars)
    setup = result.setup
    assert setup is not None and result.accepted
    assert result.atr == setup.atr == Decimal("0.40")
    # Trend day: 100.00 is 2.15% over Friday's 97.90 close, and 11 of 12 bars
    # closed above their running VWAP (10:15-10:20 closed 99.30, under 99.3767).
    assert result.day_change_pct is not None and result.pct_bars_above_vwap is not None
    assert result.day_change_pct.quantize(Q2) == Decimal("2.15")
    assert result.pct_bars_above_vwap.quantize(Q2) == Decimal("91.67")
    # Pullback: of the four bars before the signal, 10:15-10:20 made the lowest
    # low, 99.20, inside [99.3767 - 0.5 x 0.40, 99.3767 + 0.25 x 0.40].
    assert result.pullback_low == Decimal("99.20")
    assert result.pullback_vwap is not None
    assert result.pullback_vwap.quantize(Q4) == Decimal("99.3767")
    # Trigger: 100.00 closes above VWAP and the previous 99.70 high, on 1,500
    # lots against an earlier mean of 1,000. VWAP = 3,729,300 / 37,500.
    assert result.vwap == Decimal("99.448")
    # Entry at the 100.00 ask; stop = floor(99.20 - 0.1 x 0.40) = 99.16, a
    # 0.84 (0.84%) distance; target = 100.00 + 2 x 0.84.
    assert (setup.entry_limit, setup.stop_price, setup.target_price) == (
        Decimal("100.00"),
        Decimal("99.16"),
        Decimal("101.68"),
    )
    assert setup.strategy_kind == VWAP_PB_STRATEGY_KIND == "vwap_pb"
    assert setup.opening_range_high is None and setup.opening_range_low is None
    assert setup.levels == {
        "vwap": Decimal("99.4480"),
        "pullback_low": Decimal("99.20"),
        "day_change_pct": Decimal("2.15"),
        "pct_bars_above_vwap": Decimal("91.67"),
        "signal_bar_volume": Decimal("150000"),
        "avg_bar_volume": Decimal("100000"),
        "stop_distance": Decimal("0.84"),
    }
    assert setup.detected_at == signal.interval_end_utc

    # Provenance names every causal input: Friday's 78 and today's 12 RTH bars.
    provenance = setup.causal_provenance
    assert provenance is not None
    assert provenance.breakout_bar_fingerprint == signal.fingerprint()
    assert provenance.atr_bar_fingerprints == tuple(bar.fingerprint() for bar in bars)
    assert set(setup.bar_fingerprints) == {bar.fingerprint() for bar in bars}
    assert provenance.quote_fingerprint == setup.quote_fingerprint

    # The strategy boundary returns the same setup.
    now = _after(bars)
    adapted = VwapPbStrategy().evaluate(
        candidate=_syn_candidate(),
        bars=bars,
        quote=_syn_quote(now),
        session_date=SESSION,
        run_id=RUN_ID,
        strategy_id="strategy-vwap-pb",
        strategy_version=1,
        reference_price="97.90",
        average_dollar_volume="25000000",
        candidate_input_fingerprint=None,
        now=now,
    )
    assert adapted.setup == setup


def test_a_day_up_less_than_one_percent_is_not_a_trend_day():
    bars = _session()
    # 100.00 against 99.00 is +1.0101%: still a trend day.
    at_threshold = _evaluate_synthetic(bars, reference_price="99.00")
    assert at_threshold.setup is not None
    assert at_threshold.setup.levels["day_change_pct"] == Decimal("1.01")
    # 100.00 is below 99.01 x 1.01 = 100.0001.
    flat = _evaluate_synthetic(bars, reference_price="99.01")
    assert _no_signal(flat) == (NO_SIGNAL_YET, DAY_CHANGE_TOO_LOW, True)
    assert flat.setup is None and flat.day_change_pct is not None and flat.day_change_pct < 1


def test_a_choppy_day_with_too_few_bars_above_vwap_is_not_a_trend_day():
    bars = _session(CHOPPY_DAY)
    result = _evaluate_synthetic(bars)
    # Up 2.15% on the day, but six of twelve bars closed under their running VWAP.
    assert _no_signal(result) == (NO_SIGNAL_YET, TOO_FEW_BARS_ABOVE_VWAP, True)
    assert result.day_change_pct is not None and result.day_change_pct > 1
    assert result.pct_bars_above_vwap == Decimal("50")

    # The share alone blocks it: at a 50% threshold the same bars price, and the
    # stop, floor(99.55 - 0.1 x 0.4568) = 99.50, sits exactly on the 0.5% floor.
    relaxed = _evaluate_synthetic(bars, config=VwapPbConfig(min_pct_bars_above_vwap=Decimal("50")))
    setup = relaxed.setup
    assert setup is not None
    assert (setup.stop_price, setup.target_price) == (Decimal("99.50"), Decimal("101.00"))
    assert setup.levels["stop_distance"] == Decimal("0.50")
    stricter = VwapPbConfig(min_pct_bars_above_vwap=Decimal("50.01"))
    assert _evaluate_synthetic(bars, config=stricter).unmet == TOO_FEW_BARS_ABOVE_VWAP


def test_a_pullback_that_breaks_down_through_vwap_does_not_signal():
    shapes = list(TREND_DAY)
    shapes[9] = ("-0.50", "-0.40", "-0.90", "-0.70")  # 10:15-10:20 now dips to 99.10
    bars = _session(shapes)
    result = _evaluate_synthetic(bars)
    assert _no_signal(result) == (NO_SIGNAL_YET, PULLBACK_BROKE_DOWN, True)
    # 99.10 sits 0.27 under that bar's VWAP, beyond half of ATR 0.4062.
    assert result.pullback_low == Decimal("99.10")
    assert result.pullback_vwap is not None and result.atr is not None
    assert result.pullback_vwap.quantize(Q4) == Decimal("99.3733")
    assert result.atr.quantize(Q4) == Decimal("0.4062")
    assert result.pullback_low < result.pullback_vwap - result.atr / 2

    # With a whole-ATR floor the same bars price: stop floor(99.10 - 0.0406).
    loose = _evaluate_synthetic(bars, config=VwapPbConfig(pullback_floor_atr=Decimal("1")))
    assert loose.setup is not None
    assert (loose.setup.stop_price, loose.setup.target_price) == (
        Decimal("99.05"),
        Decimal("101.90"),
    )


def test_a_pullback_that_never_reaches_vwap_does_not_signal():
    result = _evaluate_synthetic(_session(SHALLOW_DAY))
    assert _no_signal(result) == (NO_SIGNAL_YET, PULLBACK_MISSED_VWAP, True)
    # The lowest low of the four bars, 99.70, stayed 0.20 above VWAP 99.50:
    # more than 0.25 x ATR 0.3796.
    assert (result.pullback_low, result.pullback_vwap) == (Decimal("99.70"), Decimal("99.50"))
    assert result.atr is not None
    assert result.pullback_low > result.pullback_vwap + result.atr / 4


def test_a_tied_pullback_low_is_measured_at_the_most_recent_bar():
    shapes = list(TREND_DAY)
    shapes[8] = ("-0.30", "-0.20", "-0.80", "-0.50")  # 10:10-10:15 also dips to 99.20
    result = _evaluate_synthetic(_session(shapes))
    # Both the 10:10-10:15 and 10:15-10:20 bars low 99.20: the band is measured
    # at the later one, the bar the signal reclaims from (VWAP 99.37, not 99.3704).
    assert result.setup is not None and result.pullback_low == Decimal("99.20")
    assert result.pullback_vwap == Decimal("99.37")


def test_a_bar_closing_exactly_at_its_running_vwap_is_not_above_it():
    shapes = list(TREND_DAY)
    shapes[0] = ("-2.00", "-1.70", "-2.10", "-1.90")  # 09:30-09:35 closes at its typical price
    result = _evaluate_synthetic(_session(shapes))
    assert result.setup is not None
    assert result.setup.levels["pct_bars_above_vwap"] == Decimal("83.33")  # 10 of 12


def test_the_signal_bar_needs_one_point_two_times_the_session_average_volume():
    # The eleven earlier bars traded 1,000 lots each: 1,200 is the threshold.
    at_threshold = _evaluate_synthetic(_session(signal_volume="1200"))
    assert at_threshold.setup is not None
    assert at_threshold.setup.levels["signal_bar_volume"] == Decimal("120000")
    thin = _evaluate_synthetic(_session(signal_volume="1199"))
    assert _no_signal(thin) == (NO_SIGNAL_YET, SIGNAL_VOLUME_TOO_LOW, True)


def test_only_the_latest_completed_bar_may_signal():
    bars = _session()
    signal_end = bars[-1].interval_end_utc

    # 10:30-10:35 closes 99.90, under the signal bar's 100.00 high: the 10:30
    # signal is stale and is never priced.
    later = [*bars, _bar(signal_end, ("0.00", "0.10", "-0.20", "-0.10"))]
    stale = _evaluate_synthetic(later)
    assert _no_signal(stale) == (NO_SIGNAL_YET, CLOSE_NOT_ABOVE_PREVIOUS_HIGH, True)
    assert stale.evaluated_bar == later[-1]

    # A feed one bar behind the clock never lets the older bar signal.
    lagging = _evaluate_synthetic(bars, now=signal_end + BAR + timedelta(seconds=5))
    assert (lagging.rejection_reason, lagging.retryable) == ("bar_session_stale", True)
    assert lagging.rejection_reason is not None and is_data_rejection(lagging.rejection_reason)

    # A missing interval would misstate VWAP and the trend share.
    gap = [bar for bar in bars if bar.interval_start_utc != _et(SESSION, 10, 0)]
    assert _evaluate_synthetic(gap).rejection_reason == "bar_session_incomplete"

    # Without Friday's bars, twelve session bars cannot seed ATR(14) yet.
    today_only = [bar for bar in bars if bar.interval_start_utc >= _et(SESSION, 9, 30)]
    unseeded = _evaluate_synthetic(today_only)
    assert (unseeded.rejection_reason, unseeded.retryable) == ("insufficient_atr_history", True)

    # A signal bar that closes under VWAP never signals.
    under = _evaluate_synthetic(_session((*TREND_DAY[:-1], ("-0.40", "-0.30", "-0.80", "-0.70"))))
    assert _no_signal(under) == (NO_SIGNAL_YET, CLOSE_NOT_ABOVE_VWAP, True)


def test_the_entry_window_runs_from_10_30_to_14_30_and_moves_in_on_a_half_day():
    strategy = VwapPbStrategy()
    assert entry_window_utc(strategy, SESSION) == (_et(SESSION, 10, 30), _et(SESSION, 14, 30))
    half_day = date(2026, 11, 27)  # XNYS closes at 13:00; entries keep an hour of runway
    assert strategy.entry_window(half_day) == (_et(half_day, 10, 30), _et(half_day, 12, 0))

    bars = _session()
    assert _evaluate_synthetic(bars).setup is not None  # signal bar ends at 10:30
    late_start = _evaluate_synthetic(bars, config=VwapPbConfig(window_start=time(10, 35)))
    assert _no_signal(late_start) == (NO_SIGNAL_YET, OUTSIDE_ENTRY_WINDOW, True)

    # A signal bar ending at the cutoff is never priced: the window has closed
    # by the time the bar is seen.
    at_cutoff = VwapPbConfig(window_start=time(10, 0), entry_cutoff=time(10, 30))
    unquoted = _evaluate_synthetic(bars, quoted=False, config=at_cutoff)
    assert (unquoted.rejection_reason, unquoted.retryable) == (QUOTE_REQUIRED, False)
    quoted = _evaluate_synthetic(bars, config=at_cutoff)
    assert (quoted.rejection_reason, quoted.retryable) == ("signal_after_cutoff", False)
    one_bar_later = VwapPbConfig(window_start=time(10, 0), entry_cutoff=time(10, 35))
    assert _evaluate_synthetic(bars, config=one_bar_later).setup is not None

    # An 11:30 ET close pulls the cutoff onto the 10:30 signal itself.
    early_close = VwapPbStrategy(session_close=lambda day: _et(day, 11, 30))
    assert early_close.entry_window(SESSION) == (_et(SESSION, 10, 30), _et(SESSION, 10, 30))
    now = _after(bars)
    closed = early_close.evaluate(
        candidate=_syn_candidate(),
        bars=bars,
        quote=_syn_quote(now),
        session_date=SESSION,
        run_id=RUN_ID,
        strategy_id="strategy-vwap-pb",
        strategy_version=1,
        reference_price="97.90",
        average_dollar_volume="25000000",
        candidate_input_fingerprint=None,
        now=now,
    )
    assert (closed.setup, closed.rejection_reason, closed.retryable) == (
        None,
        "signal_after_cutoff",
        False,
    )


def test_the_stop_distance_must_stay_between_half_and_one_and_a_half_percent():
    # Halving every move halves ATR (0.20) and the pullback (low 99.60): stop
    # floor(99.60 - 0.02) = 99.58 is only 0.42% under the 100.00 entry.
    half = Decimal("0.5")
    tight = _evaluate_synthetic(_session(scale=half), reference_price=_reference(half))
    assert (tight.rejection_reason, tight.retryable) == (STOP_TOO_TIGHT, True)
    assert (tight.atr, tight.pullback_low) == (Decimal("0.20"), Decimal("99.60"))
    # Doubling them (ATR 0.80, low 98.40): stop floor(98.40 - 0.08) = 98.32 is 1.68%.
    double = Decimal("2")
    wide = _evaluate_synthetic(_session(scale=double), reference_price=_reference(double))
    assert (wide.rejection_reason, wide.retryable) == (STOP_TOO_WIDE, True)
    assert (wide.atr, wide.pullback_low) == (Decimal("0.80"), Decimal("98.40"))
    assert not is_data_rejection(STOP_TOO_TIGHT) and not is_data_rejection(STOP_TOO_WIDE)

    # Both bounds are inclusive and apply to the tick-rounded 0.84 distance.
    bars = _session()

    def bounded(low: str, high: str) -> VwapPbResult:
        config = VwapPbConfig(min_stop_pct=Decimal(low), max_stop_pct=Decimal(high))
        return _evaluate_synthetic(bars, config=config)

    assert bounded("0.84", "1.5").setup is not None
    assert bounded("0.85", "1.5").rejection_reason == STOP_TOO_TIGHT
    assert bounded("0.5", "0.84").setup is not None
    assert bounded("0.5", "0.83").rejection_reason == STOP_TOO_WIDE


def test_quote_checks_and_the_entry_chase_are_retryable():
    bars = _session()
    now = _after(bars)
    # The entry may sit at most 20 bps over the 100.00 close: 100.20 prices.
    capped = _evaluate_synthetic(bars, quote=_syn_quote(now, bid="100.19", ask="100.20"))
    setup = capped.setup
    assert setup is not None
    assert (setup.entry_limit, setup.stop_price, setup.target_price) == (
        Decimal("100.20"),
        Decimal("99.16"),
        Decimal("102.28"),
    )
    assert setup.levels["stop_distance"] == Decimal("1.04")
    chased = _evaluate_synthetic(bars, quote=_syn_quote(now, bid="100.20", ask="100.21"))
    assert (chased.rejection_reason, chased.retryable) == (ENTRY_CHASE_TOO_WIDE, True)
    assert not is_data_rejection(ENTRY_CHASE_TOO_WIDE)

    for quote, reason in (
        (_syn_quote(now, bid="99.84", ask="100.00"), "quote_spread_too_wide"),  # 16 bps
        (_syn_quote(now, age=timedelta(seconds=3)), "quote_stale"),
        (_syn_quote(now, con_id=SYN_CON_ID + 1), "quote_contract_mismatch"),
    ):
        result = _evaluate_synthetic(bars, quote=quote)
        assert (result.setup, result.rejection_reason, result.retryable) == (None, reason, True)
        assert is_data_rejection(reason)


@pytest.mark.parametrize(
    ("reference_price", "average_dollar_volume", "reason"),
    [
        ("9.99", "25000000", "candidate_price_out_of_range"),
        ("300.01", "25000000", "candidate_price_out_of_range"),
        ("97.90", "19999999", "candidate_dollar_volume_too_low"),
    ],
)
def test_qualification_rejects_before_any_bar_is_read(
    reference_price, average_dollar_volume, reason
):
    result = evaluate_vwap_pb(
        candidate=_syn_candidate(),
        bars=(),
        quote=None,
        session_date=SESSION,
        run_id=RUN_ID,
        strategy_id="strategy-vwap-pb",
        strategy_version=1,
        reference_price=reference_price,
        average_dollar_volume=average_dollar_volume,
        now=_et(SESSION, 10, 30, 5),
    )
    assert (result.rejection_reason, result.retryable) == (reason, False)


# --- The shadow pipeline ------------------------------------------------------


class _Provider:
    def __init__(self) -> None:
        self.setups: list[RuleSetup] = []

    async def propose_trade(self, *, setup: RuleSetup) -> AIReview:
        self.setups.append(setup)
        return AIReview(
            setup_fingerprint=setup.fingerprint(),
            decision="PROPOSE",
            confidence="0.7",
            rationale="VWAP pullback review.",
            model_id="test-model",
            prompt_version=VWAP_REVIEW_PROMPT_ID,
            response_hash=HASH,
            latency_ms=1,
            cost_usd="0",
        )


class _Recorder:
    def __init__(self) -> None:
        self.decisions: list[object] = []

    async def __call__(self, **kwargs: object) -> None:
        self.decisions.append(kwargs["decision"])


def _assess_context() -> dict[str, Any]:
    spec = vwap_pb_strategy_spec()
    created = _et(SESSION, 8, 0)
    strategy = StrategyRecord(
        strategy_id="strategy-vwap-pb",
        owner_id="owner-a",
        version=1,
        content_hash=spec.fingerprint(),
        spec=spec,
        state=StrategyState.SHADOW,
        created_at=created,
        updated_at=created,
    )
    return {
        "run_id": RUN_ID,
        "strategy": strategy,
        "portfolio": PortfolioSnapshot(observed_at=_et(SESSION, 9, 0), net_liquidation="100000"),
        "policy": RiskPolicy(),
        "session_date": SESSION,
        "admitted_symbols": frozenset({"SYN"}),
    }


def _item(
    bars: Sequence[CompletedBar],
    *,
    quote: QuoteSnapshot | None = None,
    reference_price: str = "97.90",
) -> CandidateInput:
    return CandidateInput(
        candidate=_syn_candidate(),
        bars=tuple(bars),
        quote=quote,
        reference_price=reference_price,
        average_dollar_volume="25000000",
    )


@pytest.mark.anyio
async def test_the_shadow_pipeline_requests_a_quote_then_accepts_the_setup():
    provider, recorder = _Provider(), _Recorder()
    pipeline = ShadowPipeline(
        proposal_provider=provider, decision_recorder=recorder, strategy=VwapPbStrategy()
    )
    assert pipeline.strategy.strategy_kind == "vwap_pb"
    assert entry_cutoff_utc(SESSION, pipeline.strategy) == _et(SESSION, 14, 30)
    bars = _session()
    now = _after(bars)

    pending = await pipeline.assess_one(item=_item(bars), now=now, **_assess_context())
    assert pending.decision is None and pending.needs_quote is True
    assert pending.pending_reason == QUOTE_REQUIRED and provider.setups == []

    accepted = await pipeline.assess_one(
        item=_item(bars, quote=_syn_quote(now)), now=now, **_assess_context()
    )
    decision = accepted.decision
    assert decision is not None and decision.outcome == DecisionOutcome.SHADOW_ACCEPTED
    [setup] = provider.setups
    assert setup.strategy_kind == "vwap_pb" and set(setup.levels) == SETUP_LEVELS
    assert decision.setup_fingerprint == setup.fingerprint()
    assert decision.risk is not None and decision.risk.approved is True
    # The 15% notional cap binds on a 100k book: 150 shares at 100.00.
    assert decision.risk.intent is not None and decision.risk.intent.quantity == 150
    assert recorder.decisions == [decision]


@pytest.mark.anyio
async def test_retryable_skips_stay_pending_until_the_14_30_cutoff():
    provider, recorder = _Provider(), _Recorder()
    pipeline = ShadowPipeline(
        proposal_provider=provider, decision_recorder=recorder, strategy=VwapPbStrategy()
    )
    bars = _session()
    now = _after(bars)
    chased = await pipeline.assess_one(
        item=_item(bars, quote=_syn_quote(now, bid="100.20", ask="100.21")),
        now=now,
        **_assess_context(),
    )
    assert chased.decision is None and chased.needs_quote is False
    assert chased.pending_reason == ENTRY_CHASE_TOO_WIDE

    double = Decimal("2")
    wide_item = _item(
        _session(scale=double), quote=_syn_quote(now), reference_price=_reference(double)
    )
    wide = await pipeline.assess_one(item=wide_item, now=now, **_assess_context())
    assert wide.decision is None and wide.pending_reason == STOP_TOO_WIDE

    # The day then holds flat through 14:30: the cutoff sweep is terminal.
    flat = [_bar(_et(SESSION, 10, 30) + BAR * index, FLAT) for index in range(48)]
    full_day = [*bars, *flat]
    assert full_day[-1].interval_end_utc == _et(SESSION, 14, 30)
    final = await pipeline.assess_one(
        item=_item(full_day), now=_et(SESSION, 14, 30), **_assess_context()
    )
    assert final.decision is not None
    assert final.decision.outcome == DecisionOutcome.RULE_REJECTED
    assert provider.setups == []
    assert recorder.decisions == [final.decision]


# --- Replays of the real 2026-10-12 session -----------------------------------


@dataclass(frozen=True)
class _Replay:
    """One real fixture: bars, the production liquidity profile, the live log."""

    symbol: str
    con_id: int
    bars: tuple[CompletedBar, ...]
    reference_price: Decimal
    average_dollar_volume: Decimal
    live: dict[str, Any]

    def candidate(self, scanner_run_id: str = "scanner-replay", rank: int = 1) -> Candidate:
        return Candidate(
            id=f"{scanner_run_id}-{self.con_id}",
            scanner_run_id=scanner_run_id,
            con_id=self.con_id,
            symbol=self.symbol,
            primary_exchange="NYSE",
            rank=rank,
            admission_status="admitted",
        )

    def bars_until(self, now: datetime) -> tuple[CompletedBar, ...]:
        return tuple(bar for bar in self.bars if bar.interval_end_utc <= now)


def _load(name: str) -> _Replay:
    raw = json.loads((FIXTURES / f"{name}_2026-10-12.json").read_text())
    con_id = raw["con_id"]
    bars = tuple(
        CompletedBar(
            con_id=con_id,
            interval_start_utc=datetime.fromisoformat(item["interval_start_utc"]),
            interval_end_utc=datetime.fromisoformat(item["interval_end_utc"]),
            open=item["open"],
            high=item["high"],
            low=item["low"],
            close=item["close"],
            volume=item["volume"],
            rth=True,
            source="ibkr",
            data_mode="historical",
        )
        for item in raw["bars"]
    )
    daily = DailyBarBatch(
        bars=tuple(
            IBKRDailyBar(
                con_id=con_id,
                session_date=date.fromisoformat(item["session_date"]),
                close=Decimal(item["close"]),
                volume=Decimal(item["volume"]),
            )
            for item in raw["daily_bars"]
        )
    )
    # The same 20-session profile a live run seeds from IBKR daily bars:
    # reference_price is Friday's close, so day change is measured against it.
    profile = liquidity_profile(daily, session_date=SESSION, volume_multiplier=VOLUME_MULTIPLIER)
    return _Replay(
        symbol=raw["symbol"],
        con_id=con_id,
        bars=bars,
        reference_price=profile.reference_price,
        average_dollar_volume=profile.average_dollar_volume,
        live=raw["live_context"],
    )


BF_B = _load("bf_b")
FIGS = _load("figs")


def _evaluate_replay(replay: _Replay, now: datetime) -> VwapPbResult:
    return evaluate_vwap_pb(
        candidate=replay.candidate(),
        bars=replay.bars_until(now),
        quote=None,
        session_date=SESSION,
        run_id="run-replay",
        strategy_id="strategy-replay",
        strategy_version=1,
        reference_price=replay.reference_price,
        average_dollar_volume=replay.average_dollar_volume,
        now=now,
    )


def _replay_window(replay: _Replay) -> dict[str, VwapPbResult]:
    """Each poll inside the window, 5 s after a bar close, keyed by bar end (ET)."""
    polls: dict[str, VwapPbResult] = {}
    bar_end = _et(SESSION, 10, 30)
    while bar_end < _et(SESSION, 14, 30):
        polls[f"{bar_end.astimezone(NEW_YORK):%H:%M}"] = _evaluate_replay(
            replay, bar_end + timedelta(seconds=5)
        )
        bar_end += BAR
    return polls


def _stamps(start: str, end: str) -> set[str]:
    first = datetime.combine(SESSION, time.fromisoformat(start))
    last = datetime.combine(SESSION, time.fromisoformat(end))
    return {f"{first + BAR * step:%H:%M}" for step in range(int((last - first) / BAR) + 1)}


def test_the_real_2026_10_12_session_never_signals_vwap_pb():
    assert (BF_B.reference_price, FIGS.reference_price) == (Decimal("26.39"), Decimal("15.78"))
    bf_b, figs = _replay_window(BF_B), _replay_window(FIGS)
    for polls in (bf_b, figs):
        assert len(polls) == 48  # bar ends 10:30 through 14:25 ET
        # No poll ever asked for a quote: neither symbol signaled in the window.
        assert {result.rejection_reason for result in polls.values()} == {NO_SIGNAL_YET}
        assert all(result.setup is None and result.retryable for result in polls.values())

    # BF B: under +1% on every poll before MOMO's 11:50 breakout, then short of
    # 60% of bars above VWAP until 13:15, then no bar closed above the
    # previous high on 1.2x volume.
    assert Counter(result.unmet for result in bf_b.values()) == {
        DAY_CHANGE_TOO_LOW: 17,
        TOO_FEW_BARS_ABOVE_VWAP: 16,
        CLOSE_NOT_ABOVE_PREVIOUS_HIGH: 10,
        SIGNAL_VOLUME_TOO_LOW: 5,
    }
    assert {stamp for stamp, result in bf_b.items() if result.unmet == DAY_CHANGE_TOO_LOW} == (
        _stamps("10:30", "11:45") | {"12:20"}
    )
    breakout = bf_b["11:50"]
    assert breakout.unmet == TOO_FEW_BARS_ABOVE_VWAP
    assert breakout.vwap is not None and breakout.day_change_pct is not None
    assert breakout.pct_bars_above_vwap is not None
    # The same VWAP the live MOMO run logged for this bar.
    assert breakout.vwap.quantize(Q4) == Decimal(BF_B.live["vwap"]) == Decimal("26.5586")
    assert breakout.day_change_pct.quantize(Q2) == Decimal("1.40")
    assert breakout.pct_bars_above_vwap.quantize(Q2) == Decimal("35.71")  # 10 of 28 bars
    assert all(
        result.unmet in {CLOSE_NOT_ABOVE_PREVIOUS_HIGH, SIGNAL_VOLUME_TOO_LOW}
        for stamp, result in bf_b.items()
        if stamp >= "13:15"
    )

    # FIGS: a trend day on every poll (at least +7%, at least 83% of bars above
    # VWAP), but its three trigger bars had pulled back nowhere near VWAP.
    assert Counter(result.unmet for result in figs.values()) == {
        CLOSE_NOT_ABOVE_PREVIOUS_HIGH: 34,
        SIGNAL_VOLUME_TOO_LOW: 12,
        PULLBACK_MISSED_VWAP: 2,
    }
    assert min(result.day_change_pct or Decimal("0") for result in figs.values()) > 7
    assert min(result.pct_bars_above_vwap or Decimal("0") for result in figs.values()) > 83
    near_misses = {
        stamp: result for stamp, result in figs.items() if result.unmet == PULLBACK_MISSED_VWAP
    }
    assert sorted(near_misses) == ["12:35", "13:25"]
    for stamp, (low, vwap, atr) in {
        "12:35": ("17.28", "16.9153", "0.1101"),
        "13:25": ("17.35", "17.0712", "0.1072"),
    }.items():
        result = near_misses[stamp]
        assert result.pullback_vwap is not None and result.atr is not None
        assert result.pullback_low == Decimal(low)
        assert result.pullback_vwap.quantize(Q4) == Decimal(vwap)
        assert result.atr.quantize(Q4) == Decimal(atr)
        assert result.pullback_low > result.pullback_vwap + result.atr / 4

    # The 14:30 bar met every trend and trigger rule for both symbols, ended at
    # the cutoff, and missed VWAP anyway.
    for replay, low, vwap in ((BF_B, "26.80", "26.5934"), (FIGS, "17.57", "17.1707")):
        late = _evaluate_replay(replay, _et(SESSION, 14, 30, 5))
        assert _no_signal(late) == (NO_SIGNAL_BEFORE_CUTOFF, PULLBACK_MISSED_VWAP, False)
        assert late.pullback_vwap is not None
        assert late.pullback_low == Decimal(low)
        assert late.pullback_vwap.quantize(Q4) == Decimal(vwap)
    figs_close = _evaluate_replay(FIGS, _et(SESSION, 14, 30, 5))
    assert figs_close.vwap is not None
    assert figs_close.vwap.quantize(Q4) == Decimal(FIGS.live["vwap"])


@pytest.mark.anyio
async def test_the_leased_session_replay_of_the_real_day_trades_nothing(tmp_path, monkeypatch):
    """Replay the real day bar by bar, the way the session runner polls."""
    from config import cfg
    from db.core import init_db

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "vwap-pb.db"))
    await init_db()
    strategy = await ensure_strategy(owner_id="owner-a", spec=vwap_pb_strategy_spec())
    assert strategy.spec.name == "VWAP_PB_V1" and strategy.state == StrategyState.SHADOW

    run = Run(
        id="run-vwap-pb-replay",
        owner_id="owner-a",
        source_sha="abcdef1",
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.version,
        policy_fingerprints={
            "risk": RiskPolicy().fingerprint(),
            "vwap_pb": VwapPbConfig().fingerprint(),
        },
        session_date=SESSION,
        mode=EngineMode.SHADOW,
    )
    parameters = {"source": "screener_hotlist", "top": 35}
    scanner = ScannerRun(
        id=f"{run.id}-scanner",
        run_id=run.id,
        parameters=parameters,
        parameters_fingerprint=fingerprint_payload(parameters),
        status="completed",
        started_at=_et(SESSION, 10, 25),
    )
    replays = {replay.symbol: replay for replay in (BF_B, FIGS)}
    candidates = {
        symbol: replay.candidate(scanner.id, rank)
        for rank, (symbol, replay) in enumerate(replays.items(), 1)
    }

    def item(symbol: str, now: datetime) -> CandidateInput:
        replay = replays[symbol]
        return CandidateInput(
            candidate=candidates[symbol],
            bars=replay.bars_until(now),
            quote=None,
            reference_price=replay.reference_price,
            average_dollar_volume=replay.average_dollar_volume,
        )

    provider = _Provider()
    clock = {"now": _et(SESSION, 10, 30, 5)}
    runner = RepositoryBackedShadowRunner(
        pipeline=ShadowPipeline(proposal_provider=provider, strategy=VwapPbStrategy()),
        clock=lambda: clock["now"],
        lease_ttl=timedelta(hours=6),
        lease_renew_interval=timedelta(hours=1),
    )
    portfolio = PortfolioSnapshot(observed_at=clock["now"], net_liquidation="665000")
    reasons: dict[str, set[str | None]] = {symbol: set() for symbol in replays}
    async with runner.open_session(
        run=run,
        scanner_run=scanner,
        strategy=strategy,
        candidates=tuple(candidates.values()),
        policy=RiskPolicy(),
        holder_id="replay-vwap-pb",
    ) as session:
        assert session.cutoff == _et(SESSION, 14, 30)
        latest: dict[str, CandidateInput] = {}
        poll, polls = _et(SESSION, 10, 30, 5), 0
        while poll < session.cutoff:
            clock["now"] = poll
            for candidate in session.pending():
                latest[candidate.symbol] = item(candidate.symbol, poll)
                assessment = await session.assess(
                    latest[candidate.symbol], portfolio=portfolio, now=poll
                )
                assert assessment.decision is None and assessment.needs_quote is False
                reasons[candidate.symbol].add(assessment.pending_reason)
            polls += 1
            poll += BAR
        assert polls == 48
        assert sorted(candidate.symbol for candidate in session.pending()) == ["BF B", "FIGS"]
        clock["now"] = session.cutoff
        final = await session.finalize(
            list(latest.values()), portfolio=portfolio, now=session.cutoff
        )

    assert reasons == {"BF B": {NO_SIGNAL_YET}, "FIGS": {NO_SIGNAL_YET}}
    for candidate in candidates.values():
        assert final[candidate.id].outcome == DecisionOutcome.RULE_REJECTED
        assert final[candidate.id].ai_review is None
    assert provider.setups == []


# --- Identity -------------------------------------------------------------------


def test_vwap_pb_identity_is_consistent_across_rules_catalogue_and_prompt():
    config = VwapPbConfig()
    assert (config.window_start, config.entry_cutoff, config.close_buffer) == (
        time(10, 30),
        time(14, 30),
        timedelta(hours=1),
    )
    assert (
        config.min_day_change_pct,
        config.min_pct_bars_above_vwap,
        config.pullback_bars,
        config.pullback_floor_atr,
        config.pullback_ceiling_atr,
        config.bar_volume_multiple,
        config.stop_buffer_atr,
        config.min_stop_pct,
        config.max_stop_pct,
        config.target_reward_risk,
        config.max_spread_bps,
        config.max_chase_bps,
        config.volume_multiplier,
    ) == (
        Decimal("1"),
        Decimal("60"),
        4,
        Decimal("0.5"),
        Decimal("0.25"),
        Decimal("1.2"),
        Decimal("0.1"),
        Decimal("0.5"),
        Decimal("1.5"),
        Decimal("2"),
        Decimal("15"),
        Decimal("20"),
        Decimal("100"),
    )
    spec = vwap_pb_strategy_spec()
    assert VWAP_PB_RULES_VERSION == VWAP_PB_STRATEGY_NAME == spec.name == "VWAP_PB_V1"
    # The PAPER bridge sends orders only for MOMO_HOD strategies: this stays shadow.
    assert not spec.name.startswith("MOMO_HOD")
    for term in ("Shadow only", "1%", "60%", "0.5 ATR(14)", "0.25 ATR", "1.2x", "0.1 ATR"):
        assert term in spec.rationale
    for term in ("0.5%", "1.5%", "2R", "10:30-14:30 ET", "VwapPbConfig"):
        assert term in spec.rationale
    strategy = VwapPbStrategy()
    assert isinstance(strategy, StrategyEvaluator)
    assert strategy.strategy_kind == VWAP_PB_STRATEGY_KIND == "vwap_pb"
    assert strategy.config == config

    # Every rule parameter is part of the run's rule fingerprint.
    baseline = config.fingerprint()
    assert baseline != MomoConfig().fingerprint()
    for name, value in (
        ("window_start", time(10, 35)),
        ("entry_cutoff", time(14, 25)),
        ("min_day_change_pct", Decimal("1.5")),
        ("min_pct_bars_above_vwap", Decimal("65")),
        ("pullback_bars", 5),
        ("pullback_floor_atr", Decimal("0.6")),
        ("pullback_ceiling_atr", Decimal("0.3")),
        ("bar_volume_multiple", Decimal("1.3")),
        ("stop_buffer_atr", Decimal("0.2")),
        ("min_stop_pct", Decimal("0.4")),
        ("max_stop_pct", Decimal("1.4")),
    ):
        assert VwapPbConfig(**{name: value}).fingerprint() != baseline

    # The review prompt describes these exact rules and every level a setup carries.
    assert VWAP_REVIEW_PROMPT_ID == "vwap-review-v1"
    for term in (
        'strategy_kind "vwap_pb"',
        "VWAP_PB_V1",
        "at least 1% above the previous session's close",
        "at least 60% of the session's bars",
        "four bars before the signal bar",
        "0.5 ATR(14) below",
        "0.25 ATR above",
        "1.2 times the mean volume",
        "one tenth of an ATR(14)",
        "below 0.5% or above 1.5% of the entry",
        "(2R)",
        "PROPOSE",
        "ABSTAIN",
        "When the evidence is ambiguous, ABSTAIN.",
        "You cannot change any price, quantity, symbol, or order field.",
        'When market_context is absent or {"available": false}, judge the setup on its own.',
        *sorted(SETUP_LEVELS),
    ):
        assert term in VWAP_REVIEW_V1
    assert "momo_hod" not in VWAP_REVIEW_V1
    # Once a recorded review names vwap-review-v1 this text must never change;
    # new wording needs a new prompt id.
    assert (
        sha256(VWAP_REVIEW_V1.encode("utf-8")).hexdigest()
        == "8d84bc22f53fa2a6d4eac5f8e71568a004ecfc7b38f9c702e5b9a36f89c513ef"
    )
    # The earliest window start leaves the opening bar and four pullback bars.
    assert VwapPbConfig(window_start=time(9, 55)).window_start == time(9, 55)


@pytest.mark.parametrize(
    "overrides",
    [
        {"window_start": time(9, 50)},
        {"window_start": time(14, 30)},
        {"entry_cutoff": time(16, 5)},
        {"pullback_bars": 0},
        {"atr_period": 0},
        {"close_buffer": timedelta(minutes=-1)},
        {"min_stop_pct": Decimal("0")},
        {"min_stop_pct": Decimal("1.5")},
        {"max_stop_pct": Decimal("100")},
        {"min_pct_bars_above_vwap": Decimal("100.01")},
        {"pullback_floor_atr": Decimal("-0.1")},
        {"target_reward_risk": Decimal("1.9")},
        {"volume_multiplier": Decimal("0")},
    ],
)
def test_invalid_vwap_pb_parameters_are_rejected(overrides):
    with pytest.raises(ValueError):
        VwapPbConfig(**overrides)
