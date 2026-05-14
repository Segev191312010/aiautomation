"""
Batch 1 regression tests — close the NaN → live-position chain.

Pins three invariants:

1. A non-finite ``avgFillPrice`` from the broker NEVER results in a
   PENDING→FILLED transition. The trade is marked ERROR, fill_callbacks are
   NOT invoked, and order_recovery treats the case as the named outcome
   ``avg_fill_non_finite``.
2. ``order_executor._get_limit_price`` tries every ticker field (last, close,
   bid, ask) — a NaN value in one field does NOT short-circuit the chain.
3. ``register_entry_position_from_fill`` rejects non-finite fill_price and
   uses the documented ``DEGRADED_ATR_FRACTION = 0.02`` sentinel when bars
   are insufficient — the constant cannot be silently retuned without
   failing this test.
"""
from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from market_data import finite_positive
from models import Trade
from services import order_lifecycle, order_recovery


# ---------------------------------------------------------------------------
# 1. avgFillPrice NaN → never FILLED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_nan_fill_price_marks_error_not_filled():
    """A FILLED status with NaN fill_price must mark trade ERROR and skip callbacks."""
    trade = Trade(
        id="t-nan-1",
        rule_id="r1",
        rule_name="test",
        symbol="AAPL",
        action="BUY",  # type: ignore[arg-type]
        asset_type="STK",
        quantity=10,
        order_type="LMT",
        limit_price=100.0,
        fill_price=None,
        status="PENDING",
        order_id=1,
        timestamp="2026-05-14T00:00:00+00:00",
        mode="LIVE",
        opened_at="2026-05-14T00:00:00+00:00",
    )
    callback_calls: list[Trade] = []

    with patch("services.order_recovery.update_trade_status", new=AsyncMock()) as mock_upd, \
         patch("services.order_recovery.order_lifecycle.persist_filled_trade_record",
               new=AsyncMock()) as mock_persist:
        result = await order_recovery.reconcile_trade_status_update(
            trade,
            "Filled",
            fill_price=float("nan"),
            fill_callbacks=[lambda t: callback_calls.append(t)],
        )

    assert result == "ERROR", "NaN fill_price must NOT result in FILLED"
    assert trade.status == "ERROR"
    assert callback_calls == [], "fill_callbacks must NOT be invoked on NaN fill"
    mock_persist.assert_not_called()
    mock_upd.assert_awaited_with(trade.id, "ERROR")


@pytest.mark.asyncio
async def test_reconcile_finite_fill_price_persists_and_fires_callbacks():
    """Sanity check: a finite, positive fill_price still flows through normally."""
    trade = Trade(
        id="t-ok-1",
        rule_id="r1",
        rule_name="test",
        symbol="AAPL",
        action="BUY",  # type: ignore[arg-type]
        asset_type="STK",
        quantity=10,
        order_type="LMT",
        limit_price=100.0,
        fill_price=None,
        status="PENDING",
        order_id=1,
        timestamp="2026-05-14T00:00:00+00:00",
        mode="LIVE",
        opened_at="2026-05-14T00:00:00+00:00",
    )
    callback_calls: list[Trade] = []

    persisted_trade = trade.model_copy(update={"status": "FILLED", "fill_price": 123.45})
    with patch("services.order_recovery.order_lifecycle.persist_filled_trade_record",
               new=AsyncMock(return_value=persisted_trade)):
        result = await order_recovery.reconcile_trade_status_update(
            trade,
            "Filled",
            fill_price=123.45,
            fill_callbacks=[lambda t: callback_calls.append(t)],
        )

    assert result == "FILLED"
    assert len(callback_calls) == 1
    assert callback_calls[0].fill_price == 123.45


# ---------------------------------------------------------------------------
# 2. _get_limit_price fallback chain tries every ticker field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_limit_price_skips_nan_and_tries_next_field():
    """NaN ticker.last must NOT prevent the chain from trying ticker.close/bid/ask."""
    from order_executor import _get_limit_price

    fake_ticker = SimpleNamespace(
        last=float("nan"),   # NaN — should be skipped
        close=float("nan"),  # NaN — should be skipped
        bid=99.50,           # finite — should be used
        ask=100.50,
    )

    mock_ib = MagicMock()
    mock_ib.reqTickersAsync = AsyncMock(return_value=[fake_ticker])
    fake_ibkr = SimpleNamespace(
        ib=mock_ib,
        make_stock_contract=lambda sym: SimpleNamespace(symbol=sym),
    )

    with patch("order_executor.ibkr", fake_ibkr):
        result = await _get_limit_price("AAPL", "BUY", slip_pct=0.005)

    # BUY → 99.50 * 1.005 = 99.9975 → rounded to 100.00 (2 decimals)
    assert result == pytest.approx(99.9975, abs=1e-3) or result == 100.00


@pytest.mark.asyncio
async def test_get_limit_price_returns_none_when_all_fields_nan():
    """If every ticker field is NaN, _get_limit_price must fall through to yfinance."""
    from order_executor import _get_limit_price

    fake_ticker = SimpleNamespace(
        last=float("nan"), close=float("nan"),
        bid=float("nan"), ask=float("nan"),
    )

    mock_ib = MagicMock()
    mock_ib.reqTickersAsync = AsyncMock(return_value=[fake_ticker])
    fake_ibkr = SimpleNamespace(
        ib=mock_ib,
        make_stock_contract=lambda sym: SimpleNamespace(symbol=sym),
    )

    # yfinance fallback also returns NaN — final result must be None, not NaN
    fake_yf_info = SimpleNamespace(last_price=float("nan"), regular_market_price=float("nan"))

    with patch("order_executor.ibkr", fake_ibkr), \
         patch("yfinance.Ticker", return_value=SimpleNamespace(fast_info=fake_yf_info)):
        result = await _get_limit_price("AAPL", "BUY")

    assert result is None, "All-NaN price chain must yield None, not NaN"


# ---------------------------------------------------------------------------
# 3. register_entry_position_from_fill — NaN guard + degraded-ATR sentinel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_entry_rejects_nan_fill_price():
    """NaN fill_price must be rejected (previously `not nan` is False so it slipped)."""
    trade = Trade(
        id="t-nan-2",
        rule_id="r1",
        rule_name="test",
        symbol="AAPL",
        action="BUY",  # type: ignore[arg-type]
        asset_type="STK",
        quantity=10,
        order_type="LMT",
        limit_price=100.0,
        fill_price=float("nan"),
        status="FILLED",
        order_id=1,
        timestamp="2026-05-14T00:00:00+00:00",
        mode="LIVE",
        opened_at="2026-05-14T00:00:00+00:00",
    )
    with patch("services.order_lifecycle.register_position", new=AsyncMock()) as mock_reg:
        ok = await order_lifecycle.register_entry_position_from_fill(trade)

    assert ok is False, "Non-finite fill_price must be rejected"
    mock_reg.assert_not_called()


def test_degraded_atr_fraction_constant_is_pinned():
    """The sentinel ATR fraction is a literal 0.02 — fail loudly if retuned."""
    assert order_lifecycle.DEGRADED_ATR_FRACTION == 0.02, (
        "DEGRADED_ATR_FRACTION must remain 0.02 — see plan invariant. "
        "Changing this value requires updating the data_gap audit-log contract "
        "AND this test."
    )


@pytest.mark.asyncio
async def test_register_entry_degraded_path_uses_sentinel_atr():
    """When bars fetch returns insufficient rows, register with the sentinel ATR."""
    import pandas as pd

    trade = Trade(
        id="t-deg-1",
        rule_id="r1",
        rule_name="test",
        symbol="AAPL",
        action="BUY",  # type: ignore[arg-type]
        asset_type="STK",
        quantity=10,
        order_type="LMT",
        limit_price=100.0,
        fill_price=100.0,
        status="FILLED",
        order_id=1,
        timestamp="2026-05-14T00:00:00+00:00",
        mode="LIVE",
        opened_at="2026-05-14T00:00:00+00:00",
    )

    short_df = pd.DataFrame({
        "open": [99.0], "high": [101.0], "low": [98.0], "close": [100.0], "volume": [1000],
    })

    with patch("services.order_lifecycle.get_historical_bars",
               new=AsyncMock(return_value=short_df)), \
         patch("services.order_lifecycle.register_position", new=AsyncMock()) as mock_reg:
        ok = await order_lifecycle.register_entry_position_from_fill(trade)

    assert ok is True
    # Verify the sentinel ATR matches fill_price * DEGRADED_ATR_FRACTION exactly
    mock_reg.assert_awaited_once()
    kwargs = mock_reg.await_args.kwargs
    assert kwargs["degraded_atr"] == pytest.approx(
        100.0 * order_lifecycle.DEGRADED_ATR_FRACTION
    ), "Degraded ATR must equal fill_price * DEGRADED_ATR_FRACTION exactly"


# ---------------------------------------------------------------------------
# 4. finite_positive helper itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    (1.0, 1.0),
    (0.01, 0.01),
    (100.5, 100.5),
    ("123.45", 123.45),
    (0.0, None),
    (-1.0, None),
    (None, None),
    (float("nan"), None),
    (float("inf"), None),
    (float("-inf"), None),
    ("not_a_number", None),
])
def test_finite_positive(value, expected):
    """The finite_positive helper rejects every non-finite / non-positive input."""
    result = finite_positive(value)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)
