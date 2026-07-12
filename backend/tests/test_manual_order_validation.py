"""Manual-order boundary and fat-finger policy regressions."""
from __future__ import annotations

import math
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from config import _validate_config, cfg
from manual_order_validation import ManualOrderRequest
from routers.orders import place_manual_order


@pytest.fixture(autouse=True)
def restore_manual_order_policy():
    original = (
        cfg.SIM_MODE,
        cfg.JWT_SECRET,
        cfg.MANUAL_ORDER_MAX_QUANTITY,
        cfg.MANUAL_ORDER_MAX_NOTIONAL,
    )
    cfg.SIM_MODE = False
    cfg.JWT_SECRET = "manual-order-test-secret"
    cfg.MANUAL_ORDER_MAX_QUANTITY = 10_000
    cfg.MANUAL_ORDER_MAX_NOTIONAL = 100_000.0
    try:
        yield
    finally:
        (
            cfg.SIM_MODE,
            cfg.JWT_SECRET,
            cfg.MANUAL_ORDER_MAX_QUANTITY,
            cfg.MANUAL_ORDER_MAX_NOTIONAL,
        ) = original


@pytest.mark.parametrize("symbol", ["AAPL", "BRK.B", "BTC-USD", "1", "A123456789"])
def test_accepts_canonical_symbols(symbol):
    request = ManualOrderRequest(symbol=symbol, action="BUY", quantity=1)
    assert request.symbol == symbol


@pytest.mark.parametrize(
    "symbol",
    [
        "",
        "aapl",
        " AAPL",
        "AAPL ",
        ".AAPL",
        "-AAPL",
        "AAPL/USD",
        "AAPL$",
        "ABCDEFGHIJK",
        "\u00c5APL",
        123,
        None,
    ],
)
def test_rejects_noncanonical_symbols(symbol):
    with pytest.raises(ValidationError):
        ManualOrderRequest(symbol=symbol, action="BUY", quantity=1)


@pytest.mark.parametrize("quantity", [0, -1, 10_001, 1.0, "1", True])
def test_rejects_invalid_or_coerced_quantities(quantity):
    with pytest.raises(ValidationError):
        ManualOrderRequest(symbol="AAPL", action="BUY", quantity=quantity)


def test_quantity_cap_can_be_lowered_by_configuration():
    cfg.MANUAL_ORDER_MAX_QUANTITY = 5

    assert ManualOrderRequest(symbol="AAPL", action="BUY", quantity=5).quantity == 5
    with pytest.raises(ValidationError, match="quantity exceeds maximum 5"):
        ManualOrderRequest(symbol="AAPL", action="BUY", quantity=6)


@pytest.mark.parametrize("limit_price", [0, -1, math.nan, math.inf, -math.inf, "10", True])
def test_rejects_nonpositive_nonfinite_or_coerced_limit_prices(limit_price):
    with pytest.raises(ValidationError):
        ManualOrderRequest(
            symbol="AAPL",
            action="BUY",
            quantity=1,
            order_type="LMT",
            limit_price=limit_price,
        )


def test_limit_order_requires_price_and_market_order_forbids_it():
    with pytest.raises(ValidationError, match="limit_price is required"):
        ManualOrderRequest(symbol="AAPL", action="BUY", quantity=1, order_type="LMT")

    with pytest.raises(ValidationError, match="must be omitted"):
        ManualOrderRequest(symbol="AAPL", action="BUY", quantity=1, limit_price=10.0)


@pytest.mark.parametrize("asset_type", ["OPT", "FUT"])
def test_rejects_derivatives_without_multiplier_aware_notional(asset_type):
    with pytest.raises(ValidationError, match="multiplier-aware notional"):
        ManualOrderRequest(
            symbol="AAPL",
            action="BUY",
            quantity=1,
            asset_type=asset_type,
        )


def test_limit_order_enforces_notional_cap_inclusively():
    cfg.MANUAL_ORDER_MAX_NOTIONAL = 100.0

    accepted = ManualOrderRequest(
        symbol="AAPL", action="BUY", quantity=4, order_type="LMT", limit_price=25.0
    )
    assert accepted.limit_price == 25.0

    with pytest.raises(ValidationError, match="notional .* exceeds maximum"):
        ManualOrderRequest(
            symbol="AAPL", action="BUY", quantity=4, order_type="LMT", limit_price=25.01
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("MANUAL_ORDER_MAX_QUANTITY", 0, "between 1 and 10000"),
        ("MANUAL_ORDER_MAX_QUANTITY", 10_001, "between 1 and 10000"),
        ("MANUAL_ORDER_MAX_NOTIONAL", 0.0, "finite positive"),
        ("MANUAL_ORDER_MAX_NOTIONAL", math.inf, "finite positive"),
        ("MANUAL_ORDER_MAX_NOTIONAL", math.nan, "finite positive"),
    ],
)
def test_config_validation_rejects_unsafe_manual_policy(field, value, message):
    setattr(cfg, field, value)
    with pytest.raises(ValueError, match=message):
        _validate_config(cfg)


@pytest.mark.anyio
async def test_sim_market_order_over_notional_cap_has_no_side_effect(anyio_backend):
    cfg.SIM_MODE = True
    cfg.MANUAL_ORDER_MAX_NOTIONAL = 100.0
    body = ManualOrderRequest(symbol="AAPL", action="BUY", quantity=2)
    execute_order = AsyncMock(return_value=(True, "filled"))

    with patch("routers.orders.get_latest_price", new=AsyncMock(return_value=50.01)), patch(
        "simulation.sim_engine.execute_order", new=execute_order
    ):
        with pytest.raises(HTTPException) as exc_info:
            await place_manual_order(body)

    assert exc_info.value.status_code == 422
    execute_order.assert_not_awaited()


@pytest.mark.anyio
async def test_live_market_order_over_notional_cap_has_no_side_effect(anyio_backend):
    cfg.MANUAL_ORDER_MAX_NOTIONAL = 100.0
    body = ManualOrderRequest(symbol="AAPL", action="SELL", quantity=2)
    place_order = AsyncMock()

    with patch("ibkr_client.ibkr.is_connected", return_value=True), patch(
        "routers.orders.get_latest_price", new=AsyncMock(return_value=50.01)
    ), patch("routers.orders.place_order", new=place_order):
        with pytest.raises(HTTPException) as exc_info:
            await place_manual_order(body)

    assert exc_info.value.status_code == 422
    place_order.assert_not_awaited()


@pytest.mark.anyio
async def test_live_market_buy_reserves_slippage_inside_notional_cap(anyio_backend):
    body = ManualOrderRequest(symbol="AAPL", action="BUY", quantity=1_000)
    place_order = AsyncMock()

    with patch("ibkr_client.ibkr.is_connected", return_value=True), patch(
        "routers.orders.get_latest_price", new=AsyncMock(return_value=100.0)
    ), patch("routers.orders.place_order", new=place_order):
        with pytest.raises(HTTPException) as exc_info:
            await place_manual_order(body)

    assert exc_info.value.status_code == 422
    assert "100500.00" in str(exc_info.value.detail)
    place_order.assert_not_awaited()


@pytest.mark.anyio
async def test_live_market_buy_is_converted_to_validated_protective_limit(anyio_backend):
    body = ManualOrderRequest(symbol="AAPL", action="BUY", quantity=2)
    fake_trade = Mock()
    fake_trade.model_dump.return_value = {"id": "trade-1"}

    with patch("ibkr_client.ibkr.is_connected", return_value=True), patch(
        "routers.orders.get_latest_price", new=AsyncMock(return_value=100.0)
    ), patch("routers.orders.place_order", new=AsyncMock(return_value=fake_trade)) as mock_place:
        await place_manual_order(body)

    rule = mock_place.await_args.args[0]
    assert rule.action.order_type == "LMT"
    assert rule.action.limit_price == 100.5


@pytest.mark.anyio
async def test_live_manual_sell_is_verified_and_classified_as_exit(anyio_backend):
    body = ManualOrderRequest(symbol="AAPL", action="SELL", quantity=2)
    position = Mock(symbol="AAPL", asset_type="STK", qty=5.0)
    fake_trade = Mock()
    fake_trade.model_dump.return_value = {"id": "trade-1"}

    with patch("ibkr_client.ibkr.is_connected", return_value=True), patch(
        "ibkr_client.ibkr.get_positions", new=AsyncMock(return_value=[position])
    ), patch("routers.orders.get_latest_price", new=AsyncMock(return_value=100.0)), patch(
        "routers.orders.place_order", new=AsyncMock(return_value=fake_trade)
    ) as mock_place:
        await place_manual_order(body)

    assert mock_place.await_args.kwargs["is_exit"] is True
    assert mock_place.await_args.kwargs["has_existing_position"] is True


@pytest.mark.anyio
async def test_live_manual_sell_without_verified_position_fails_closed(anyio_backend):
    body = ManualOrderRequest(symbol="AAPL", action="SELL", quantity=2)
    place_order = AsyncMock()

    with patch("ibkr_client.ibkr.is_connected", return_value=True), patch(
        "ibkr_client.ibkr.get_positions", new=AsyncMock(return_value=[])
    ), patch("routers.orders.get_latest_price", new=AsyncMock(return_value=100.0)), patch(
        "routers.orders.place_order", new=place_order
    ):
        with pytest.raises(HTTPException) as exc_info:
            await place_manual_order(body)

    assert exc_info.value.status_code == 422
    place_order.assert_not_awaited()


@pytest.mark.anyio
async def test_live_manual_sell_rejects_invalid_broker_position_data(anyio_backend):
    body = ManualOrderRequest(symbol="AAPL", action="SELL", quantity=1)
    position = Mock(symbol="AAPL", asset_type="STK", qty=math.nan)
    place_order = AsyncMock()

    with patch("ibkr_client.ibkr.is_connected", return_value=True), patch(
        "ibkr_client.ibkr.get_positions", new=AsyncMock(return_value=[position])
    ), patch("routers.orders.get_latest_price", new=AsyncMock(return_value=100.0)), patch(
        "routers.orders.place_order", new=place_order
    ):
        with pytest.raises(HTTPException) as exc_info:
            await place_manual_order(body)

    assert exc_info.value.status_code == 503
    place_order.assert_not_awaited()


@pytest.mark.anyio
async def test_sim_limit_order_does_not_ignore_non_marketable_limit(anyio_backend):
    cfg.SIM_MODE = True
    body = ManualOrderRequest(
        symbol="AAPL", action="BUY", quantity=1, order_type="LMT", limit_price=10.0
    )
    execute_order = AsyncMock()

    with patch("routers.orders.get_latest_price", new=AsyncMock(return_value=100.0)), patch(
        "simulation.sim_engine.execute_order", new=execute_order
    ):
        with pytest.raises(HTTPException) as exc_info:
            await place_manual_order(body)

    assert exc_info.value.status_code == 409
    execute_order.assert_not_awaited()


@pytest.mark.anyio
async def test_live_market_order_without_valid_quote_fails_closed(anyio_backend):
    body = ManualOrderRequest(symbol="AAPL", action="BUY", quantity=1)
    place_order = AsyncMock()

    with patch("ibkr_client.ibkr.is_connected", return_value=True), patch(
        "routers.orders.get_latest_price", new=AsyncMock(return_value=math.nan)
    ), patch("yahoo_data.yf_quotes", new=AsyncMock(return_value=[])), patch(
        "routers.orders.place_order", new=place_order
    ):
        with pytest.raises(HTTPException) as exc_info:
            await place_manual_order(body)

    assert exc_info.value.status_code == 503
    place_order.assert_not_awaited()


@pytest.mark.anyio
async def test_valid_live_limit_order_uses_validated_request_without_quote(anyio_backend):
    body = ManualOrderRequest(
        symbol="BRK.B", action="BUY", quantity=2, order_type="LMT", limit_price=100.0
    )
    fake_trade = Mock()
    fake_trade.model_dump.return_value = {"id": "trade-1"}
    get_price = AsyncMock(side_effect=AssertionError("limit order must not fetch a quote"))

    with patch("ibkr_client.ibkr.is_connected", return_value=True), patch(
        "routers.orders.get_latest_price", new=get_price
    ), patch("routers.orders.place_order", new=AsyncMock(return_value=fake_trade)) as mock_place:
        payload = await place_manual_order(body)

    assert payload == {"id": "trade-1"}
    get_price.assert_not_awaited()
    rule = mock_place.await_args.args[0]
    assert rule.symbol == "BRK.B"
    assert rule.action.limit_price == 100.0
