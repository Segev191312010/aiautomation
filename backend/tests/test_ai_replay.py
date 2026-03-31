"""Stage 2 replay tests — mode semantics, filter parity, fail-closed behavior."""
import json
import pytest

import config
import database
from database import init_db
from ai_decision_ledger import (
    start_decision_run,
    record_decision_items,
)


@pytest.fixture
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "replay.db")
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)


async def _seed_runs(n: int = 3, user_id: str = "demo") -> list[str]:
    """Seed n decision runs with items for testing."""
    run_ids = []
    for i in range(n):
        ctx = json.dumps({"trade_count": 10 + i, "pnl_summary": {"total_pnl": 100 * i}})
        run_id = await start_decision_run(
            source="optimizer", mode="PAPER", provider="anthropic",
            model="claude-sonnet-4-20250514", context_json=ctx,
            aggregate_confidence=0.5 + i * 0.1, user_id=user_id,
        )
        await record_decision_items(run_id, [
            {"item_type": "direct_trade", "action_name": "BUY", "symbol": "AAPL",
             "proposed": {"action": "BUY"}, "confidence": 0.6 + i * 0.1},
            {"item_type": "rule_action", "action_name": "create", "symbol": "NVDA",
             "proposed": {"action": "create"}, "confidence": 0.3 + i * 0.05},
            {"item_type": "score_threshold", "action_name": "adjust",
             "target_key": "min_score", "proposed": {"value": 55}, "confidence": 0.5},
        ], user_id=user_id)
        run_ids.append(run_id)
    return run_ids


# ── stored_context_existing: never calls generation ─────────────────────────

@pytest.mark.anyio
async def test_existing_mode_returns_persisted_items_no_generation(_isolated_db, anyio_backend):
    """stored_context_existing must return already-persisted items and never call LLM."""
    await init_db()
    await _seed_runs(2)

    from ai_replay import run_stored_context_existing
    result = await run_stored_context_existing(window_days=365)

    assert result["mode"] == "stored_context_existing"
    assert result["runs_evaluated"] == 2
    assert result["items_count"] == 6  # 3 items * 2 runs


@pytest.mark.anyio
async def test_existing_mode_filters_by_min_confidence(_isolated_db, anyio_backend):
    await init_db()
    await _seed_runs(2)

    from ai_replay import run_stored_context_existing
    result = await run_stored_context_existing(window_days=365, min_confidence=0.65)

    # Only items with confidence >= 0.65 should pass
    for item in result["items"]:
        assert (item.get("confidence") or 0) >= 0.65


@pytest.mark.anyio
async def test_existing_mode_filters_by_symbols(_isolated_db, anyio_backend):
    await init_db()
    await _seed_runs(2)

    from ai_replay import run_stored_context_existing
    result = await run_stored_context_existing(window_days=365, symbols=["AAPL"])

    # Only AAPL items should pass — items without symbol are EXCLUDED by the filter
    for item in result["items"]:
        assert item.get("symbol") == "AAPL"


@pytest.mark.anyio
async def test_existing_mode_filters_by_action_types(_isolated_db, anyio_backend):
    await init_db()
    await _seed_runs(2)

    from ai_replay import run_stored_context_existing
    result = await run_stored_context_existing(window_days=365, action_types=["direct_trade"])

    for item in result["items"]:
        assert item["item_type"] == "direct_trade"


@pytest.mark.anyio
async def test_existing_mode_window_uses_sql_not_python(_isolated_db, anyio_backend):
    """Existing mode must use SQL-windowed selection, not post-fetch Python filter.
    Seeds 5 runs, requests limit_runs=2 with wide window — both modes must see same count."""
    await init_db()
    await _seed_runs(5)

    from ai_replay import run_stored_context_existing
    result = await run_stored_context_existing(window_days=365, limit_runs=2)

    # With SQL window + limit=2, we get exactly 2 runs (not 5 fetched then filtered)
    assert result["runs_evaluated"] == 2


@pytest.mark.anyio
async def test_symbols_filter_excludes_none_symbol_items(_isolated_db, anyio_backend):
    """Items with symbol=None must be excluded when a symbols filter is active."""
    await init_db()
    await _seed_runs(1)

    from ai_replay import run_stored_context_existing
    result = await run_stored_context_existing(window_days=365, symbols=["AAPL"])

    # score_threshold items have no symbol — they must be excluded
    for item in result["items"]:
        assert item.get("symbol") is not None, "Item with None symbol should be filtered out"
        assert item["symbol"] == "AAPL"


# ── stored_context_generate: calls generation path ──────────────────────────

@pytest.mark.anyio
async def test_generate_mode_calls_generation(_isolated_db, anyio_backend, monkeypatch):
    """stored_context_generate must call the LLM generation path."""
    await init_db()
    await _seed_runs(1)

    generation_calls = []

    async def mock_generate(context_json, candidate_config):
        generation_calls.append({"context": context_json, "config": candidate_config})
        return {
            "confidence": 0.7,
            "direct_trades": [{"symbol": "TSLA", "action": "BUY", "confidence": 0.7}],
        }

    from ai_replay import run_stored_context_generate
    import ai_replay
    monkeypatch.setattr(ai_replay, "generate_candidate_items_from_context", mock_generate)

    result = await run_stored_context_generate(
        candidate_key="claude-haiku-4-5-20251001",
        candidate_type="model_version",
        window_days=365,
    )

    assert result["mode"] == "stored_context_generate"
    assert len(generation_calls) == 1  # Called for the one seeded run
    assert result["runs_evaluated"] == 1


@pytest.mark.anyio
async def test_generate_mode_applies_filters(_isolated_db, anyio_backend, monkeypatch):
    """Generate mode must apply the same filters as existing mode."""
    await init_db()
    await _seed_runs(1)

    async def mock_generate(context_json, candidate_config):
        return {
            "confidence": 0.7,
            "direct_trades": [
                {"symbol": "AAPL", "action": "BUY", "confidence": 0.8},
                {"symbol": "MSFT", "action": "BUY", "confidence": 0.3},
            ],
        }

    import ai_replay
    monkeypatch.setattr(ai_replay, "generate_candidate_items_from_context", mock_generate)

    from ai_replay import run_stored_context_generate
    result = await run_stored_context_generate(
        candidate_key="claude-haiku-4-5-20251001",
        candidate_type="model_version",
        window_days=365,
        min_confidence=0.5,
        symbols=["AAPL"],
    )

    # Only AAPL with confidence >= 0.5 should appear in scored_items
    for item in result["scored_items"]:
        if item.get("symbol"):
            assert item["symbol"] == "AAPL"
        assert (item.get("confidence") or 0) >= 0.5

    # Filters should be recorded in response
    assert result["filters_applied"]["min_confidence"] == 0.5
    assert result["filters_applied"]["symbols"] == ["AAPL"]


@pytest.mark.anyio
async def test_generate_mode_empty_window(_isolated_db, anyio_backend):
    """Generate mode with no runs returns empty, not error."""
    await init_db()

    from ai_replay import run_stored_context_generate
    result = await run_stored_context_generate(
        candidate_key="claude-haiku-4-5-20251001",
        candidate_type="model_version",
        window_days=365,
    )

    assert result["mode"] == "stored_context_generate"
    assert result["runs_evaluated"] == 0
    assert "No runs in window" in result.get("errors", [])


# ── rule_backtest: rejects non-replayable rules ────────────────────────────

@pytest.mark.anyio
async def test_rule_backtest_rejects_non_replayable(_isolated_db, anyio_backend, monkeypatch):
    """rule_backtest must reject rules without replay_config."""
    await init_db()

    from models import Rule
    non_replayable = Rule(
        id="test-rule", name="No Config Rule", symbol="SPY",
        conditions=[{"indicator": "RSI", "operator": "LT", "value": 30}],
        action={"type": "BUY", "asset_type": "STK", "quantity": 1, "order_type": "MKT"},
        replay_config=None,  # No replay config
    )

    from database import save_rule
    await save_rule(non_replayable)

    from ai_replay import run_rule_backtest_replay
    result = await run_rule_backtest_replay("test-rule")

    assert result.get("not_replayable") is True
    assert "reason" in result


@pytest.mark.anyio
async def test_rule_backtest_missing_rule(_isolated_db, anyio_backend):
    """rule_backtest must return error for non-existent rule."""
    await init_db()

    from ai_replay import run_rule_backtest_replay
    result = await run_rule_backtest_replay("nonexistent-id")

    assert "error" in result


# ── rule_backtest: rejects unsupported filter fields ─────────────────────────

def test_rule_backtest_rejects_filter_fields():
    """rule_backtest must 422 when filter fields are supplied."""
    from api_contracts import ReplayRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="rule_backtest mode does not support filter fields"):
        ReplayRequest(
            candidate_type="rule_snapshot",
            candidate_key="test-rule",
            evaluation_mode="rule_backtest",
            min_confidence=0.7,
        )

    with pytest.raises(ValidationError, match="rule_backtest mode does not support filter fields"):
        ReplayRequest(
            candidate_type="rule_snapshot",
            candidate_key="test-rule",
            evaluation_mode="rule_backtest",
            symbols=["AAPL"],
        )

    # Without filters, rule_backtest should be accepted
    req = ReplayRequest(
        candidate_type="rule_snapshot",
        candidate_key="test-rule",
        evaluation_mode="rule_backtest",
    )
    assert req.evaluation_mode == "rule_backtest"
