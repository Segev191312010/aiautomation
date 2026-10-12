from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading.contracts import CommissionPolicy, CompletedBar, QuoteSnapshot, RuleSetup
from trading.evidence.modelled import (
    ShadowFillModelError,
    model_entry_fill,
    model_session_close_fill,
    model_triggered_exit_fill,
    net_pnl,
    round_down_to_tick,
    round_up_to_tick,
    triggered_exit_reason,
)


NOW = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)


def _policy() -> CommissionPolicy:
    return CommissionPolicy(
        id="owner-ibkr-commission",
        version=1,
        effective_from=date(2026, 8, 31),
        commission_per_share_usd="0.005",
        minimum_commission_usd="1",
        maximum_commission_pct_of_trade_value="1",
    )


def _setup(*, entry_limit: str = "100.07") -> RuleSetup:
    return RuleSetup(
        run_id="run-1",
        candidate_id="candidate-1",
        strategy_id="ORB_V1",
        strategy_version=1,
        symbol="AAPL",
        opening_range_high="100",
        opening_range_low="98",
        atr="1",
        entry_limit=entry_limit,
        stop_price="99.06",
        target_price="102.06",
        bar_fingerprints=("a" * 64,),
        quote_fingerprint="b" * 64,
        detected_at=NOW,
    )


def _quote(*, bid: str = "100", ask: str = "100.01", observed_at: datetime = NOW) -> QuoteSnapshot:
    return QuoteSnapshot(
        con_id=123,
        bid=bid,
        ask=ask,
        observed_at=observed_at,
        min_tick="0.01",
    )


def _bar(*, low: str, high: str) -> CompletedBar:
    return CompletedBar(
        con_id=123,
        interval_start_utc=NOW,
        interval_end_utc=NOW + timedelta(minutes=5),
        open="100",
        high=high,
        low=low,
        close="100",
        volume="1000",
        rth=True,
        source="ibkr",
        data_mode="live",
    )


def test_tick_rounding_is_adverse_and_deterministic() -> None:
    assert round_up_to_tick(Decimal("100.001"), Decimal("0.01")) == Decimal("100.01")
    assert round_down_to_tick(Decimal("100.009"), Decimal("0.01")) == Decimal("100.00")
    with pytest.raises(ShadowFillModelError):
        round_down_to_tick(Decimal("100"), Decimal("0"))


def test_entry_uses_ask_plus_five_bps_and_respects_rule_limit() -> None:
    result = model_entry_fill(
        setup=_setup(),
        quote=_quote(),
        quantity=100,
        commission_policy=_policy(),
        filled_at=NOW,
    )
    assert result.blocked_reason is None
    assert result.fill is not None
    assert result.fill.kind == "MODELLED"
    assert result.fill.modelled_price == Decimal("100.07")
    assert result.fill.commission_usd == Decimal("1")

    blocked = model_entry_fill(
        setup=_setup(entry_limit="100.06"),
        quote=_quote(),
        quantity=100,
        commission_policy=_policy(),
        filled_at=NOW,
    )
    assert blocked.fill is None
    assert blocked.blocked_reason == "modelled_entry_exceeds_limit"


def test_stale_quote_cannot_produce_a_modelled_fill() -> None:
    with pytest.raises(ValueError, match="stale"):
        model_entry_fill(
            setup=_setup(),
            quote=_quote(observed_at=NOW - timedelta(seconds=3)),
            quantity=100,
            commission_policy=_policy(),
            filled_at=NOW,
        )


def test_both_exit_levels_in_one_bar_choose_stop_and_use_bid_minus_five_bps() -> None:
    setup = _setup()
    bar = _bar(low="98", high="103")
    assert triggered_exit_reason(setup=setup, bar=bar) == "stop"
    exit_fill = model_triggered_exit_fill(
        setup=setup,
        bar=bar,
        quote=_quote(bid="99", ask="99.01", observed_at=bar.interval_end_utc),
        quantity=100,
        commission_policy=_policy(),
        filled_at=bar.interval_end_utc,
    )
    assert exit_fill is not None
    assert exit_fill.reason == "stop"
    assert exit_fill.modelled_price == Decimal("98.95")


def test_untriggered_bar_returns_no_exit() -> None:
    assert (
        model_triggered_exit_fill(
            setup=_setup(),
            bar=_bar(low="99.50", high="101"),
            quote=_quote(),
            quantity=100,
            commission_policy=_policy(),
            filled_at=NOW,
        )
        is None
    )


def test_session_close_requires_fresh_bid_and_net_pnl_matches_fill_identity() -> None:
    setup = _setup()
    entry = model_entry_fill(
        setup=setup,
        quote=_quote(),
        quantity=100,
        commission_policy=_policy(),
        filled_at=NOW,
    ).fill
    assert entry is not None
    close_time = NOW + timedelta(hours=6)
    exit_fill = model_session_close_fill(
        setup=setup,
        quote=_quote(bid="101", ask="101.01", observed_at=close_time),
        quantity=100,
        commission_policy=_policy(),
        closed_at=close_time,
    )
    assert exit_fill.reason == "session_close"
    assert exit_fill.modelled_price == Decimal("100.94")
    assert net_pnl(entry, exit_fill) == Decimal("85.00")

    with pytest.raises(ValueError, match="stale"):
        model_session_close_fill(
            setup=setup,
            quote=_quote(observed_at=close_time - timedelta(seconds=3)),
            quantity=100,
            commission_policy=_policy(),
            closed_at=close_time,
        )
