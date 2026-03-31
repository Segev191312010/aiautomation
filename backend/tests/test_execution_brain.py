import pytest
from unittest.mock import patch

from execution_brain import (
    _pending_direct_queue,
    choose_candidates,
    drain_direct_candidates,
    queue_direct_candidates,
)


def setup_function():
    # Drain queue to ensure clean state between tests
    while not _pending_direct_queue.empty():
        try:
            _pending_direct_queue.get_nowait()
        except Exception:
            break


def test_choose_candidates_prefers_exit_for_same_symbol():
    selected = choose_candidates(
        [
            {"symbol": "AAPL", "source": "rule", "score": 91.0, "is_exit": False, "risk_pct": 1.0},
        ],
        [
            {"symbol": "AAPL", "source": "ai_direct", "score": 72.0, "is_exit": True, "risk_pct": 1.0},
        ],
    )

    assert len(selected) == 1
    assert selected[0]["source"] == "ai_direct"
    assert selected[0]["is_exit"] is True


def test_queue_and_drain_direct_candidates_keep_highest_priority_per_symbol():
    queued = queue_direct_candidates([
        {
            "symbol": "NVDA",
            "action": "BUY",
            "order_type": "MKT",
            "stop_price": 860.0,
            "invalidation": "Break below support",
            "reason": "Momentum continuation",
            "confidence": 0.61,
        },
        {
            "symbol": "NVDA",
            "action": "SELL",
            "order_type": "MKT",
            "stop_price": 860.0,
            "invalidation": "Trend failure",
            "reason": "Protect open gains",
            "confidence": 0.55,
        },
    ])

    assert queued == 2

    drained = drain_direct_candidates()
    assert len(drained) == 1
    assert drained[0]["symbol"] == "NVDA"
    assert drained[0]["is_exit"] is True
    assert drain_direct_candidates() == []


@pytest.mark.anyio
async def test_ai_optimizer_queues_direct_trades(anyio_backend):
    from ai_optimizer import _apply_decisions

    decision = {
        "symbol": "MSFT",
        "action": "BUY",
        "order_type": "MKT",
        "stop_price": 401.0,
        "invalidation": "Break below VWAP",
        "reason": "Fresh trend confirmation",
        "confidence": 0.74,
    }

    with patch("execution_brain.queue_direct_candidates", return_value=1) as mock_queue:
        results = await _apply_decisions({"direct_trades": [decision]}, {})

    mock_queue.assert_called_once_with([decision])
    assert any("direct_trade_queued: MSFT BUY" in item for item in results["applied"])
