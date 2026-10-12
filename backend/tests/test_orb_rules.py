from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading.contracts import Candidate, CompletedBar, QuoteSnapshot
from trading.rules.orb import (
    ORBError,
    build_orb_setup,
    evaluate_orb,
    qualify_candidate,
    wilder_atr,
)

CON_ID = 12345
SESSION_DATE = date(2026, 1, 5)


def _bar(
    start: datetime,
    *,
    open_price: str = "99",
    high: str = "100",
    low: str = "98",
    close: str = "99",
    con_id: int = CON_ID,
    rth: bool = True,
) -> CompletedBar:
    return CompletedBar(
        con_id=con_id,
        interval_start_utc=start,
        interval_end_utc=start + timedelta(minutes=5),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume="10000",
        rth=rth,
        source="ibkr",
        data_mode="historical",
    )


def _candidate(*, status: str = "admitted") -> Candidate:
    return Candidate(
        scanner_run_id="scan-1",
        con_id=CON_ID,
        symbol="AAPL",
        currency="USD",
        security_type="STK",
        stock_type="COMMON",
        primary_exchange="NASDAQ",
        rank=1,
        admission_status=status,
        rejection_reason=None if status == "admitted" else "not eligible",
    )


def _quote(
    *,
    observed_at: datetime = datetime(2026, 1, 5, 14, 55, tzinfo=timezone.utc),
    bid: str = "101.00",
    ask: str = "101.01",
) -> QuoteSnapshot:
    return QuoteSnapshot(
        con_id=CON_ID,
        bid=bid,
        ask=ask,
        observed_at=observed_at,
        min_tick="0.01",
    )


def _session_bars() -> list[CompletedBar]:
    prior_start = datetime(2026, 1, 2, 13, 20, tzinfo=timezone.utc)
    prior = [_bar(prior_start + timedelta(minutes=5 * index)) for index in range(14)]
    opening_start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    opening = [
        _bar(opening_start, high="100", low="98"),
        _bar(opening_start + timedelta(minutes=5), high="100.5", low="98.5"),
        _bar(opening_start + timedelta(minutes=10), high="100.25", low="98.25"),
    ]
    pre_breakout = _bar(opening_start + timedelta(minutes=15), high="100.2", close="99.5")
    breakout = _bar(
        opening_start + timedelta(minutes=20),
        open_price="100.2",
        high="101.2",
        low="100.1",
        close="101",
    )
    return [*prior, *opening, pre_breakout, breakout]


def _evaluate(bars: list[CompletedBar], **kwargs: object):
    params: dict[str, object] = {
        "candidate": _candidate(),
        "bars": bars,
        "quote": _quote(),
        "session_date": SESSION_DATE,
        "run_id": "run-1",
        "strategy_id": "strategy-1",
        "strategy_version": 1,
        "reference_price": "101",
        "average_dollar_volume": "25000000",
        "now": datetime(2026, 1, 5, 14, 55, 1, tzinfo=timezone.utc),
    }
    params.update(kwargs)
    return evaluate_orb(**params)


def test_orb_uses_three_new_york_opening_bars_and_first_completed_breakout():
    result = _evaluate(_session_bars())

    assert result.accepted
    assert result.setup is not None
    assert result.opening_range_high == Decimal("100.5")
    assert result.opening_range_low == Decimal("98")
    assert result.breakout_bar is not None
    assert result.breakout_bar.interval_end_utc == datetime(
        2026, 1, 5, 14, 55, tzinfo=timezone.utc
    )
    assert result.setup.entry_limit == Decimal("101.01")
    assert result.setup.stop_price == Decimal("99.01")
    assert result.setup.target_price == Decimal("105.01")
    assert result.setup.target_price - result.setup.entry_limit == Decimal("2") * (
        result.setup.entry_limit - result.setup.stop_price
    )


def test_orb_rejects_incomplete_opening_range_and_does_not_use_later_bars():
    bars = _session_bars()
    del bars[15]
    result = _evaluate(bars)
    assert result.rejection_reason == "opening_range_requires_three_bars"

    bars = _session_bars()
    bars[-1] = _bar(
        datetime(2026, 1, 5, 14, 50, tzinfo=timezone.utc),
        high="100.2",
        close="99.5",
    )
    bars.append(
        _bar(
            datetime(2026, 1, 5, 14, 55, tzinfo=timezone.utc),
            open_price="100.2",
            high="101.2",
            low="100.1",
            close="101",
        )
    )
    pending = _evaluate(bars)
    assert pending.setup is None
    assert pending.rejection_reason == "NO_SIGNAL_YET"

    completed = _evaluate(
        bars,
        quote=_quote(observed_at=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)),
        now=datetime(2026, 1, 5, 15, 0, 1, tzinfo=timezone.utc),
    )
    assert completed.setup is not None
    assert completed.breakout_bar is not None
    assert completed.breakout_bar.interval_start_utc == datetime(
        2026, 1, 5, 14, 55, tzinfo=timezone.utc
    )


def test_orb_enforces_candidate_price_and_dollar_volume_constraints():
    candidate = _candidate()
    assert (
        qualify_candidate(
            candidate,
            reference_price="9.99",
            average_dollar_volume="25000000",
        )
        == "candidate_price_out_of_range"
    )
    assert (
        qualify_candidate(
            candidate,
            reference_price="100",
            average_dollar_volume="19999999",
        )
        == "candidate_dollar_volume_too_low"
    )
    assert qualify_candidate(
        candidate,
        reference_price="100",
        average_dollar_volume="20000000",
    ) is None
    assert (
        qualify_candidate(
            _candidate(status="rejected"),
            reference_price="100",
            average_dollar_volume="25000000",
        )
        == "candidate_not_admitted"
    )


def test_orb_rejects_stale_wide_and_overchased_quotes():
    stale = _evaluate(
        _session_bars(),
        quote=_quote(observed_at=datetime(2026, 1, 5, 14, 50, tzinfo=timezone.utc)),
    )
    assert stale.rejection_reason == "quote_stale"

    wide = _evaluate(_session_bars(), quote=_quote(bid="100", ask="100.2"))
    assert wide.rejection_reason == "quote_spread_too_wide"

    chased = _evaluate(_session_bars(), quote=_quote(bid="101.99", ask="102"))
    assert chased.rejection_reason == "breakout_chase_too_wide"


def test_wilder_atr_is_deterministic_and_requires_rth_history():
    start = datetime(2026, 1, 2, 13, 20, tzinfo=timezone.utc)
    bars = [
        _bar(
            start + timedelta(minutes=5 * index),
            open_price="99" if index == 0 else "100",
            high="101" if index == 0 else ("102" if index == 14 else "100.5"),
            low="99" if index == 0 else ("99" if index == 14 else "99.5"),
            close="100" if index == 0 else "100",
        )
        for index in range(15)
    ]
    expected = ((Decimal("15") / Decimal("14")) * Decimal("13") + Decimal("3")) / Decimal("14")
    assert wilder_atr(bars, period=14) == expected
    with pytest.raises(ORBError, match="RTH"):
        wilder_atr([*bars[:-1], bars[-1].model_copy(update={"rth": False})])


def test_orb_build_wrapper_reports_pending_signal_before_cutoff():
    bars = _session_bars()
    bars[-1] = _bar(
        datetime(2026, 1, 5, 14, 50, tzinfo=timezone.utc),
        high="100.4",
        close="100.4",
    )
    with pytest.raises(ORBError, match="NO_SIGNAL_YET"):
        build_orb_setup(
            candidate=_candidate(),
            bars=bars,
            quote=_quote(),
            session_date=SESSION_DATE,
            run_id="run-1",
            strategy_id="strategy-1",
            strategy_version=1,
            reference_price="101",
            average_dollar_volume="25000000",
            now=datetime(2026, 1, 5, 14, 55, 1, tzinfo=timezone.utc),
        )


def test_orb_closes_pending_signal_at_cutoff():
    bars = _session_bars()
    bars[-1] = _bar(
        datetime(2026, 1, 5, 14, 50, tzinfo=timezone.utc),
        high="100.4",
        close="100.4",
    )
    result = _evaluate(
        bars,
        quote=_quote(observed_at=datetime(2026, 1, 5, 17, 0, tzinfo=timezone.utc)),
        now=datetime(2026, 1, 5, 17, 0, tzinfo=timezone.utc),
    )
    assert result.rejection_reason == "no_breakout_before_cutoff"


def test_orb_ignores_bars_that_have_not_completed_as_of_evaluation():
    result = _evaluate(
        _session_bars(),
        quote=_quote(observed_at=datetime(2026, 1, 5, 14, 54, tzinfo=timezone.utc)),
        now=datetime(2026, 1, 5, 14, 54, tzinfo=timezone.utc),
    )
    assert result.rejection_reason == "NO_SIGNAL_YET"
    assert result.setup is None
