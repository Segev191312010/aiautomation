"""MOMO_HOD_V2: new high of day on volume, priced only on the latest bar.

The V2 stop rules (stop width, VWAP extension) and the real 2026-10-12
replays live in test_momo_v2.py.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from trading.contracts import Candidate, CompletedBar, QuoteSnapshot
from trading.intelligence.prompts import (
    MOMO_REVIEW_V1,
    MOMO_REVIEW_V2,
    ORB_REVIEW_V1,
    review_prompt_for,
)
from trading.rules import (
    MOMO_STRATEGY_KIND,
    NO_SIGNAL_BEFORE_CUTOFF,
    NO_SIGNAL_YET,
    QUOTE_REQUIRED,
    MomoConfig,
    MomoResult,
    MomoStrategy,
    StrategyEvaluator,
    evaluate_momo,
    is_data_rejection,
    momo_entry_window,
)

NEW_YORK = ZoneInfo("America/New_York")
CON_ID = 4242
SESSION = date(2026, 1, 5)
PRIOR = date(2026, 1, 2)
BAR = timedelta(minutes=5)
# The 10:30-10:35 ET bar: twelve earlier session bars.
SIGNAL_BARS = 13
# Wilder ATR(14): every true range is 1 except the signal bar's 1.1. The V2
# stop (1.5 ATR = 1.51) then stays inside the 1.5%-of-entry cap (1.515 at 101).
SIGNAL_ATR = (Decimal("13") + Decimal("1.1")) / Decimal("14")


def _et(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=NEW_YORK).astimezone(
        timezone.utc
    )


def _bar(
    start: datetime,
    *,
    open_price: str = "100",
    high: str = "100.5",
    low: str = "99.5",
    close: str = "100",
    volume: str = "1000",
    rth: bool = True,
) -> CompletedBar:
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


def _prior_session(day: date = PRIOR) -> list[CompletedBar]:
    """A full quiet session whose high (101) leaves every true range at 1."""
    start = _et(day, 9, 30)
    return [
        _bar(start + BAR * index, open_price="100.5", high="101", low="100", close="100.5")
        if index == 40
        else _bar(start + BAR * index)
        for index in range(78)
    ]


def _today(
    count: int, *, day: date = SESSION, signal: bool = True, **last: str
) -> list[CompletedBar]:
    """``count`` contiguous session bars from 09:30; the last one breaks out."""
    start = _et(day, 9, 30)
    bars = [_bar(start + BAR * index) for index in range(count - 1)]
    shape = (
        {"open_price": "100.2", "high": "101.1", "low": "100.1", "close": "101", "volume": "2000"}
        if signal
        else {}
    )
    shape.update(last)
    bars.append(_bar(start + BAR * (count - 1), **shape))
    return bars


def _after(bars: list[CompletedBar], seconds: int = 5) -> datetime:
    return bars[-1].interval_end_utc + timedelta(seconds=seconds)


def _candidate(*, status: str = "admitted") -> Candidate:
    return Candidate(
        id="candidate-momo",
        scanner_run_id="scan-momo",
        con_id=CON_ID,
        symbol="MOMO",
        primary_exchange="NASDAQ",
        rank=1,
        admission_status=status,
        rejection_reason=None if status == "admitted" else "not eligible",
    )


def _quote(
    now: datetime,
    *,
    bid: str = "100.99",
    ask: str = "101.00",
    min_tick: str = "0.01",
    age_seconds: int = 0,
    con_id: int = CON_ID,
) -> QuoteSnapshot:
    return QuoteSnapshot(
        con_id=con_id,
        bid=bid,
        ask=ask,
        observed_at=now - timedelta(seconds=age_seconds),
        min_tick=min_tick,
    )


def _evaluate(
    bars: list[CompletedBar],
    *,
    quote: QuoteSnapshot | None = None,
    now: datetime | None = None,
    **overrides: object,
) -> MomoResult:
    params: dict[str, object] = {
        "candidate": _candidate(),
        "bars": bars,
        "quote": quote,
        "session_date": SESSION,
        "run_id": "run-momo",
        "strategy_id": "strategy-momo",
        "strategy_version": 1,
        "reference_price": "100",
        "average_dollar_volume": "25000000",
        "now": now or _after(bars),
    }
    params.update(overrides)
    return evaluate_momo(**params)


def _signal_bars() -> list[CompletedBar]:
    return [*_prior_session(), *_today(SIGNAL_BARS)]


def test_new_high_above_vwap_on_volume_asks_for_a_quote_then_builds_the_setup():
    bars = _signal_bars()
    now = _after(bars)

    pending = _evaluate(bars)
    assert pending.setup is None
    assert pending.rejection_reason == QUOTE_REQUIRED and pending.retryable is True
    assert pending.evaluated_bar == bars[-1]
    assert pending.session_high == Decimal("100.5")
    assert pending.vwap == Decimal("4204400") / Decimal("42000")
    assert pending.avg_bar_volume == Decimal("1000")
    # 1.4M shares by 10:35 against 250k shares/day paced over 65 of 390 minutes.
    assert pending.rvol == Decimal("33.6")

    result = _evaluate(bars, quote=_quote(now))
    setup = result.setup
    assert setup is not None and result.rejection_reason is None
    assert setup.strategy_kind == MOMO_STRATEGY_KIND
    assert setup.opening_range_high is None and setup.opening_range_low is None
    assert setup.atr == SIGNAL_ATR
    assert setup.entry_limit == Decimal("101.00")
    # V2: 1.5 ATR (1.5107) beats 0.6% of entry (0.606) and the signal low
    # (0.91), and rounds down to 99.48; the target is 2R on that 1.52 stop.
    assert setup.stop_price == Decimal("99.48")
    assert setup.target_price == Decimal("104.04")
    assert setup.levels == {
        "prior_day_high": Decimal("101"),
        "vwap": Decimal("100.1048"),
        "signal_bar_volume": Decimal("200000"),
        "avg_bar_volume": Decimal("100000"),
        "rvol": Decimal("33.60"),
        "stop_distance": Decimal("1.52"),
        "vwap_extension_atr": Decimal("0.89"),
    }
    assert setup.detected_at == bars[-1].interval_end_utc
    provenance = setup.causal_provenance
    assert provenance is not None
    assert provenance.opening_bar_fingerprints is None
    assert provenance.breakout_bar_fingerprint == bars[-1].fingerprint()
    assert provenance.quote_fingerprint == setup.quote_fingerprint == _quote(now).fingerprint()
    assert len(provenance.atr_bar_fingerprints) == 78 + SIGNAL_BARS
    assert set(setup.bar_fingerprints) == {bar.fingerprint() for bar in bars}
    # Same evidence, same immutable setup.
    assert _evaluate(bars, quote=_quote(now)).setup == setup


def test_close_at_or_below_vwap_does_not_signal():
    # The signal bar's own heavy volume near its high drags VWAP above its close.
    bars = [
        *_prior_session(),
        *_today(
            SIGNAL_BARS, open_price="101", high="104", low="100.6", close="101", volume="50000"
        ),
    ]
    result = _evaluate(bars)
    assert result.rejection_reason == NO_SIGNAL_YET and result.retryable is True
    assert result.vwap is not None and result.vwap > bars[-1].close
    assert bars[-1].close > (result.session_high or Decimal("0"))


def test_close_equal_to_the_prior_high_of_day_does_not_signal():
    bars = [
        *_prior_session(),
        *_today(SIGNAL_BARS, open_price="100.2", high="100.9", low="100", close="100.5"),
    ]
    result = _evaluate(bars)
    assert result.rejection_reason == NO_SIGNAL_YET
    assert result.session_high == Decimal("100.5")


def test_bar_volume_must_reach_one_and_a_half_times_the_session_average():
    thin = _evaluate([*_prior_session(), *_today(SIGNAL_BARS, volume="1499")])
    assert thin.rejection_reason == NO_SIGNAL_YET

    enough = _evaluate([*_prior_session(), *_today(SIGNAL_BARS, volume="1500")])
    assert enough.rejection_reason == QUOTE_REQUIRED


def test_relative_volume_must_reach_two():
    bars = _signal_bars()
    exactly_two = _evaluate(bars, average_dollar_volume="420000000")
    assert exactly_two.rvol == Decimal("2")
    assert exactly_two.rejection_reason == QUOTE_REQUIRED

    below = _evaluate(bars, average_dollar_volume="420000001")
    assert below.rvol is not None and below.rvol < Decimal("2")
    assert below.rejection_reason == NO_SIGNAL_YET


def test_round_lot_bar_volume_is_converted_to_shares_for_rvol_and_levels():
    bars = _signal_bars()
    as_shares = _evaluate(bars, config=MomoConfig(volume_multiplier=Decimal("1")))
    assert as_shares.rvol == Decimal("0.336")
    assert as_shares.rejection_reason == NO_SIGNAL_YET

    as_lots = _evaluate(bars, quote=_quote(_after(bars)))
    assert as_lots.rvol == Decimal("33.6")
    assert as_lots.setup is not None
    assert as_lots.setup.levels["signal_bar_volume"] == Decimal("2000") * Decimal("100")
    assert as_lots.setup.levels["avg_bar_volume"] == Decimal("1000") * Decimal("100")


def test_signal_bar_must_end_inside_the_window_after_enough_session_bars():
    # 09:55-10:00 ends inside the window but has only five earlier bars.
    five_prior = _evaluate(_today(6))
    assert five_prior.rejection_reason == NO_SIGNAL_YET
    six_prior = _evaluate(_today(7))
    assert six_prior.rejection_reason == QUOTE_REQUIRED

    relaxed = MomoConfig(min_prior_bars=3)
    before_window = _evaluate(_today(5), config=relaxed)  # ends 09:55
    assert before_window.rejection_reason == NO_SIGNAL_YET
    window_open = _evaluate(_today(6), config=relaxed)  # ends 10:00, inclusive
    assert window_open.rejection_reason == QUOTE_REQUIRED


def test_only_the_latest_completed_bar_may_signal():
    signal = _signal_bars()
    followed = [
        *signal,
        _bar(
            signal[-1].interval_end_utc,
            open_price="100.9",
            high="100.95",
            low="100.3",
            close="100.6",
        ),
    ]
    stale_breakout = _evaluate(followed, quote=_quote(_after(followed)))
    assert stale_breakout.setup is None
    assert stale_breakout.rejection_reason == NO_SIGNAL_YET
    assert stale_breakout.evaluated_bar == followed[-1]

    # Bars that stop one interval short of now cannot stand in for the newest bar.
    lagging = _evaluate(signal, quote=_quote(_after(signal, 305)), now=_after(signal, 305))
    assert lagging.setup is None
    assert lagging.rejection_reason == "bar_session_stale"
    assert lagging.retryable is True and is_data_rejection("bar_session_stale")


def test_cutoff_is_terminal_and_never_prices_a_signal():
    quiet = _today(66, signal=False)  # last bar 14:55-15:00 ET
    at_cutoff = _evaluate(quiet, now=_et(SESSION, 15, 0))
    assert at_cutoff.rejection_reason == NO_SIGNAL_BEFORE_CUTOFF
    assert at_cutoff.retryable is False

    late_signal = [*_prior_session(), *_today(66)]
    unpriced = _evaluate(late_signal)
    assert unpriced.rejection_reason == QUOTE_REQUIRED and unpriced.retryable is False
    quoted = _evaluate(late_signal, quote=_quote(_after(late_signal)))
    assert quoted.setup is None
    assert quoted.rejection_reason == "signal_after_cutoff" and quoted.retryable is False

    after_window = _evaluate([*_prior_session(), *_today(67)])  # 15:00-15:05 ET
    assert after_window.rejection_reason == NO_SIGNAL_BEFORE_CUTOFF


def test_quote_chasing_the_signal_close_is_retryable():
    bars = _signal_bars()
    now = _after(bars)
    chased = _evaluate(bars, quote=_quote(now, bid="101.20", ask="101.25"))
    assert chased.setup is None
    assert chased.rejection_reason == "breakout_chase_too_wide"
    assert chased.retryable is True and not is_data_rejection("breakout_chase_too_wide")

    # 101.20 sits inside 20 bps of the 101 close (101.202).
    inside = _evaluate(bars, quote=_quote(now, bid="101.15", ask="101.20"))
    assert inside.setup is not None and inside.setup.entry_limit == Decimal("101.20")


@pytest.mark.parametrize(
    ("quote_kwargs", "reason"),
    [
        ({"bid": "100.80", "ask": "101.00"}, "quote_spread_too_wide"),
        ({"age_seconds": 3}, "quote_stale"),
        ({"con_id": 999}, "quote_contract_mismatch"),
    ],
)
def test_bad_quotes_are_retryable_data_rejections(quote_kwargs, reason):
    bars = _signal_bars()
    result = _evaluate(bars, quote=_quote(_after(bars), **quote_kwargs))
    assert result.setup is None
    assert result.rejection_reason == reason
    assert result.retryable is True and is_data_rejection(reason)


def test_a_two_second_old_quote_is_still_fresh():
    bars = _signal_bars()
    assert _evaluate(bars, quote=_quote(_after(bars), age_seconds=2)).setup is not None


def test_atr_stop_and_target_round_outward_on_the_quote_tick():
    bars = _signal_bars()
    quote = _quote(_after(bars), bid="100.95", ask="101.01", min_tick="0.05")
    result = _evaluate(bars, quote=quote)
    setup = result.setup
    assert setup is not None and result.atr == SIGNAL_ATR
    # Entry rounds up, the stop rounds down (101.05 - 1.5107 = 99.539), and
    # the 2R target rounds up.
    assert setup.entry_limit == Decimal("101.05")
    assert setup.stop_price == Decimal("99.50")
    assert setup.target_price == Decimal("104.15")
    assert setup.target_price - setup.entry_limit >= Decimal("2") * (
        setup.entry_limit - setup.stop_price
    )


def test_missing_history_is_a_retryable_data_rejection():
    morning = _today(SIGNAL_BARS)
    short = _evaluate(morning, quote=_quote(_after(morning)))
    assert short.rejection_reason == "insufficient_atr_history"
    assert short.retryable is True

    midday = _today(26)  # 11:35-11:40 ET: ATR(14) fits inside today's bars
    no_prior = _evaluate(midday, quote=_quote(_after(midday)))
    assert no_prior.rejection_reason == "bar_prior_session_missing"
    assert no_prior.retryable is True


def test_session_gaps_block_and_premarket_bars_are_ignored():
    bars = _signal_bars()
    gapped = [bar for index, bar in enumerate(bars) if index != 78 + 5]
    result = _evaluate(gapped)
    assert result.rejection_reason == "bar_session_incomplete"
    assert result.retryable is True

    premarket = _bar(
        _et(SESSION, 9, 0), open_price="104", high="105", low="104", close="104.5", rth=False
    )
    now = _after(bars)
    with_premarket = _evaluate([premarket, *bars], quote=_quote(now))
    assert with_premarket.setup == _evaluate(bars, quote=_quote(now)).setup


def test_candidate_rejections_are_terminal():
    price = _evaluate(_signal_bars(), reference_price="9.99")
    assert price.rejection_reason == "candidate_price_out_of_range"
    assert price.retryable is False

    rejected = _evaluate(_signal_bars(), candidate=_candidate(status="rejected"))
    assert rejected.rejection_reason == "candidate_not_admitted"
    assert rejected.retryable is False


def test_entry_window_pulls_the_cutoff_in_before_an_early_close():
    half_day = date(2026, 11, 27)
    assert momo_entry_window(SESSION) == (_et(SESSION, 10, 0), _et(SESSION, 15, 0))
    assert momo_entry_window(half_day, session_close=_et(half_day, 13, 0)) == (
        _et(half_day, 10, 0),
        _et(half_day, 12, 0),
    )
    # A close before the window opens leaves an empty window, never an inverted one.
    assert momo_entry_window(half_day, session_close=_et(half_day, 10, 30)) == (
        _et(half_day, 10, 0),
        _et(half_day, 10, 0),
    )

    strategy = MomoStrategy()
    assert isinstance(strategy, StrategyEvaluator)
    assert strategy.strategy_kind == MOMO_STRATEGY_KIND
    assert strategy.entry_window(half_day) == (_et(half_day, 10, 0), _et(half_day, 12, 0))
    assert strategy.entry_window(SESSION) == (_et(SESSION, 10, 0), _et(SESSION, 15, 0))
    assert MomoStrategy(session_close=lambda _: None).entry_window(half_day)[1] == _et(
        half_day, 15, 0
    )

    # The XNYS half day also closes evaluation: a noon signal is never priced.
    bars = [*_prior_session(date(2026, 11, 25)), *_today(30, day=half_day)]
    now = _after(bars)
    result = strategy.evaluate(
        candidate=_candidate(),
        bars=bars,
        quote=_quote(now),
        session_date=half_day,
        run_id="run-momo",
        strategy_id="strategy-momo",
        strategy_version=1,
        reference_price="100",
        average_dollar_volume="25000000",
        candidate_input_fingerprint=None,
        now=now,
    )
    assert result.setup is None
    assert result.rejection_reason == "signal_after_cutoff" and result.retryable is False


def test_review_prompt_explains_every_level_and_is_bound_to_its_strategy_kind():
    bars = _signal_bars()
    setup = _evaluate(bars, quote=_quote(_after(bars))).setup
    assert setup is not None
    assert MOMO_STRATEGY_KIND in MOMO_REVIEW_V2
    assert all(name in MOMO_REVIEW_V2 for name in setup.levels)
    assert review_prompt_for("momo-review-v2", strategy_kind=setup.strategy_kind) == MOMO_REVIEW_V2
    # V1 stays registered, unchanged, so recorded reviews remain attributable.
    assert review_prompt_for("momo-review-v1", strategy_kind=setup.strategy_kind) == MOMO_REVIEW_V1
    assert review_prompt_for("orb-review-v1", strategy_kind="orb") == ORB_REVIEW_V1
    # An ORB prompt reviewing a momentum setup would judge an opening range it lacks.
    with pytest.raises(ValueError):
        review_prompt_for("orb-review-v1", strategy_kind=setup.strategy_kind)
    with pytest.raises(ValueError):
        review_prompt_for("momo-review-v0", strategy_kind=MOMO_STRATEGY_KIND)


def test_config_fingerprint_identifies_the_exact_rule_parameters():
    assert MomoConfig().fingerprint() == MomoConfig().fingerprint()
    assert len(MomoConfig().fingerprint()) == 64
    assert MomoConfig(min_rvol=Decimal("2.5")).fingerprint() != MomoConfig().fingerprint()
    assert MomoStrategy().config == MomoConfig()


@pytest.mark.parametrize(
    "overrides",
    [
        {"window_start": time(15, 0)},
        {"entry_cutoff": time(16, 5)},
        {"window_start": time(9, 30)},
        {"volume_multiplier": Decimal("0")},
        {"target_reward_risk": Decimal("1.5")},
        {"min_prior_bars": 0},
        {"close_buffer": timedelta(minutes=-1)},
    ],
)
def test_invalid_momo_config_is_rejected(overrides):
    with pytest.raises(ValueError):
        MomoConfig(**overrides)
