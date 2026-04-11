"""Stage 10 decision ledger tests — run lifecycle, item CRUD, and queries."""
import json
import pytest

import config
import database
from database import init_db
from ai_decision_ledger import (
    start_decision_run,
    finalize_decision_run,
    record_decision_items,
    mark_decision_item_applied,
    mark_decision_item_blocked,
    mark_decision_item_shadow,
    attach_realized_trade,
    get_decision_runs,
    get_decision_run,
    get_decision_items,
)


@pytest.fixture
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "ledger.db")
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)


@pytest.mark.anyio
async def test_create_and_read_decision_run(_isolated_db, anyio_backend):
    await init_db()

    ctx = json.dumps({"rules": [], "regime": "bull"})
    run_id = await start_decision_run(
        source="optimizer", mode="PAPER", provider="anthropic",
        model="claude-sonnet-4-6", prompt_version="v1",
        context_json=ctx, reasoning="Test run",
        aggregate_confidence=0.75, input_tokens=100, output_tokens=50,
    )
    assert run_id

    run = await get_decision_run(run_id)
    assert run is not None
    assert run["source"] == "optimizer"
    assert run["mode"] == "PAPER"
    assert run["model"] == "claude-sonnet-4-6"
    assert run["status"] == "created"
    assert run["aggregate_confidence"] == 0.75
    assert run["context_hash"]  # should be populated


@pytest.mark.anyio
async def test_finalize_decision_run(_isolated_db, anyio_backend):
    await init_db()

    run_id = await start_decision_run(
        source="optimizer", mode="LIVE",
        context_json=json.dumps({"test": True}),
    )
    await finalize_decision_run(run_id, status="completed")

    run = await get_decision_run(run_id)
    assert run["status"] == "completed"
    assert run["completed_at"] is not None


@pytest.mark.anyio
async def test_finalize_with_error(_isolated_db, anyio_backend):
    await init_db()

    run_id = await start_decision_run(
        source="optimizer", mode="PAPER",
        context_json=json.dumps({}),
    )
    await finalize_decision_run(run_id, status="error", error="LLM timeout")

    run = await get_decision_run(run_id)
    assert run["status"] == "error"
    assert run["error"] == "LLM timeout"


@pytest.mark.anyio
async def test_record_and_read_items(_isolated_db, anyio_backend):
    await init_db()

    run_id = await start_decision_run(
        source="optimizer", mode="PAPER",
        context_json=json.dumps({"test": True}),
    )

    items = [
        {"item_type": "score_threshold", "target_key": "min_score", "proposed": {"value": 55}, "confidence": 0.8},
        {"item_type": "direct_trade", "action_name": "BUY", "symbol": "AAPL", "proposed": {"qty": 10}, "confidence": 0.7},
        {"item_type": "rule_action", "action_name": "pause", "target_key": "rule-1", "proposed": {"status": "paused"}, "confidence": 0.6},
    ]
    item_ids = await record_decision_items(run_id, items, regime="bull")

    assert len(item_ids) == 3

    loaded = await get_decision_items(run_id)
    assert len(loaded) == 3
    assert loaded[0]["item_type"] == "score_threshold"
    assert loaded[0]["item_index"] == 0
    assert loaded[0]["regime"] == "bull"
    assert loaded[1]["item_type"] == "direct_trade"
    assert loaded[1]["symbol"] == "AAPL"
    assert loaded[2]["item_type"] == "rule_action"
    assert loaded[2]["action_name"] == "pause"


@pytest.mark.anyio
async def test_mark_item_applied(_isolated_db, anyio_backend):
    await init_db()

    run_id = await start_decision_run(source="optimizer", mode="PAPER", context_json="{}")
    [item_id] = await record_decision_items(run_id, [
        {"item_type": "direct_trade", "action_name": "BUY", "symbol": "MSFT", "proposed": {"qty": 5}, "confidence": 0.9},
    ])

    await mark_decision_item_applied(item_id, applied_json={"qty": 5, "fill_price": 400.0}, created_trade_id="trade-123")

    items = await get_decision_items(run_id)
    assert items[0]["gate_status"] == "applied"
    assert items[0]["created_trade_id"] == "trade-123"
    assert items[0]["applied_json"]["qty"] == 5


@pytest.mark.anyio
async def test_mark_item_blocked(_isolated_db, anyio_backend):
    await init_db()

    run_id = await start_decision_run(source="optimizer", mode="PAPER", context_json="{}")
    [item_id] = await record_decision_items(run_id, [
        {"item_type": "direct_trade", "action_name": "BUY", "symbol": "TSLA", "proposed": {}, "confidence": 0.3},
    ])

    await mark_decision_item_blocked(item_id, reason="Confidence below threshold")

    items = await get_decision_items(run_id)
    assert items[0]["gate_status"] == "blocked"
    assert items[0]["gate_reason"] == "Confidence below threshold"


@pytest.mark.anyio
async def test_mark_item_shadow(_isolated_db, anyio_backend):
    await init_db()

    run_id = await start_decision_run(source="optimizer", mode="PAPER", context_json="{}")
    [item_id] = await record_decision_items(run_id, [
        {"item_type": "risk_adjust", "proposed": {"mult": 1.2}, "confidence": 0.5},
    ])

    await mark_decision_item_shadow(item_id, notes="Shadow mode active")

    items = await get_decision_items(run_id)
    assert items[0]["gate_status"] == "shadow"


@pytest.mark.anyio
async def test_attach_realized_trade(_isolated_db, anyio_backend):
    await init_db()

    run_id = await start_decision_run(source="optimizer", mode="LIVE", context_json="{}")
    [item_id] = await record_decision_items(run_id, [
        {"item_type": "direct_trade", "action_name": "BUY", "symbol": "GOOG", "proposed": {"qty": 3}, "confidence": 0.8},
    ])

    await mark_decision_item_applied(item_id, created_trade_id="trade-456")
    await attach_realized_trade(item_id, trade_id="trade-456", realized_pnl=150.0, realized_at="2026-03-22T14:00:00+00:00")

    items = await get_decision_items(run_id)
    assert items[0]["realized_trade_id"] == "trade-456"
    assert items[0]["realized_pnl"] == 150.0
    assert items[0]["score_status"] == "direct_realized"


@pytest.mark.anyio
async def test_get_decision_runs_with_filters(_isolated_db, anyio_backend):
    await init_db()

    await start_decision_run(source="optimizer", mode="PAPER", context_json="{}")
    await start_decision_run(source="manual_direct_trade", mode="LIVE", context_json="{}")
    await start_decision_run(source="optimizer", mode="LIVE", context_json="{}")

    all_runs = await get_decision_runs(limit=10)
    assert len(all_runs) == 3

    optimizer_runs = await get_decision_runs(source="optimizer")
    assert len(optimizer_runs) == 2

    live_runs = await get_decision_runs(mode="LIVE")
    assert len(live_runs) == 2


@pytest.mark.anyio
async def test_item_counts_in_run_response(_isolated_db, anyio_backend):
    await init_db()

    run_id = await start_decision_run(source="optimizer", mode="PAPER", context_json="{}")
    item_ids = await record_decision_items(run_id, [
        {"item_type": "score_threshold", "proposed": {}, "confidence": 0.7},
        {"item_type": "direct_trade", "proposed": {}, "confidence": 0.8},
        {"item_type": "rule_action", "proposed": {}, "confidence": 0.6},
    ])

    await mark_decision_item_applied(item_ids[0])
    await mark_decision_item_applied(item_ids[1])
    await mark_decision_item_blocked(item_ids[2], reason="test")

    run = await get_decision_run(run_id)
    assert run["item_counts"]["applied"] == 2
    assert run["item_counts"]["blocked"] == 1


@pytest.mark.anyio
async def test_abstained_run(_isolated_db, anyio_backend):
    await init_db()

    run_id = await start_decision_run(
        source="optimizer", mode="PAPER",
        context_json=json.dumps({"regime": "uncertain"}),
        abstained=True, aggregate_confidence=0.2,
    )
    [item_id] = await record_decision_items(run_id, [
        {"item_type": "abstain", "proposed": {"reason": "Low confidence regime"}, "confidence": 0.2},
    ])

    run = await get_decision_run(run_id)
    assert run["abstained"] is True

    items = await get_decision_items(run_id)
    assert items[0]["item_type"] == "abstain"


# ── HB1-05 regression: SQLite FK cascade enforcement ───────────────────────
# Verifies PRAGMA foreign_keys=ON is honored by db/core connection factory:
# deleting a parent ai_decision_runs row must cascade-delete child items
# from ai_decision_items (declared with ON DELETE CASCADE).


@pytest.mark.anyio
async def test_hb1_05_decision_run_delete_cascades_to_items(_isolated_db, anyio_backend):
    await init_db()

    run_id = await start_decision_run(
        source="optimizer", mode="PAPER",
        context_json=json.dumps({"test": "cascade"}),
    )
    await record_decision_items(run_id, [
        {"item_type": "score_threshold", "target_key": "min_score",
         "proposed": {"value": 55}, "confidence": 0.8},
        {"item_type": "direct_trade", "action_name": "BUY", "symbol": "AAPL",
         "proposed": {"qty": 10}, "confidence": 0.7},
    ])
    items_before = await get_decision_items(run_id)
    assert len(items_before) == 2

    from db.core import get_db
    async with get_db() as db:
        # Confirm pragma is actually ON in this connection
        async with db.execute("PRAGMA foreign_keys") as cur:
            row = await cur.fetchone()
        assert row[0] == 1, "foreign_keys pragma must be ON for cascade to fire"

        await db.execute("DELETE FROM ai_decision_runs WHERE id=?", (run_id,))
        await db.commit()

    items_after = await get_decision_items(run_id)
    assert items_after == [], "child decision_items should be cascade-deleted"
