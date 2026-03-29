"""Order route regressions."""
from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from config import cfg
from routers.orders import ManualOrderRequest, place_manual_order


@pytest.fixture(autouse=True)
def restore_sim_mode():
    prev_sim = cfg.SIM_MODE
    try:
        yield
    finally:
        cfg.SIM_MODE = prev_sim


@pytest.mark.anyio
async def test_place_manual_order_bypasses_autopilot_authority(anyio_backend):
    cfg.SIM_MODE = False
    body = ManualOrderRequest(symbol="AAPL", action="BUY", quantity=2, order_type="MKT")
    fake_trade = Mock()
    fake_trade.model_dump.return_value = {"id": "trade-1"}

    with patch("ibkr_client.ibkr.is_connected", return_value=True), patch(
        "routers.orders.place_order",
        new=AsyncMock(return_value=fake_trade),
    ) as mock_place:
        payload = await place_manual_order(body)

    assert payload == {"id": "trade-1"}
    _, kwargs = mock_place.await_args
    assert kwargs["source"] == "manual"
    assert kwargs["require_autopilot_authority"] is False
