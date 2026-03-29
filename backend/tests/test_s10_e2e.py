"""Stage 10 end-to-end tests — decision run -> items -> learning metrics, origin tracking, evaluation."""
import json
import pytest

import config
import database
from database import init_db, save_rule, save_trade, finalize_trade_outcome
from models import Rule, Trade
from ai_decision_ledger import (
    start_decision_run,
    record_decision_items,
    mark_decision_item_applied,
    attach_realized_trade,
    finalize_decision_run,
    get_decision_run,
    get_decision_items,
)
from ai_evaluator import (
    create_evaluation_run,
    complete_evaluation_run,
    build_slices_from_items,
    save_evaluation_slices,
    get_evaluation_slices,
)


@pytest.fixture
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "s10e2e.db")
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)


# ── E2E 1: Decision run -> direct trade -> finalized -> learning metrics ─────

@pytest.mark.anyio
async def test_decision_run_to_realized_trade_to_learning(_isolated_db, anyio_backend):
    """Full path: optimizer creates run + items, trade opens, trade closes, learning sees it."""
    await init_db()

    # 1. Create a decision run
    run_id = await start_decision_run(
        source="optimizer", mode="PAPER", provider="anthropic",
        model="claude-sonnet-4-6", context_json=json.dumps({"test": True}),
        reasoning="Test cycle", aggregate_confidence=0.75,
        input_tokens=500, output_tokens=200,
    )

    # 2. Record items
    item_ids = await record_decision_items(run_id, [
        {"item_type": "direct_trade", "action_name": "BUY", "symbol": "AAPL",
         "proposed": {"qty": 10, "stop_price": 145.0}, "confidence": 0.8},
        {"item_type": "score_threshold", "action_name": "adjust", "target_key": "min_score",
         "proposed": {"value": 55}, "confidence": 0.7},
    ], regime="bull")

    trade_item_id = item_ids[0]
    score_item_id = item_ids[1]

    # 3. Mark score item applied
    await mark_decision_item_applied(score_item_id, applied_json={"value": 55})

    # 4. Create a trade linked to the decision item
    trade = Trade(
        rule_id="ai-direct", rule_name="AI Direct", symbol="AAPL",
        action="BUY", asset_type="STK", quantity=10, order_type="MKT",
        limit_price=None, fill_price=150.0, status="FILLED",
        timestamp="2026-03-20T10:00:00+00:00", source="ai_direct",
        mode="PAPER", opened_at="2026-03-20T10:00:00+00:00",
        entry_price=150.0, decision_id=trade_item_id,
    )
    trade.position_id = trade.id
    await save_trade(trade)

    # Mark item applied with trade id
    await mark_decision_item_applied(trade_item_id, created_trade_id=trade.id)

    # 5. Create exit trade and finalize
    exit_trade = Trade(
        rule_id="ai-direct", rule_name="EXIT:AI Direct", symbol="AAPL",
        action="SELL", asset_type="STK", quantity=10, order_type="MKT",
        limit_price=None, fill_price=160.0, status="FILLED",
        timestamp="2026-03-21T14:00:00+00:00", source="ai_direct",
        mode="PAPER", position_id=trade.id, decision_id=trade_item_id,
    )
    await save_trade(exit_trade)

    finalized = await finalize_trade_outcome(
        exit_trade.id, position_side="BUY", entry_price=150.0,
        exit_price=160.0, fees=0.0, close_reason="trailing_stop",
        position_id=trade.id,
    )
    assert finalized is not None
    assert finalized.realized_pnl == 100.0

    # 6. Finalize the decision run
    await finalize_decision_run(run_id, status="completed")

    # 7. Verify: item has realized outcome
    items = await get_decision_items(run_id)
    trade_item = next(i for i in items if i["item_type"] == "direct_trade")
    assert trade_item["realized_pnl"] == 100.0
    assert trade_item["score_status"] == "direct_realized"
    assert trade_item["created_trade_id"] == trade.id

    # 8. Verify: learning metrics from ledger
    from ai_learning import evaluate_past_decisions
    metrics = await evaluate_past_decisions(30)
    assert metrics["total_runs"] >= 1
    # Items should include scored ones
    assert metrics["scored_decisions"] >= 1

    # 9. Verify: cost report uses model from run
    from ai_learning import compute_cost_report
    costs = await compute_cost_report(30)
    assert costs["total_calls"] >= 1


# ── E2E 2: AI rule creation stamps origin_decision_id ────────────────────────

@pytest.mark.anyio
async def test_ai_rule_creation_stamps_origin(_isolated_db, anyio_backend):
    """AI-created rule carries origin_decision_id and origin_run_id."""
    await init_db()

    run_id = await start_decision_run(
        source="optimizer", mode="PAPER",
        context_json=json.dumps({}), reasoning="Rule creation test",
    )
    item_ids = await record_decision_items(run_id, [
        {"item_type": "rule_action", "action_name": "create",
         "proposed": {"name": "AI Test Rule"}, "confidence": 0.7},
    ])

    from ai_rule_lab import apply_rule_actions
    results = await apply_rule_actions([
        {
            "action": "create",
            "rule_payload": {
                "name": "AI Origin Test",
                "symbol": "MSFT",
                "conditions": [{"indicator": "RSI", "params": {"length": 14}, "operator": "<", "value": 30}],
                "logic": "AND",
                "action": {"type": "BUY", "asset_type": "STK", "quantity": 5, "order_type": "MKT"},
                "cooldown_minutes": 60,
            },
            "reason": "Test origin tracking",
            "confidence": 0.7,
        }
    ], author="ai", allow_active=False,
       decision_run_id=run_id, decision_item_ids=item_ids)

    assert results[0]["ok"] is True
    rule_id = results[0]["rule_id"]

    # Verify rule has origin IDs
    from database import get_rule
    rule = await get_rule(rule_id)
    assert rule is not None
    assert rule.origin_decision_id == item_ids[0]
    assert rule.origin_run_id == run_id


# ── E2E 3: Evaluation slices persist and match ──────────────────────────────

@pytest.mark.anyio
async def test_evaluation_slices_from_decision_items(_isolated_db, anyio_backend):
    """Build slices from decision items, persist them, and verify they match."""
    await init_db()

    run_id = await start_decision_run(
        source="optimizer", mode="PAPER",
        context_json=json.dumps({}),
    )
    item_ids = await record_decision_items(run_id, [
        {"item_type": "direct_trade", "action_name": "BUY", "symbol": "AAPL",
         "proposed": {}, "confidence": 0.8},
        {"item_type": "direct_trade", "action_name": "BUY", "symbol": "MSFT",
         "proposed": {}, "confidence": 0.6},
        {"item_type": "score_threshold", "action_name": "adjust",
         "proposed": {}, "confidence": 0.7},
    ], regime="bull")

    # Simulate realized outcomes on trade items
    await mark_decision_item_applied(item_ids[0], created_trade_id="t1")
    await attach_realized_trade(item_ids[0], "t1", realized_pnl=50.0, realized_at="2026-03-21T14:00:00+00:00")
    await mark_decision_item_applied(item_ids[1], created_trade_id="t2")
    await attach_realized_trade(item_ids[1], "t2", realized_pnl=-20.0, realized_at="2026-03-21T15:00:00+00:00")
    await mark_decision_item_applied(item_ids[2])

    # Get items and build slices
    items = await get_decision_items(run_id)
    slices = build_slices_from_items(items)

    # Create evaluation run and persist slices
    eval_id = await create_evaluation_run(
        candidate_type="decision_run", candidate_key=run_id,
        evaluation_mode="stored_context", request_json={},
    )
    await save_evaluation_slices(eval_id, slices)

    overall = next(s for s in slices if s["slice_type"] == "overall")
    await complete_evaluation_run(eval_id, summary=overall["metrics"])

    # Verify persisted slices
    loaded = await get_evaluation_slices(eval_id)
    assert len(loaded) >= 3  # overall + at least action_type + symbol slices

    overall_loaded = next(s for s in loaded if s["slice_type"] == "overall")
    assert overall_loaded["count"] == 3
    assert overall_loaded["scored_count"] == 2  # two trade items with realized_pnl
    assert overall_loaded["net_pnl"] == 30.0  # 50 + (-20)
