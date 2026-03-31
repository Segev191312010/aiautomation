"""Regression tests for ai_optimizer — prompt building and partial context safety."""
import json
import pytest

import config
import database
from database import init_db


@pytest.fixture
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "optimizer.db")
    monkeypatch.setattr(config.cfg, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)


def test_get_ai_decisions_survives_partial_rule_rows(monkeypatch):
    """Live optimizer must not crash on rule_performance rows with missing keys."""
    from optimizer_prompts import format_rule_performance

    # Row missing several keys — would KeyError with old r['rule_name'] access
    partial_rows = [
        {"rule_name": "Good Rule", "total_trades": 10, "win_rate": 60,
         "profit_factor": 1.5, "total_pnl": 500, "verdict": "keep"},
        {"total_trades": 5},  # missing rule_name, win_rate, etc.
        {},  # completely empty
    ]

    text = format_rule_performance(partial_rows)
    assert "Good Rule" in text
    assert "?" in text  # missing keys should fallback to '?'


@pytest.mark.anyio
async def test_get_ai_decisions_builds_prompt_without_crash(_isolated_db, anyio_backend, monkeypatch):
    """_get_ai_decisions must build the prompt from context without crashing."""
    await init_db()
    monkeypatch.setattr(config.cfg, "ANTHROPIC_API_KEY", "test-key")

    # Mock ai_call to capture the prompt
    captured = {}

    async def mock_ai_call(*, system, prompt, source, model, max_tokens, temperature):
        captured["system"] = system
        captured["prompt"] = prompt

        class FakeResult:
            ok = True
            text = json.dumps({"confidence": 0.5, "reasoning": "test", "min_score": {"value": 55}})
            tokens_in = 100
            tokens_out = 50
            model_used = "mock"
            fallback_used = False
        return FakeResult()

    import ai_model_router
    monkeypatch.setattr(ai_model_router, "ai_call", mock_ai_call)

    from ai_optimizer import _get_ai_decisions
    context = {
        "lookback_days": 30,
        "trade_count": 5,
        "rule_performance": [
            {"total_trades": 3},  # partial row — old code would KeyError
        ],
        "sector_performance": [],
        "time_patterns": [],
        "score_analysis": {},
        "bracket_analysis": {},
        "current_params": {},
        "market_snapshot": {},
        "current_regime": "BULL",
        "pnl_summary": {},
    }

    result = await _get_ai_decisions(context)
    assert result is not None
    assert "prompt" in captured
    assert "?" in captured["prompt"]  # partial row formatted with fallback
