import pytest
from unittest.mock import AsyncMock, patch

import config
import database
from database import init_db
from execution_brain import (
    choose_candidates,
    drain_direct_candidates,
    queue_direct_candidates,
)


@pytest.fixture
def _isolated_db(tmp_path, monkeypatch):
    """Redirect DB_PATH to a fresh sqlite file per test."""
    db_path = str(tmp_path / "execution_brain.db")
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    # db.core reads cfg.DB_PATH at open-time, so patching cfg is sufficient


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


@pytest.mark.anyio
async def test_queue_and_drain_direct_candidates_keep_highest_priority_per_symbol(
    _isolated_db, anyio_backend
):
    await init_db()

    queued = await queue_direct_candidates([
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

    drained = await drain_direct_candidates()
    assert len(drained) == 1
    assert drained[0]["symbol"] == "NVDA"
    assert drained[0]["is_exit"] is True
    # Second drain returns nothing — rows now in 'draining' state
    assert await drain_direct_candidates() == []


@pytest.mark.anyio
async def test_queue_survives_restart_simulation(_isolated_db, anyio_backend):
    """Persistence test: a candidate queued before the 'restart' is still
    drainable after the in-memory state has been discarded (since the DB is
    the source of truth)."""
    await init_db()

    queued = await queue_direct_candidates([
        {
            "symbol": "MSFT",
            "action": "BUY",
            "order_type": "MKT",
            "stop_price": 401.0,
            "invalidation": "Break VWAP",
            "reason": "Trend continuation",
            "confidence": 0.70,
        },
    ])
    assert queued == 1

    # "Restart simulation": nothing else to reset — there is no in-memory
    # queue any more. Draining via a fresh call must still return the row.
    drained = await drain_direct_candidates()
    assert len(drained) == 1
    assert drained[0]["symbol"] == "MSFT"


@pytest.mark.anyio
async def test_drain_drops_stale_candidates_past_ttl(_isolated_db, anyio_backend):
    """Rows older than max_age_seconds must be marked expired and excluded."""
    from db.direct_candidates import queue_candidate, get_candidate_status
    from datetime import datetime, timedelta, timezone

    await init_db()

    # Insert a row with a stale queued_at stamp directly via the CRUD helper
    # and then overwrite queued_at to be 1 hour old.
    await queue_candidate(
        "cand-stale",
        "TSLA",
        {"symbol": "TSLA", "score": 50, "is_exit": False, "queued_at": "fake"},
        ttl_seconds=900,
    )
    # Rewrite queued_at to an hour ago
    from db.core import get_db
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    async with get_db() as db:
        await db.execute(
            "UPDATE direct_candidates SET queued_at=? WHERE id=?",
            (old_ts, "cand-stale"),
        )
        await db.commit()

    drained = await drain_direct_candidates(max_age_seconds=900)
    assert drained == []
    status = await get_candidate_status("cand-stale")
    assert status == "expired"


@pytest.mark.anyio
async def test_mark_candidate_status_applied_after_execution(_isolated_db, anyio_backend):
    """queue → drain → execution success → mark applied."""
    from db.direct_candidates import mark_candidate_status, get_candidate_status

    await init_db()

    await queue_direct_candidates([
        {
            "symbol": "AMD",
            "action": "BUY",
            "order_type": "MKT",
            "stop_price": 150.0,
            "invalidation": "Break support",
            "reason": "Trend",
            "confidence": 0.62,
            "decision_id": "cand-applied-1",
        },
    ])

    drained = await drain_direct_candidates()
    assert len(drained) == 1
    cand_id = drained[0].get("_candidate_id")
    assert cand_id == "cand-applied-1"

    await mark_candidate_status(cand_id, "applied")
    assert await get_candidate_status(cand_id) == "applied"


@pytest.mark.anyio
async def test_purge_expired_candidates_on_startup(_isolated_db, anyio_backend):
    """Startup purge marks stale queued/draining rows as expired."""
    from db.direct_candidates import purge_expired_candidates, get_candidate_status
    from db.core import get_db
    from datetime import datetime, timedelta, timezone

    await init_db()

    await queue_direct_candidates([
        {
            "symbol": "GOOG",
            "action": "BUY",
            "order_type": "MKT",
            "stop_price": 140.0,
            "invalidation": "Break support",
            "reason": "Trend",
            "confidence": 0.55,
            "decision_id": "cand-purge-1",
        },
    ])

    # Make the row look like it was queued 2 hours ago (TTL = 900s default)
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    async with get_db() as db:
        await db.execute(
            "UPDATE direct_candidates SET queued_at=? WHERE id=?",
            (old_ts, "cand-purge-1"),
        )
        await db.commit()

    expired = await purge_expired_candidates()
    assert expired == 1
    assert await get_candidate_status("cand-purge-1") == "expired"


@pytest.mark.anyio
async def test_ai_optimizer_queues_direct_trades(_isolated_db, anyio_backend):
    from ai_optimizer import _apply_decisions

    await init_db()

    decision = {
        "symbol": "MSFT",
        "action": "BUY",
        "order_type": "MKT",
        "stop_price": 401.0,
        "invalidation": "Break below VWAP",
        "reason": "Fresh trend confirmation",
        "confidence": 0.74,
    }

    mock_queue = AsyncMock(return_value=1)
    with patch("execution_brain.queue_direct_candidates", new=mock_queue):
        results = await _apply_decisions({"direct_trades": [decision]}, {})

    mock_queue.assert_awaited_once_with([decision])
    assert any("direct_trade_queued: MSFT BUY" in item for item in results["applied"])
