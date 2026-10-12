"""GAP_GO_V1: a gap of 3%+ on real pre-market volume, bought on the first
five-minute close above the higher of the pre-market high and the first bar's high.

The synthetic day: a quiet prior session closing at 100, thirty pre-market bars
from 07:00 ET (high 105.00, 150,000 shares against a 5,000,000-share average
day), a 09:30 bar opening at 104.00 (a 4% gap, high 105.20, low 103.60), and the
09:40-09:45 bar as the first close above the 105.20 trigger.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from trading.contracts import (
    AIReview,
    Candidate,
    CompletedBar,
    DecisionOutcome,
    FixedUniverse,
    PortfolioSnapshot,
    QuoteSnapshot,
    RiskPolicy,
    RuleCondition,
    RuleSetup,
    StrategyRecord,
    StrategySpec,
    StrategyState,
)
from trading.orchestration import CandidateInput, ShadowPipeline
from trading.orchestration.pipeline import entry_cutoff_utc
from trading.rules.gap_go import (
    GAP_GO_RULES_VERSION,
    GAP_GO_STRATEGY_KIND,
    GAP_REVIEW_PROMPT_ID,
    GAP_REVIEW_V1,
    GAP_TOO_SMALL,
    NO_PREMARKET_DATA,
    NO_SIGNAL_BEFORE_CUTOFF,
    PREMARKET_VOLUME_TOO_LOW,
    STOP_TOO_TIGHT,
    STOP_TOO_WIDE,
    GapGoConfig,
    GapGoResult,
    GapGoStrategy,
    evaluate_gap_go,
    gap_go_bars,
    gap_go_entry_window,
    gap_go_strategy_spec,
)
from trading.rules.orb import NO_SIGNAL_YET, QUOTE_REQUIRED
from trading.rules.strategy import (
    NO_BARS_SUPPLIED,
    StrategyEvaluator,
    entry_window_utc,
    is_data_rejection,
)

NEW_YORK = ZoneInfo("America/New_York")
SESSION = date(2026, 10, 13)
PRIOR = date(2026, 10, 12)
HALF_DAY = date(2026, 11, 27)
BAR = timedelta(minutes=5)
CON_ID = 4242
HASH = "f" * 64
# 500M dollars at the 100 prior close: a 5,000,000-share day, so 2% is 100,000.
AVERAGE_DOLLAR_VOLUME = "500000000"
FIRST = ("104", "105.2", "103.6", "104.8")
NO_BREAK = ("104.8", "105.1", "104.5", "105")
BREAKOUT = ("105", "105.6", "104.9", "105.5")
ABOVE = ("105.5", "106", "105.4", "105.8")
BELOW = ("105.8", "105.9", "104.9", "105")
LEVEL_NAMES = {
    "gap_pct",
    "premarket_high",
    "premarket_volume",
    "first_bar_high",
    "first_bar_low",
    "trigger",
    "stop_distance",
}

Shape = tuple[str, str, str, str]


def _et(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=NEW_YORK).astimezone(
        timezone.utc
    )


def _bar(start: datetime, shape: Shape, *, volume: str = "1000", rth: bool = True) -> CompletedBar:
    open_price, high, low, close = shape
    return CompletedBar(
        con_id=CON_ID,
        interval_start_utc=start,
        interval_end_utc=start + BAR,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        rth=rth,
        source="ibkr",
        data_mode="historical",
    )


def _prior_session(half_range: str = "0.25") -> list[CompletedBar]:
    half = Decimal(half_range)
    quiet = ("100", str(Decimal("100") + half), str(Decimal("100") - half), "100")
    return [_bar(_et(PRIOR, 9, 30) + BAR * index, quiet) for index in range(78)]


def _premarket(*, count: int = 30, volume: str = "50", high: str = "105") -> list[CompletedBar]:
    bars = [
        _bar(_et(SESSION, 7, 0) + BAR * index, ("104.2", "104.6", "104", "104.4"), volume=volume, rth=False)
        for index in range(count)
    ]
    if count > 10:
        bars[10] = _bar(_et(SESSION, 7, 50), ("104.4", high, "104.3", "104.5"), volume=volume, rth=False)
    return bars


def _day(
    *,
    first: Shape = FIRST,
    session: Sequence[Shape] = (NO_BREAK, BREAKOUT),
    premarket: Sequence[CompletedBar] | None = None,
    prior_half_range: str = "0.25",
    with_prior: bool = True,
) -> tuple[CompletedBar, ...]:
    """Prior session, pre-market, then ``first`` at 09:30 and ``session`` from 09:35."""
    open_bar = _bar(_et(SESSION, 9, 30), first, volume="3000")
    later = [_bar(_et(SESSION, 9, 35) + BAR * index, shape, volume="2000") for index, shape in enumerate(session)]
    return (
        *(_prior_session(prior_half_range) if with_prior else ()),
        *(_premarket() if premarket is None else premarket),
        open_bar,
        *later,
    )


def _ending(hour: int, minute: int) -> datetime:
    """The poll five seconds after the bar ending at ``hour:minute`` ET."""
    return _et(SESSION, hour, minute, 5)


def _until(bars: Sequence[CompletedBar], now: datetime) -> tuple[CompletedBar, ...]:
    return tuple(bar for bar in bars if bar.interval_end_utc <= now)


def _candidate(candidate_id: str = "candidate-gap") -> Candidate:
    return Candidate(
        id=candidate_id,
        scanner_run_id="scanner-gap",
        con_id=CON_ID,
        symbol="GAPR",
        primary_exchange="NASDAQ",
        rank=1,
        admission_status="admitted",
    )


def _quote(now: datetime, *, bid: str = "105.49", ask: str = "105.50", **overrides: Any) -> QuoteSnapshot:
    values: dict[str, Any] = {
        "con_id": CON_ID,
        "bid": bid,
        "ask": ask,
        "observed_at": now,
        "min_tick": "0.01",
    }
    values.update(overrides)
    return QuoteSnapshot(**values)


def _evaluate(
    bars: Sequence[CompletedBar],
    now: datetime,
    *,
    quote: QuoteSnapshot | None = None,
    config: GapGoConfig = GapGoConfig(),
    **overrides: Any,
) -> GapGoResult:
    params: dict[str, Any] = {
        "candidate": _candidate(),
        "bars": _until(bars, now),
        "quote": quote,
        "session_date": SESSION,
        "run_id": "run-gap",
        "strategy_id": "strategy-gap",
        "strategy_version": 1,
        "reference_price": "100",
        "average_dollar_volume": AVERAGE_DOLLAR_VOLUME,
        "now": now,
        "config": config,
    }
    params.update(overrides)
    return evaluate_gap_go(**params)


def _priced(bars: Sequence[CompletedBar], now: datetime, **kwargs: Any) -> GapGoResult:
    return _evaluate(bars, now, quote=_quote(now), **kwargs)


# --- the signal -------------------------------------------------------------


def test_synthetic_gap_day_signals_once_on_the_first_close_above_the_trigger():
    # 09:45 breaks out, 09:50 stays above, 09:55 dips, 10:00 re-breaks, then drifts above.
    bars = _day(session=(NO_BREAK, BREAKOUT, ABOVE, BELOW, ABOVE, *([ABOVE] * 6)))
    polls: dict[str, tuple[str | None, bool]] = {}
    poll = _ending(9, 35)
    while poll < _et(SESSION, 10, 30):
        result = _evaluate(bars, poll)
        assert result.setup is None
        polls[poll.astimezone(NEW_YORK).strftime("%H:%M")] = (result.rejection_reason, result.retryable)
        poll += BAR
    assert polls.pop("09:45") == (QUOTE_REQUIRED, True)
    assert set(polls.values()) == {(NO_SIGNAL_YET, True)}
    assert len(polls) == 10

    now = _ending(9, 45)
    result = _priced(bars, now)
    setup = result.setup
    assert setup is not None and result.rejection_reason is None and result.retryable is False
    assert setup.strategy_kind == GAP_GO_STRATEGY_KIND == "gap_go"
    assert (setup.symbol, setup.run_id, setup.candidate_id) == ("GAPR", "run-gap", "candidate-gap")
    assert setup.opening_range_high is None and setup.opening_range_low is None
    # ATR(14): 0.5 per quiet prior bar, then the 5.2 gap bar, 0.6 and 0.7.
    assert result.atr is not None and result.atr.quantize(Decimal("0.0001")) == Decimal("0.8104")
    # Entry 105.50 (the close; the ask is not higher). Stop = max(103.60 first-bar
    # low, 105.50 - 0.8104) = 104.6896, floored to 104.68; 2R target.
    assert (setup.entry_limit, setup.stop_price, setup.target_price) == (
        Decimal("105.50"),
        Decimal("104.68"),
        Decimal("107.14"),
    )
    assert setup.levels == {
        "gap_pct": Decimal("4.00"),
        "premarket_high": Decimal("105"),
        "premarket_volume": Decimal("150000"),
        "first_bar_high": Decimal("105.2"),
        "first_bar_low": Decimal("103.6"),
        "trigger": Decimal("105.2"),
        "stop_distance": Decimal("0.82"),
    }
    assert set(setup.levels) == LEVEL_NAMES
    assert setup.detected_at == _et(SESSION, 9, 45)

    signal = next(bar for bar in bars if bar.interval_end_utc == _et(SESSION, 9, 45))
    assert result.breakout_bar == result.evaluated_bar == signal
    provenance = setup.causal_provenance
    assert provenance is not None
    assert provenance.breakout_bar_fingerprint == signal.fingerprint()
    assert provenance.opening_bar_fingerprints is None
    # ATR history: the 78 prior-session bars and today's three RTH bars.
    assert len(provenance.atr_bar_fingerprints) == 81
    premarket = [bar for bar in bars if not bar.rth]
    assert len(premarket) == 30
    assert set(setup.bar_fingerprints) == {
        *(bar.fingerprint() for bar in premarket),
        *provenance.atr_bar_fingerprints,
    }
    assert len(setup.bar_fingerprints) == 111

    # The 10:00 re-break is a second breakout: never priced, even with a quote.
    rebreak = _priced(bars, _ending(10, 0))
    assert rebreak.setup is None and rebreak.rejection_reason == NO_SIGNAL_YET
    assert rebreak.breakout_bar == signal


@pytest.mark.parametrize(
    ("premarket_high", "trigger", "signals"),
    [
        # The first bar's 105.20 high is the trigger; the 105.50 close clears it.
        ("105", Decimal("105.2"), True),
        # A 105.60 pre-market high lifts the trigger above the 105.50 close.
        ("105.6", Decimal("105.6"), False),
    ],
)
def test_trigger_is_the_higher_of_the_premarket_high_and_the_first_bar_high(
    premarket_high, trigger, signals
):
    bars = _day(premarket=_premarket(high=premarket_high))
    result = _evaluate(bars, _ending(9, 45))
    assert result.trigger == trigger
    assert result.premarket_high == Decimal(premarket_high)
    assert result.rejection_reason == (QUOTE_REQUIRED if signals else NO_SIGNAL_YET)


def test_a_breakout_bar_must_close_above_the_trigger_not_just_trade_through_it():
    through = ("105", "105.9", "104.9", "105.2")  # high above, close equal to the trigger
    result = _evaluate(_day(session=(NO_BREAK, through)), _ending(9, 45))
    assert result.rejection_reason == NO_SIGNAL_YET and result.breakout_bar is None


# --- qualification --------------------------------------------------------------


@pytest.mark.parametrize(
    ("first_open", "gap_pct", "reason"),
    [
        ("102.99", Decimal("2.99"), GAP_TOO_SMALL),
        ("103", Decimal("3"), QUOTE_REQUIRED),
    ],
)
def test_a_gap_below_three_percent_is_rejected_once_the_first_bar_completes(
    first_open, gap_pct, reason
):
    bars = _day(first=(first_open, "105.2", "102.9", "104.8"))
    assert _evaluate(bars, _ending(9, 30) + timedelta(minutes=4, seconds=54)).rejection_reason == (
        NO_SIGNAL_YET
    )
    result = _evaluate(bars, _ending(9, 45))
    assert result.gap_pct == gap_pct
    assert result.rejection_reason == reason
    if reason == GAP_TOO_SMALL:
        # The open is final: rejected at once, not retried.
        assert result.retryable is False and not is_data_rejection(reason)
        assert _evaluate(bars, _ending(9, 35)).rejection_reason == GAP_TOO_SMALL


@pytest.mark.parametrize(
    ("average_dollar_volume", "reason"),
    [
        # 150,000 pre-market shares are exactly 2% of a 7,500,000-share day.
        ("750000000", QUOTE_REQUIRED),
        ("750000100", PREMARKET_VOLUME_TOO_LOW),
    ],
)
def test_pre_market_volume_must_reach_two_percent_of_average_daily_shares(
    average_dollar_volume, reason
):
    result = _evaluate(_day(), _ending(9, 45), average_dollar_volume=average_dollar_volume)
    assert result.premarket_volume == Decimal("150000")
    assert result.rejection_reason == reason
    if reason == PREMARKET_VOLUME_TOO_LOW:
        assert result.retryable is False and not is_data_rejection(reason)


def test_thin_pre_market_is_rejected_at_the_first_bar():
    # 30 bars x 33 lots x 100 = 99,000 shares, under 2% of a 5,000,000-share day.
    thin = _day(premarket=_premarket(volume="33"))
    result = _evaluate(thin, _ending(9, 35))
    assert result.rejection_reason == PREMARKET_VOLUME_TOO_LOW and result.retryable is False
    assert result.premarket_volume == Decimal("99000")


def test_missing_pre_market_bars_are_retryable_until_the_cutoff():
    # Prior-day after-hours bars are not today's pre-market.
    after_hours = [
        _bar(_et(PRIOR, 16, 0) + BAR * index, ("100", "100.2", "99.9", "100.1"), volume="500", rth=False)
        for index in range(12)
    ]
    for premarket in ([], after_hours):
        bars = _day(premarket=premarket)
        waiting = _evaluate(bars, _ending(9, 45))
        assert waiting.rejection_reason == NO_PREMARKET_DATA and waiting.retryable is True
        assert not is_data_rejection(NO_PREMARKET_DATA)
        final = _evaluate(bars, _et(SESSION, 10, 30))
        assert final.rejection_reason == NO_PREMARKET_DATA and final.retryable is False


def test_candidate_qualification_is_terminal():
    bars = _day()
    now = _ending(9, 45)
    assert _evaluate(bars, now, reference_price="9").rejection_reason == "candidate_price_out_of_range"
    low_liquidity = _evaluate(bars, now, average_dollar_volume="19999999")
    assert low_liquidity.rejection_reason == "candidate_dollar_volume_too_low"
    assert low_liquidity.retryable is False


# --- the window ---------------------------------------------------------------


def test_entry_window_is_09_35_to_10_30_new_york_on_full_and_half_days():
    strategy = GapGoStrategy()
    assert isinstance(strategy, StrategyEvaluator)
    for day in (SESSION, HALF_DAY):
        assert strategy.entry_window(day) == (_et(day, 9, 35), _et(day, 10, 30))
        assert entry_window_utc(strategy, day) == gap_go_entry_window(day)
    assert entry_cutoff_utc(SESSION, strategy) == _et(SESSION, 10, 30)


def test_nothing_signals_before_the_first_bar_completes():
    bars = _day()
    early = _evaluate(bars, _et(SESSION, 9, 34, 59))
    assert early.rejection_reason == NO_SIGNAL_YET and early.retryable is True
    assert early.first_bar is None
    first = _evaluate(bars, _ending(9, 35))
    assert first.rejection_reason == NO_SIGNAL_YET and first.first_bar is not None


def _late_breakout(bars_before_breakout: int) -> tuple[CompletedBar, ...]:
    return _day(session=(*([NO_BREAK] * bars_before_breakout), BREAKOUT, ABOVE))


def test_the_last_signal_bar_inside_the_window_is_priced():
    # 09:35 + 9 bars: the breakout bar is 10:20-10:25.
    bars = _late_breakout(9)
    now = _ending(10, 25)
    assert _evaluate(bars, now).rejection_reason == QUOTE_REQUIRED
    setup = _priced(bars, now).setup
    assert setup is not None and setup.detected_at == _et(SESSION, 10, 25)


def test_a_signal_on_the_bar_ending_at_the_cutoff_is_never_priced():
    bars = _late_breakout(10)  # breakout bar 10:25-10:30
    now = _ending(10, 30)
    unquoted = _evaluate(bars, now)
    assert unquoted.rejection_reason == QUOTE_REQUIRED and unquoted.retryable is False
    quoted = _priced(bars, now)
    assert quoted.setup is None
    assert quoted.rejection_reason == "signal_after_cutoff" and quoted.retryable is False


def test_a_breakout_after_the_cutoff_is_no_signal():
    bars = _late_breakout(11)  # breakout bar 10:30-10:35
    result = _evaluate(bars, _ending(10, 35))
    assert result.rejection_reason == NO_SIGNAL_BEFORE_CUTOFF and result.retryable is False


def test_a_first_breakout_before_a_later_window_start_is_lost_not_deferred():
    config = GapGoConfig(window_start=time(9, 50))
    bars = _day(session=(NO_BREAK, BREAKOUT, ABOVE))
    early = _evaluate(bars, _ending(9, 45), config=config)
    assert early.rejection_reason == NO_SIGNAL_YET
    later = _priced(bars, _ending(9, 50), config=config)
    assert later.setup is None and later.rejection_reason == NO_SIGNAL_YET


# --- the stop ---------------------------------------------------------------


def test_the_first_bar_low_binds_when_it_is_above_entry_minus_atr():
    # A noisier prior session (2.00 bar ranges) lifts ATR(14) to 2.0114, so
    # entry - ATR = 103.49 falls below the 104.00 first-bar low.
    bars = _day(first=("104", "105.2", "104", "104.8"), prior_half_range="1")
    result = _priced(bars, _ending(9, 45))
    setup = result.setup
    assert setup is not None and result.atr is not None
    assert result.atr.quantize(Decimal("0.0001")) == Decimal("2.0114")
    assert (setup.stop_price, setup.target_price) == (Decimal("104.00"), Decimal("108.50"))
    assert setup.levels["stop_distance"] == Decimal("1.50")


def test_a_stop_tighter_than_half_a_percent_is_a_retryable_skip():
    # A near-flat prior session leaves ATR(14) at 0.4501: the stop at
    # 105.04 is 0.46 (0.44%) below the entry.
    bars = _day(prior_half_range="0.025")
    result = _priced(bars, _ending(9, 45))
    assert result.setup is None and result.rejection_reason == STOP_TOO_TIGHT
    assert result.retryable is True and not is_data_rejection(STOP_TOO_TIGHT)
    assert result.atr is not None and result.atr.quantize(Decimal("0.0001")) == Decimal("0.4501")
    looser = _priced(bars, _ending(9, 45), config=GapGoConfig(min_stop_pct=Decimal("0.4")))
    assert looser.setup is not None and looser.setup.stop_price == Decimal("105.04")


def test_a_stop_wider_than_two_percent_is_a_retryable_skip():
    # ATR(14) of about 3 puts entry - ATR below the 103.00 first-bar low, so
    # the stop is 103.00: 2.50 (2.37%) below the entry.
    bars = _day(first=("104", "105.2", "103", "104.8"), prior_half_range="1.5")
    result = _priced(bars, _ending(9, 45))
    assert result.setup is None and result.rejection_reason == STOP_TOO_WIDE
    assert result.retryable is True and not is_data_rejection(STOP_TOO_WIDE)
    wider = _priced(bars, _ending(9, 45), config=GapGoConfig(max_stop_pct=Decimal("2.5")))
    assert wider.setup is not None
    assert (wider.setup.stop_price, wider.setup.target_price) == (Decimal("103.00"), Decimal("110.50"))


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        # The 0.82 stop is 0.7773% of the 105.50 entry.
        ({"min_stop_pct": Decimal("0.77")}, None),
        ({"min_stop_pct": Decimal("0.78")}, STOP_TOO_TIGHT),
        ({"max_stop_pct": Decimal("0.78")}, None),
        ({"min_stop_pct": Decimal("0.5"), "max_stop_pct": Decimal("0.77")}, STOP_TOO_WIDE),
    ],
)
def test_stop_bounds_apply_to_the_rounded_stop_distance(overrides, reason):
    result = _priced(_day(), _ending(9, 45), config=GapGoConfig(**overrides))
    assert result.rejection_reason == reason
    if reason is None:
        assert result.setup is not None and result.setup.levels["stop_distance"] == Decimal("0.82")


def test_target_is_the_reward_multiple_of_the_rounded_stop_distance():
    setup = _priced(
        _day(), _ending(9, 45), config=GapGoConfig(target_reward_risk=Decimal("2.5"))
    ).setup
    assert setup is not None
    assert setup.target_price == Decimal("105.50") + Decimal("0.82") * Decimal("2.5")


# --- quotes -------------------------------------------------------------------


def test_the_entry_is_the_higher_of_the_signal_close_and_the_ask():
    now = _ending(9, 45)
    below = _evaluate(_day(), now, quote=_quote(now, bid="105.39", ask="105.40")).setup
    assert below is not None and below.entry_limit == Decimal("105.50")
    above = _evaluate(_day(), now, quote=_quote(now, bid="105.69", ask="105.70")).setup
    assert above is not None and above.entry_limit == Decimal("105.70")


@pytest.mark.parametrize(
    ("quote_overrides", "reason"),
    [
        ({"observed_at_offset": -3}, "quote_stale"),
        ({"con_id": CON_ID + 1}, "quote_contract_mismatch"),
        ({"bid": "105.2"}, "quote_spread_too_wide"),
        # 105.75 is more than 20 bps above the 105.50 close.
        ({"bid": "105.74", "ask": "105.75"}, "breakout_chase_too_wide"),
    ],
)
def test_quote_problems_are_retryable_skips(quote_overrides, reason):
    now = _ending(9, 45)
    overrides = dict(quote_overrides)
    offset = overrides.pop("observed_at_offset", 0)
    quote = _quote(now + timedelta(seconds=offset), **overrides)
    result = _evaluate(_day(), now, quote=quote)
    assert result.setup is None and result.rejection_reason == reason
    assert result.retryable is True
    assert is_data_rejection(reason) is reason.startswith("quote_")
    strategy_result = GapGoStrategy().evaluate(
        candidate=_candidate(),
        bars=_until(_day(), now),
        quote=quote,
        session_date=SESSION,
        run_id="run-gap",
        strategy_id="strategy-gap",
        strategy_version=1,
        reference_price="100",
        average_dollar_volume=AVERAGE_DOLLAR_VOLUME,
        candidate_input_fingerprint=None,
        now=now,
    )
    assert (strategy_result.rejection_reason, strategy_result.retryable) == (reason, True)


# --- data quality -------------------------------------------------------------


def test_data_problems_are_retryable_data_rejections_before_the_cutoff():
    bars = _day()
    now = _ending(9, 45)
    missing = tuple(bar for bar in bars if bar.interval_start_utc != _et(SESSION, 9, 35))
    duplicated = (*bars, bars[-1])
    other = tuple(bar.model_copy(update={"con_id": CON_ID + 1}) for bar in bars)
    cases = {
        NO_BARS_SUPPLIED: _evaluate((), now),
        "bar_sequence_invalid": _evaluate(duplicated, now),
        "bar_contract_mismatch": _evaluate(other, now),
        "bar_session_incomplete": _evaluate(missing, now),
        # The feed lags a bar: the 09:45 breakout must not be priced at 09:50.
        "bar_session_stale": _evaluate(bars, _ending(9, 50)),
        # ATR(14) needs the prior session at 09:45.
        "insufficient_atr_history": _priced(_day(with_prior=False), now),
    }
    for reason, result in cases.items():
        assert result.setup is None and result.rejection_reason == reason, reason
        assert result.retryable is True and is_data_rejection(reason), reason
    at_cutoff = _evaluate(missing, _et(SESSION, 10, 30))
    assert at_cutoff.rejection_reason == "bar_session_incomplete" and at_cutoff.retryable is False


# --- assembling the bars from the two IBKR requests ---------------------------


def test_gap_go_bars_takes_history_from_the_rth_request_and_today_from_the_extended_one():
    day = _day()
    prior = [bar for bar in day if bar.interval_start_utc < _et(SESSION, 0, 0)]
    today = [bar for bar in day if bar.interval_start_utc >= _et(SESSION, 0, 0)]
    today_rth = [bar for bar in today if bar.rth]
    # The RTH request ("2 D") repeats today's regular-session bars; the
    # extended request ("1 D") also carries after-hours bars of other days.
    regular = (*prior, *today_rth)
    stray = _bar(_et(PRIOR, 16, 0), ("100", "100.2", "99.9", "100.1"), rth=False)
    extended = (stray, *today)

    merged = gap_go_bars(regular, extended, session_date=SESSION)

    assert merged == tuple(sorted(day, key=lambda bar: bar.interval_start_utc))
    assert len({bar.interval_start_utc for bar in merged}) == len(merged)
    now = _ending(9, 45)
    assert _priced(merged, now).setup == _priced(day, now).setup


# --- the shadow pipeline ------------------------------------------------------


def _strategy_record() -> StrategyRecord:
    spec = StrategySpec(
        name="Gap pipeline fixture",
        universe=FixedUniverse(symbols=("GAPR",)),
        conditions=(RuleCondition(feature="close", operator="gte", value="10"),),
        rationale="Gap-and-go pipeline fixture.",
    )
    created = _et(SESSION, 8, 0)
    return StrategyRecord(
        strategy_id="strategy-gap",
        owner_id="owner-a",
        version=1,
        content_hash=spec.fingerprint(),
        spec=spec,
        state=StrategyState.SHADOW,
        created_at=created,
        updated_at=created,
    )


class _Provider:
    def __init__(self) -> None:
        self.setups: list[RuleSetup] = []

    async def propose_trade(self, *, setup: RuleSetup) -> AIReview:
        self.setups.append(setup)
        return AIReview(
            setup_fingerprint=setup.fingerprint(),
            decision="PROPOSE",
            confidence="0.7",
            rationale="Gap pipeline review.",
            model_id="test-model",
            prompt_version=GAP_REVIEW_PROMPT_ID,
            response_hash=HASH,
            latency_ms=1,
            cost_usd="0",
        )


class _Recorder:
    def __init__(self) -> None:
        self.decisions: list[object] = []

    async def __call__(self, **kwargs: object) -> None:
        self.decisions.append(kwargs["decision"])


def _pipeline() -> tuple[ShadowPipeline, _Provider, _Recorder]:
    provider, recorder = _Provider(), _Recorder()
    pipeline = ShadowPipeline(
        proposal_provider=provider, decision_recorder=recorder, strategy=GapGoStrategy()
    )
    return pipeline, provider, recorder


async def _assess(
    pipeline: ShadowPipeline,
    bars: Sequence[CompletedBar],
    now: datetime,
    *,
    quote: QuoteSnapshot | None = None,
    candidate_id: str = "candidate-gap",
):
    return await pipeline.assess_one(
        run_id="run-gap",
        strategy=_strategy_record(),
        item=CandidateInput(
            candidate=_candidate(candidate_id),
            bars=_until(bars, now),
            quote=quote,
            reference_price="100",
            average_dollar_volume=AVERAGE_DOLLAR_VOLUME,
        ),
        portfolio=PortfolioSnapshot(observed_at=now, net_liquidation="100000"),
        policy=RiskPolicy(),
        session_date=SESSION,
        admitted_symbols=frozenset({"GAPR"}),
        now=now,
    )


@pytest.mark.anyio
async def test_pipeline_requests_a_quote_then_accepts_the_gap_setup():
    pipeline, provider, recorder = _pipeline()
    assert pipeline.strategy.strategy_kind == GAP_GO_STRATEGY_KIND
    bars = _day()
    waiting = await _assess(pipeline, bars, _ending(9, 40))
    assert waiting.decision is None and waiting.pending_reason == NO_SIGNAL_YET

    now = _ending(9, 45)
    unquoted = await _assess(pipeline, bars, now)
    assert unquoted.decision is None and unquoted.needs_quote is True

    accepted = await _assess(pipeline, bars, now, quote=_quote(now))
    decision = accepted.decision
    assert decision is not None and decision.outcome == DecisionOutcome.SHADOW_ACCEPTED
    assert len(provider.setups) == 1
    setup = provider.setups[0]
    assert setup.strategy_kind == "gap_go" and set(setup.levels) == LEVEL_NAMES
    assert decision.setup_fingerprint == setup.fingerprint()
    assert decision.risk is not None and decision.risk.approved is True
    intent = decision.risk.intent
    assert intent is not None
    assert (intent.entry_limit, intent.stop_price, intent.target_price) == (
        Decimal("105.50"),
        Decimal("104.68"),
        Decimal("107.14"),
    )
    assert recorder.decisions == [decision]


@pytest.mark.anyio
async def test_pipeline_rejects_a_small_gap_at_once_and_holds_retryables_to_the_cutoff():
    pipeline, provider, recorder = _pipeline()
    small = _day(first=("102.5", "105.2", "102.4", "104.8"))
    rejected = await _assess(pipeline, small, _ending(9, 35), candidate_id="small-gap")
    assert rejected.decision is not None
    assert rejected.decision.outcome == DecisionOutcome.RULE_REJECTED

    no_premarket = _day(premarket=[])
    pending = await _assess(pipeline, no_premarket, _ending(9, 45), candidate_id="no-pre")
    assert pending.decision is None and pending.pending_reason == NO_PREMARKET_DATA
    final = await _assess(pipeline, no_premarket, _et(SESSION, 10, 30), candidate_id="no-pre")
    assert final.decision is not None and final.decision.outcome == DecisionOutcome.RULE_REJECTED

    # A missed first breakout waits as NO_SIGNAL_YET, then is rejected.
    missed = _day(session=(NO_BREAK, BREAKOUT, ABOVE))
    stale = await _assess(pipeline, missed, _ending(9, 50), candidate_id="missed")
    assert stale.decision is None and stale.pending_reason == NO_SIGNAL_YET

    # A signal on the bar ending at the cutoff was never priced: data blocked.
    late = _late_breakout(10)
    blocked = await _assess(pipeline, late, _ending(10, 30), candidate_id="late")
    assert blocked.decision is not None
    assert blocked.decision.outcome == DecisionOutcome.DATA_BLOCKED

    gappy = tuple(bar for bar in _day() if bar.interval_start_utc != _et(SESSION, 9, 35))
    held = await _assess(pipeline, gappy, _ending(9, 45), candidate_id="gappy")
    assert held.decision is None and held.pending_reason == "bar_session_incomplete"
    expired = await _assess(pipeline, gappy, _et(SESSION, 10, 30), candidate_id="gappy")
    assert expired.decision is not None
    assert expired.decision.outcome == DecisionOutcome.DATA_BLOCKED

    assert provider.setups == []
    assert [decision.outcome for decision in recorder.decisions] == [
        DecisionOutcome.RULE_REJECTED,
        DecisionOutcome.RULE_REJECTED,
        DecisionOutcome.DATA_BLOCKED,
        DecisionOutcome.DATA_BLOCKED,
    ]


# --- identity -----------------------------------------------------------------


def test_config_is_frozen_and_every_parameter_is_in_the_fingerprint():
    config = GapGoConfig()
    with pytest.raises(FrozenInstanceError):
        config.min_gap_pct = Decimal("4")  # type: ignore[misc]
    assert GapGoConfig().fingerprint() == config.fingerprint()
    changed = {
        "window_start": time(9, 40),
        "entry_cutoff": time(10, 45),
        "premarket_start": time(5, 0),
        "min_gap_pct": Decimal("4"),
        "min_premarket_volume_pct": Decimal("3"),
        "atr_period": 10,
        "stop_atr_multiple": Decimal("1.5"),
        "min_stop_pct": Decimal("0.4"),
        "max_stop_pct": Decimal("2.5"),
        "target_reward_risk": Decimal("3"),
        "max_spread_bps": Decimal("10"),
        "max_chase_bps": Decimal("15"),
        "quote_max_age_seconds": Decimal("1"),
        "volume_multiplier": Decimal("1"),
        "minimum_price": Decimal("5"),
        "maximum_price": Decimal("500"),
        "minimum_average_dollar_volume": Decimal("10000000"),
    }
    fingerprints = {replace(config, **{name: value}).fingerprint() for name, value in changed.items()}
    assert len(fingerprints) == len(changed) and config.fingerprint() not in fingerprints


@pytest.mark.parametrize(
    "overrides",
    [
        {"window_start": time(9, 30)},
        {"entry_cutoff": time(9, 35)},
        {"entry_cutoff": time(13, 5)},
        {"premarket_start": time(3, 55)},
        {"premarket_start": time(9, 30)},
        {"min_gap_pct": Decimal("0")},
        {"min_premarket_volume_pct": Decimal("0")},
        {"stop_atr_multiple": Decimal("0")},
        {"min_stop_pct": Decimal("0")},
        {"min_stop_pct": Decimal("2"), "max_stop_pct": Decimal("2")},
        {"max_stop_pct": Decimal("100")},
        {"target_reward_risk": Decimal("1.9")},
        {"max_chase_bps": Decimal("-1")},
        {"volume_multiplier": Decimal("0")},
        {"minimum_price": Decimal("300")},
        {"atr_period": 0},
        {"min_gap_pct": Decimal("NaN")},
    ],
)
def test_invalid_config_is_rejected(overrides):
    with pytest.raises(ValueError):
        GapGoConfig(**overrides)


def test_catalogue_entry_names_the_rules_version():
    spec = gap_go_strategy_spec()
    assert spec.name == GAP_GO_RULES_VERSION == "GAP_GO_V1"
    assert spec.universe.kind == "scanner"
    assert spec.universe.scanner_policy_id == "screener-premarket-gappers"
    assert spec.ai_generated is False
    for term in ("3%", "2% of average daily shares", "09:35-10:30 ET", "ATR(14)", "0.5%", "2.0%", "2R"):
        assert term in spec.rationale
    assert spec.fingerprint() == gap_go_strategy_spec().fingerprint()


def test_review_prompt_describes_exactly_these_rules_and_is_pinned():
    assert GAP_REVIEW_PROMPT_ID == "gap-review-v1"
    for term in (
        'strategy_kind "gap_go"',
        "GAP_GO_V1",
        "at least 3% above the prior close",
        "at least 2% of the stock's average daily share volume",
        "between 09:35 and 10:30 ET",
        "the higher of the pre-market high and the first five-minute bar's high",
        "A later re-break never signals.",
        "at most 0.2% above that close",
        "the entry minus one ATR(14)",
        "below 0.5% or above 2.0% of the entry",
        "(2R)",
        "market_context",
        'When market_context is absent or {"available": false}, judge the setup on its own.',
        "PROPOSE",
        "ABSTAIN",
        "When the evidence is ambiguous, ABSTAIN.",
        "You cannot change any price, quantity, symbol, or order field.",
        "decision, confidence between 0 and 1, and a short rationale",
        *LEVEL_NAMES,
    ):
        assert term in GAP_REVIEW_V1, term
    # The MOMO-only risk-off entry gate is not claimed for this strategy.
    assert "already blocks new entries" not in GAP_REVIEW_V1
    # Recorded reviews will name gap-review-v1; its text must never change.
    assert (
        sha256(GAP_REVIEW_V1.encode("utf-8")).hexdigest()
        == "c575541c09875a4807ca5ac3e53d47848779769d39ec78898d9f7b51c5363555"
    )
