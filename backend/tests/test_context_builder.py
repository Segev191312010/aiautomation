"""Context builder regressions for optimizer and rule lab inputs."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from context_builder import build_optimizer_context


@pytest.mark.anyio
async def test_build_optimizer_context_includes_market_snapshot(anyio_backend):
    advisor_payload = {
        "matched_trades": [
            {"rule_name": "AI Trend", "pnl": 12.0, "pnl_pct": 1.2, "signal_score": 74, "symbol": "NVDA"}
        ],
        "pnl_summary": {"realized_pnl": 12.0},
        "rules": [],
    }
    market_snapshot = {
        "available": True,
        "candidates": [
            {
                "symbol": "NVDA",
                "screener_score": 82.5,
                "setup": "breakout",
                "relative_volume": 1.9,
                "momentum_20d": 12.4,
                "sector": "Tech",
                "notes": ["MA stack aligned"],
            }
        ],
        "setup_counts": {"breakout": 1},
        "sector_counts": {"Tech": 1},
    }

    with patch("ai_advisor.fetch_advisor_data", new=AsyncMock(return_value=advisor_payload)), patch(
        "screener.build_market_opportunity_snapshot",
        new=AsyncMock(return_value=market_snapshot),
    ), patch("context_builder._load_current_regime", new=AsyncMock(return_value="BULL")):
        context = await build_optimizer_context(lookback_days=30)

    assert context["lookback_days"] == 30
    assert context["market_snapshot"]["available"] is True
    assert context["market_snapshot"]["candidates"][0]["symbol"] == "NVDA"
    assert context["current_regime"] == "BULL"
