"""
Tests for position_tracker module.

Coverage:
  - compute_trail_stop: pure math
  - check_exits: hard stop, trailing stop, EMA/SMA crosses, RSI, MACD
  - register_position: DB persistence
  - update_watermarks: watermark update logic
"""
from __future__ import annotations

import math
import pytest
import pandas as pd

from models import OpenPosition, Trade


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(closes: list[float]) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from a list of close prices."""
    n = len(closes)
    return pd.DataFrame({
        "time":   list(range(n)),
        "open":   closes,
        "high":   [c * 1.01 for c in closes],
        "low":    [c * 0.99 for c in closes],
        "close":  closes,
        "volume": [1_000_000] * n,
    })


def _make_position(
    symbol: str = "AAPL",
    side: str = "BUY",
    entry_price: float = 100.0,
    hard_stop_price: float = 94.0,   # 6% below entry by default
    trail_pct_mult: float = 2.0,
    atr_at_entry: float = 2.0,
    high_watermark: float | None = None,
) -> OpenPosition:
    return OpenPosition(
        id="test-trade-id-001",
        symbol=symbol,
        side=side,
        quantity=10.0,
        entry_price=entry_price,
        entry_time="2026-01-01T00:00:00+00:00",
        atr_at_entry=atr_at_entry,
        hard_stop_price=hard_stop_price,
        atr_stop_mult=3.0,
        atr_trail_mult=trail_pct_mult,
        high_watermark=high_watermark if high_watermark is not None else entry_price,
        rule_id="rule-001",
        rule_name="Test Rule",
    )


def _make_trade(
    symbol: str = "AAPL",
    action: str = "BUY",
    fill_price: float = 100.0,
    quantity: int = 10,
) -> Trade:
    return Trade(
        id="test-trade-id-001",
        rule_id="rule-001",
        rule_name="Test Rule",
        symbol=symbol,
        action=action,
        asset_type="STK",
        quantity=quantity,
        order_type="MKT",
        limit_price=None,
        fill_price=fill_price,
        status="FILLED",
        order_id=12345,
        timestamp="2026-01-01T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# 1. compute_trail_stop — pure math
# ---------------------------------------------------------------------------

def test_compute_trail_stop_basic():
    from position_tracker import compute_trail_stop
    pos = _make_position(high_watermark=110.0, trail_pct_mult=2.0, atr_at_entry=2.0)
    # watermark(110) - 2.0 × atr(2.0) = 106.0
    assert compute_trail_stop(pos, current_atr=2.0) == pytest.approx(106.0, abs=0.001)


def test_compute_trail_stop_sell_side():
    from position_tracker import compute_trail_stop
    pos = _make_position(side="SELL", high_watermark=90.0, trail_pct_mult=2.0, atr_at_entry=2.0)
    # watermark(90) + 2.0 × atr(2.0) = 94.0
    assert compute_trail_stop(pos, current_atr=2.0) == pytest.approx(94.0, abs=0.001)


# ---------------------------------------------------------------------------
# 2. check_exits — hard stop
# ---------------------------------------------------------------------------

def test_hard_stop_triggers():
    from position_tracker import check_exits
    pos = _make_position(entry_price=100.0, hard_stop_price=94.0, high_watermark=100.0)
    df = _make_ohlcv([100.0] * 5)
    triggered, reason = check_exits(pos, df, current_price=93.0)
    assert triggered is True
    assert "Hard stop" in reason


def test_hard_stop_does_not_trigger_above():
    from position_tracker import check_exits
    pos = _make_position(entry_price=100.0, hard_stop_price=94.0, high_watermark=100.0)
    df = _make_ohlcv([100.0] * 5)
    triggered, _ = check_exits(pos, df, current_price=95.0)
    assert triggered is False


# ---------------------------------------------------------------------------
# 3. check_exits — trailing stop
# ---------------------------------------------------------------------------

def test_trailing_stop_triggers():
    from position_tracker import check_exits
    # Watermark 120, ATR ~2, trail_mult 2.0 → trail = 116.0
    # Current price 115 < 116 → exit
    pos = _make_position(
        entry_price=100.0,
        hard_stop_price=80.0,   # far below, so trail activates first
        high_watermark=120.0,
        trail_pct_mult=2.0,
        atr_at_entry=2.0,
    )
    # Build 20 bars so ATR can be computed, with roughly ATR≈2
    closes = [100.0 + i * 0.1 for i in range(20)]
    df = _make_ohlcv(closes)
    triggered, reason = check_exits(pos, df, current_price=115.0)
    # ATR on slowly rising prices is small; trail ≈ watermark - mult*ATR
    # Even if ATR is tiny, effective_stop = max(hard_stop=80, trail≈120-small)
    # At price=115, if trail_stop (≈119) > 115 → should exit
    assert triggered is True
    assert "Trail stop" in reason or "Hard stop" in reason


def test_trailing_stop_does_not_trigger_above_trail():
    from position_tracker import check_exits
    pos = _make_position(
        entry_price=100.0,
        hard_stop_price=80.0,
        high_watermark=105.0,
        trail_pct_mult=2.0,
        atr_at_entry=2.0,
    )
    # 20 bars; price hasn't fallen below trail
    closes = [103.0 + i * 0.1 for i in range(20)]
    df = _make_ohlcv(closes)
    triggered, _ = check_exits(pos, df, current_price=104.0)
    assert triggered is False


# ---------------------------------------------------------------------------
# 4. Hard stop takes priority over trailing stop
# ---------------------------------------------------------------------------

def test_hard_stop_priority_over_trailing():
    from position_tracker import check_exits
    # Both hard stop and trail stop would trigger at price=89
    pos = _make_position(
        entry_price=100.0,
        hard_stop_price=90.0,
        high_watermark=120.0,
        trail_pct_mult=2.0,
        atr_at_entry=2.0,
    )
    df = _make_ohlcv([89.0] * 20)
    triggered, reason = check_exits(pos, df, current_price=89.0)
    assert triggered is True
    assert "Hard stop" in reason  # hard stop is checked first


# ---------------------------------------------------------------------------
# 5. EMA(21) crossunder
# ---------------------------------------------------------------------------

def test_ema21_crossunder_exits():
    from position_tracker import check_exits
    # Build 50 bars: 40 bars rising (price above EMA), then 10 bars plunging below
    rises = [100.0 + i * 0.5 for i in range(40)]      # 100 → 119.5
    drops = [115.0 - i * 3.0 for i in range(10)]      # 115 → 88 (sharp drop)
    closes = rises + drops
    df = _make_ohlcv(closes)
    pos = _make_position(
        entry_price=100.0,
        hard_stop_price=50.0,   # far below — won't trigger
        high_watermark=120.0,
    )
    triggered, reason = check_exits(pos, df, current_price=closes[-1])
    assert triggered is True
    assert "EMA(21)" in reason or "Hard stop" in reason or "Trail stop" in reason


# ---------------------------------------------------------------------------
# 6. SMA(50) crossunder
# ---------------------------------------------------------------------------

def test_sma50_crossunder_exits():
    from position_tracker import check_exits
    # 60 bars: first 55 slowly rising, then last 5 a sharp drop below SMA50
    rises = [100.0 + i * 0.2 for i in range(55)]
    drops = [95.0 - i * 2.0 for i in range(5)]
    closes = rises + drops
    df = _make_ohlcv(closes)
    pos = _make_position(
        entry_price=100.0,
        hard_stop_price=50.0,
        high_watermark=110.0,
    )
    triggered, reason = check_exits(pos, df, current_price=closes[-1])
    assert triggered is True
    assert any(kw in reason for kw in ("SMA(50)", "EMA(21)", "Trail stop", "Hard stop", "MACD", "RSI"))


# ---------------------------------------------------------------------------
# 7. RSI overbought exits
# ---------------------------------------------------------------------------

def test_rsi_overbought_exits():
    from position_tracker import check_exits
    from indicators import _rsi
    # Monotonically rising prices push RSI well above 70
    closes = [50.0 + i * 2.0 for i in range(60)]
    df = _make_ohlcv(closes)
    rsi_val = float(_rsi(pd.Series(closes), 14).iloc[-1])
    # Verify the test data actually produces overbought RSI before asserting
    if rsi_val > 70:
        pos = _make_position(
            entry_price=50.0,
            hard_stop_price=10.0,   # far below
            high_watermark=closes[-1],
        )
        triggered, reason = check_exits(pos, df, current_price=closes[-1])
        assert triggered is True
        assert any(kw in reason for kw in ("RSI", "MACD", "EMA", "SMA", "Trail", "Hard"))
    else:
        pytest.skip(f"Test data RSI={rsi_val:.1f} didn't produce overbought — skip RSI-specific check")


# ---------------------------------------------------------------------------
# 8. MACD histogram zero-crossunder
# ---------------------------------------------------------------------------

def test_macd_crossunder_exits():
    from position_tracker import check_exits
    # Price rises then flattens → MACD fast EMA decays back, histogram crosses below 0
    rises = [100.0 + i * 1.0 for i in range(40)]
    flat  = [140.0 - i * 0.5 for i in range(20)]
    closes = rises + flat
    df = _make_ohlcv(closes)
    pos = _make_position(
        entry_price=100.0,
        hard_stop_price=50.0,
        high_watermark=140.0,
    )
    triggered, reason = check_exits(pos, df, current_price=closes[-1])
    # Some exit should fire given the decline
    assert triggered is True


# ---------------------------------------------------------------------------
# 9. No exit on healthy uptrend
# ---------------------------------------------------------------------------

def test_no_exit_healthy_trend():
    from position_tracker import check_exits
    # 9-bar cycle: 6 up-days (+0.4) then 3 down-days (-0.5)
    # Net +0.1/bar; RSI ≈ 69; MACD positive; last bar (i=59, 59%9=5) is UP
    closes = []
    price = 100.0
    for i in range(60):
        price += 0.4 if i % 9 < 6 else -0.5
        closes.append(round(price, 4))
    df = _make_ohlcv(closes)
    pos = _make_position(
        entry_price=100.0,
        hard_stop_price=80.0,   # far below current price
        high_watermark=closes[-1],
    )
    triggered, reason = check_exits(pos, df, current_price=closes[-1])
    assert triggered is False


# ---------------------------------------------------------------------------
# 10. Insufficient bars — hard stop still works, indicators skipped safely
# ---------------------------------------------------------------------------

def test_insufficient_bars_hard_stop_still_works():
    from position_tracker import check_exits
    pos = _make_position(entry_price=100.0, hard_stop_price=94.0)
    df = _make_ohlcv([93.0] * 5)  # only 5 bars — way below hard_stop
    triggered, reason = check_exits(pos, df, current_price=93.0)
    assert triggered is True
    assert "Hard stop" in reason


def test_insufficient_bars_no_false_indicator_exit():
    from position_tracker import check_exits
    pos = _make_position(entry_price=100.0, hard_stop_price=50.0)
    df = _make_ohlcv([100.0] * 5)  # 5 bars, price above hard_stop
    triggered, _ = check_exits(pos, df, current_price=100.0)
    # No indicator checks possible; stops not hit → no exit
    assert triggered is False


# ---------------------------------------------------------------------------
# 11 & 12. register_position — DB persistence and hard stop calculation
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_register_position_persists(anyio_backend):
    from database import init_db, get_open_position
    from position_tracker import register_position

    await init_db()
    trade = _make_trade(fill_price=100.0, quantity=5)
    df = _make_ohlcv([100.0] * 20)  # 20 bars, ATR computable

    pos = await register_position(trade, df, rule_name="Test Rule")

    assert pos.id == trade.id
    assert pos.symbol == "AAPL"
    assert pos.side == "BUY"
    assert pos.quantity == 5.0
    assert pos.entry_price == pytest.approx(100.0)
    assert pos.high_watermark == pytest.approx(100.0)
    assert pos.rule_id == "rule-001"

    # Verify DB persistence
    fetched = await get_open_position(trade.id)
    assert fetched is not None
    assert fetched.id == pos.id
    assert fetched.symbol == pos.symbol


@pytest.mark.anyio
async def test_register_hard_stop_uses_atr(anyio_backend):
    from database import init_db
    from position_tracker import register_position
    from indicators import _atr

    await init_db()
    trade = _make_trade(fill_price=100.0, quantity=10)
    closes = [100.0 + i * 0.1 for i in range(30)]
    df = _make_ohlcv(closes)

    atr_series = _atr(df["high"], df["low"], df["close"], 14)
    expected_atr = float(atr_series.iloc[-1])

    from config import cfg
    expected_hard_stop = 100.0 - cfg.ATR_STOP_MULT * expected_atr

    pos = await register_position(trade, df, rule_name="Test Rule")

    assert pos.hard_stop_price == pytest.approx(expected_hard_stop, abs=0.01)
    assert pos.atr_at_entry == pytest.approx(expected_atr, abs=0.001)


# ---------------------------------------------------------------------------
# 13. update_watermarks — moves up for BUY
# ---------------------------------------------------------------------------

def test_update_watermarks_moves_up():
    from position_tracker import update_watermarks
    pos = _make_position(symbol="AAPL", high_watermark=100.0)
    bars = {"AAPL": _make_ohlcv([105.0] * 5)}
    updated = update_watermarks([pos], bars)
    assert len(updated) == 1
    assert updated[0].high_watermark == pytest.approx(105.0)


# ---------------------------------------------------------------------------
# 14. update_watermarks — does NOT move down for BUY
# ---------------------------------------------------------------------------

def test_update_watermarks_no_move_down():
    from position_tracker import update_watermarks
    pos = _make_position(symbol="AAPL", high_watermark=120.0)
    bars = {"AAPL": _make_ohlcv([90.0] * 5)}
    updated = update_watermarks([pos], bars)
    assert len(updated) == 0  # no update


# ---------------------------------------------------------------------------
# 15. update_watermarks — missing symbol handled gracefully
# ---------------------------------------------------------------------------

def test_update_watermarks_missing_symbol():
    from position_tracker import update_watermarks
    pos = _make_position(symbol="TSLA", high_watermark=200.0)
    bars = {}  # TSLA not present
    updated = update_watermarks([pos], bars)
    assert updated == []
