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


# ── P2-3 / F2-08: Bull/Bear debate JSON-parse telemetry ─────────────────────


@pytest.mark.anyio
async def test_bull_bear_parse_failure_increments_counter_and_logs(
    anyio_backend, monkeypatch, caplog
):
    """Degraded debate must log a warning AND bump the failure counter."""
    import ai_advisor

    # Force a clean counter window
    monkeypatch.setattr(ai_advisor, "_debate_failure_count", 0, raising=False)
    monkeypatch.setattr(ai_advisor, "_debate_failure_day", None, raising=False)
    monkeypatch.setattr(ai_advisor, "_debate_threshold_emitted", False, raising=False)
    monkeypatch.setattr(config.cfg, "AI_DEBATE_FAILURE_THRESHOLD", 5, raising=False)

    class GarbageResult:
        ok = True
        text = "not-json-at-all"  # guaranteed parse failure
        error = None

    async def mock_ai_call(*args, **kwargs):
        return GarbageResult()

    import ai_model_router
    monkeypatch.setattr(ai_model_router, "ai_call", mock_ai_call)

    import logging
    caplog.set_level(logging.WARNING, logger="ai_advisor")

    result = await ai_advisor.run_bull_bear_debate("NVDA")

    # Both bull and bear runs failed to parse → 2 increments
    assert ai_advisor.get_debate_failure_count() == 2
    # Result is a degraded NEUTRAL
    assert result["degraded"] is True
    assert result["should_trade"] is False
    assert result["winner"] == "NEUTRAL"
    # Warning was logged with the identifiable prefix
    assert any("bull_bear_parse_failed" in rec.message for rec in caplog.records)


@pytest.mark.anyio
async def test_bull_bear_parse_failure_emits_metric_at_threshold(
    anyio_backend, monkeypatch
):
    """Once the counter crosses the threshold, a MetricEvent must be published."""
    import ai_advisor
    from events import MetricEvent

    monkeypatch.setattr(ai_advisor, "_debate_failure_count", 0, raising=False)
    monkeypatch.setattr(ai_advisor, "_debate_failure_day", None, raising=False)
    monkeypatch.setattr(ai_advisor, "_debate_threshold_emitted", False, raising=False)
    monkeypatch.setattr(config.cfg, "AI_DEBATE_FAILURE_THRESHOLD", 1, raising=False)

    published: list = []
    from bot_runner import event_bus
    monkeypatch.setattr(event_bus, "publish", lambda ev: published.append(ev))

    ai_advisor._record_debate_parse_failure("AAPL")

    assert ai_advisor.get_debate_failure_count() == 1
    assert any(isinstance(ev, MetricEvent) and ev.metric_type == "ai_debate_parse_failures" for ev in published)


def test_bot_health_surfaces_debate_failure_count(monkeypatch):
    """get_bot_health() must include ai_debate_parse_failures_24h."""
    import ai_advisor
    import bot_health

    monkeypatch.setattr(ai_advisor, "_debate_failure_count", 3, raising=False)
    monkeypatch.setattr(
        ai_advisor, "_debate_failure_day",
        __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%d"),
        raising=False,
    )

    health = bot_health.get_bot_health(is_running=False)
    assert "ai_debate_parse_failures_24h" in health
    assert health["ai_debate_parse_failures_24h"] == 3


# ── AI-5: Parameter persistence across restarts ────────────────────────────


@pytest.mark.anyio
async def test_ai_params_save_and_restore_round_trip(_isolated_db, anyio_backend):
    """AI-5 regression: parameters saved to DB must be restored on startup."""
    await init_db()
    from ai_params import AIParameterStore

    store = AIParameterStore()
    store.shadow_mode = False  # getters return stored values, not shadow defaults
    store.set_min_score(67.5)
    store.set_risk_multiplier(1.3)
    store.set_exit_params("AAPL", {"atr_stop_mult": 2.5, "atr_trail_mult": 1.8})
    store.set_signal_weights("BULL", {"rsi": 1.2, "sma": 0.8})
    store.set_rule_sizing_multiplier("rule-abc", 1.5)

    saved = await store.save_to_db()
    assert saved == 5  # min_score, risk_mult, signal_weights:BULL, exit_params:AAPL, rule_sizing:rule-abc

    # Simulate restart: create a fresh store and load from DB
    fresh = AIParameterStore()
    fresh.shadow_mode = False
    assert fresh._min_score is None  # defaults
    assert fresh._risk_multipliers == {}

    restored = await fresh.load_from_db()
    assert restored is True
    assert fresh.get_min_score() == 67.5
    assert fresh.get_risk_multiplier() == 1.3
    assert fresh.get_exit_params("AAPL") == {"atr_stop_mult": 2.5, "atr_trail_mult": 1.8}
    assert fresh.get_signal_weights("BULL") == {"rsi": 1.2, "sma": 0.8}
    assert fresh.get_rule_sizing_multiplier("rule-abc") == 1.5


@pytest.mark.anyio
async def test_ai_params_load_empty_db_returns_false(_isolated_db, anyio_backend):
    """load_from_db on a fresh DB must return False, not crash."""
    await init_db()
    from ai_params import AIParameterStore

    store = AIParameterStore()
    assert await store.load_from_db() is False
    # All defaults
    assert store.get_min_score() == 50.0
    assert store.get_risk_multiplier() == 1.0


@pytest.mark.anyio
async def test_ai_params_save_overwrites_latest(_isolated_db, anyio_backend):
    """Multiple save calls should result in the LATEST values being loaded."""
    await init_db()
    from ai_params import AIParameterStore

    # First save
    store = AIParameterStore()
    store.shadow_mode = False
    store.set_min_score(55.0)
    await store.save_to_db()

    # Second save with different value
    store.set_min_score(72.0)
    await store.save_to_db()

    # Load should get 72.0 (the latest)
    fresh = AIParameterStore()
    fresh.shadow_mode = False
    await fresh.load_from_db()
    assert fresh.get_min_score() == 72.0


@pytest.mark.anyio
async def test_ai_params_clamp_on_load(_isolated_db, anyio_backend):
    """Loaded values must be clamped to their valid ranges."""
    await init_db()
    from ai_params import AIParameterStore
    from db.core import get_db
    from datetime import datetime, timezone
    import json

    now = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        # Write out-of-range values directly
        await db.execute(
            "INSERT INTO ai_parameter_snapshots (timestamp, param_type, symbol, data, source) "
            "VALUES (?, ?, ?, ?, ?)",
            (now, "min_score", None, json.dumps({"value": 999.0}), "ai"),
        )
        await db.execute(
            "INSERT INTO ai_parameter_snapshots (timestamp, param_type, symbol, data, source) "
            "VALUES (?, ?, ?, ?, ?)",
            (now, "risk_multiplier", None, json.dumps({"global": 50.0}), "ai"),
        )
        await db.commit()

    store = AIParameterStore()
    store.shadow_mode = False
    await store.load_from_db()
    assert store.get_min_score() == 90.0  # clamped to max
    assert store.get_risk_multiplier() == 2.0  # clamped to max
