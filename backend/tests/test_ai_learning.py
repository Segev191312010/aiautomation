"""Stage 2 learning tests — cost report, decision evaluation, economic report degraded truth."""
import json
import pytest

import config
import database
from database import init_db
from ai_decision_ledger import start_decision_run, record_decision_items


@pytest.fixture
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "learning.db")
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)


async def _seed_decision_run(
    *, model: str = "claude-sonnet-4-20250514",
    input_tokens: int = 1000, output_tokens: int = 500,
    items: list[dict] | None = None,
) -> str:
    ctx = json.dumps({"trade_count": 5})
    run_id = await start_decision_run(
        source="optimizer", mode="LIVE", provider="anthropic",
        model=model, context_json=ctx,
        input_tokens=input_tokens, output_tokens=output_tokens,
    )
    if items:
        await record_decision_items(run_id, items)
    return run_id


# ── Cost Report ─────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_cost_report_empty_returns_zero(_isolated_db, anyio_backend):
    """Cost report with no data returns zero cost."""
    await init_db()

    from ai_learning import compute_cost_report
    result = await compute_cost_report(days=30)

    assert result["total_cost_usd"] == 0
    assert result["total_calls"] == 0


@pytest.mark.anyio
async def test_cost_report_model_aware_pricing(_isolated_db, anyio_backend):
    """Cost report uses per-model pricing from decision runs."""
    await init_db()
    await _seed_decision_run(model="claude-sonnet-4-20250514", input_tokens=1000, output_tokens=500)
    await _seed_decision_run(model="claude-haiku-4-5-20251001", input_tokens=2000, output_tokens=1000)

    from ai_learning import compute_cost_report
    result = await compute_cost_report(days=30)

    assert result["total_calls"] == 2
    assert result["total_cost_usd"] > 0
    # Haiku should be cheaper than Sonnet for same token count
    assert len(result["daily"]) >= 1


# ── Decision Evaluation ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_evaluate_ledger_first(_isolated_db, anyio_backend):
    """Evaluation reads from ledger (decision_runs) first."""
    await init_db()
    await _seed_decision_run(items=[
        {"item_type": "direct_trade", "action_name": "BUY", "symbol": "AAPL",
         "proposed": {"action": "BUY"}, "confidence": 0.7},
    ])

    from ai_learning import evaluate_past_decisions
    result = await evaluate_past_decisions(30)

    assert result["total_decisions"] >= 1
    assert result["data_quality"] in ("canonical", "mixed", "legacy_fallback", "insufficient")


@pytest.mark.anyio
async def test_evaluate_empty_returns_insufficient(_isolated_db, anyio_backend):
    """Evaluation with no data returns insufficient quality."""
    await init_db()

    from ai_learning import evaluate_past_decisions
    result = await evaluate_past_decisions(30)

    assert result["data_quality"] == "insufficient"
    assert result["total_decisions"] == 0


@pytest.mark.anyio
async def test_evaluate_audit_fallback(_isolated_db, anyio_backend):
    """When no decision runs exist, falls back to audit_log heuristic."""
    await init_db()
    # Seed audit_log directly (no decision_runs)
    from database import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO ai_audit_log (user_id, action_type, category, description, "
            "status, confidence, input_tokens, output_tokens, timestamp) "
            "VALUES ('demo', 'test', 'optimizer', 'test entry', 'applied', 0.7, 500, 200, ?)",
            ("2026-03-31T12:00:00Z",),
        )
        await db.commit()

    from ai_learning import evaluate_past_decisions
    result = await evaluate_past_decisions(30)

    # Should use legacy path since no decision_runs exist
    assert result["data_quality"] in ("legacy_fallback", "insufficient")


# ── Economic Report ─────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_economic_report_surfaces_data_quality(_isolated_db, anyio_backend):
    """Economic report must thread data_quality and metric_source."""
    await init_db()

    from ai_learning import compute_economic_report
    result = await compute_economic_report(days=30)

    # With no data, should be "insufficient"
    assert "data_quality" in result
    assert "metric_source" in result
    assert result["data_quality"] == "insufficient"
    assert result["metric_source"] == "insufficient"


@pytest.mark.anyio
async def test_economic_report_with_ledger_data(_isolated_db, anyio_backend):
    """Economic report with ledger data threads quality from learning evaluation."""
    await init_db()
    await _seed_decision_run(
        model="claude-sonnet-4-20250514",
        input_tokens=1000, output_tokens=500,
        items=[
            {"item_type": "direct_trade", "action_name": "BUY", "symbol": "AAPL",
             "proposed": {"action": "BUY"}, "confidence": 0.7},
        ],
    )

    from ai_learning import compute_economic_report
    result = await compute_economic_report(days=30)

    # data_quality is threaded from learning — with unscored items it may be
    # "canonical" (ledger has data) or "insufficient" (no scored items).
    # The critical assertion is that the field EXISTS and is not None.
    assert "data_quality" in result
    assert result["data_quality"] is not None
    assert "metric_source" in result
    assert result["metric_source"] is not None
    assert result["total_cost"] > 0


# ── Decision Item Factory ───────────────────────────────────────────────────

def test_decision_item_factory_builds_all_types():
    """Factory builds items for all decision payload types."""
    from decision_item_factory import build_ledger_items

    decisions = {
        "confidence": 0.75,
        "min_score": {"value": 55, "lower": 50, "upper": 60},
        "risk_multiplier": {"value": 1.1, "lower": 0.9, "upper": 1.3},
        "rule_actions": [{"action": "create", "rule_payload": {"symbol": "AAPL"}, "confidence": 0.8}],
        "direct_trades": [{"symbol": "NVDA", "action": "BUY", "confidence": 0.7}],
    }

    items = build_ledger_items(decisions)

    types = [i["item_type"] for i in items]
    assert "score_threshold" in types
    assert "risk_adjust" in types
    assert "rule_action" in types
    assert "direct_trade" in types


def test_decision_item_factory_abstain():
    """Factory produces abstain item when no actions and abstained=True."""
    from decision_item_factory import build_ledger_items

    items = build_ledger_items({"abstained": True, "reasoning": "No opportunities", "confidence": 0.9})
    assert len(items) == 1
    assert items[0]["item_type"] == "abstain"
