"""Tests for rule_replay_adapter — replayability guard and backtest request builder."""
from models import Rule
from rule_replay_adapter import (
    is_rule_replayable,
    build_backtest_request_from_rule,
    build_default_replay_config,
)


def _make_rule(**overrides) -> Rule:
    defaults = dict(
        name="Test Rule",
        symbol="AAPL",
        conditions=[{"indicator": "RSI", "params": {"length": 14}, "operator": "<", "value": 30}],
        logic="AND",
        action={"type": "BUY", "asset_type": "STK", "quantity": 10, "order_type": "MKT"},
    )
    defaults.update(overrides)
    return Rule(**defaults)


def test_replayable_rule_with_config():
    rule = _make_rule(replay_config={"stop_loss_pct": 3.0, "take_profit_pct": 6.6})
    ok, reason = is_rule_replayable(rule)
    assert ok is True
    assert "replay_config" in reason


def test_non_replayable_rule_without_config():
    rule = _make_rule(replay_config=None)
    ok, reason = is_rule_replayable(rule)
    assert ok is False
    assert "No explicit replay metadata" in reason


def test_non_replayable_rule_empty_config():
    rule = _make_rule(replay_config={})
    ok, reason = is_rule_replayable(rule)
    assert ok is False


def test_non_replayable_no_conditions():
    rule = _make_rule(conditions=[], replay_config={"stop_loss_pct": 3.0})
    ok, reason = is_rule_replayable(rule)
    assert ok is False
    assert "no entry conditions" in reason


def test_build_backtest_request():
    rule = _make_rule(replay_config={
        "stop_loss_pct": 3.0,
        "take_profit_pct": 6.6,
        "exit_conditions": [],
        "position_size_pct": 15.0,
    })
    req = build_backtest_request_from_rule(rule)
    assert req["stop_loss_pct"] == 3.0
    assert req["take_profit_pct"] == 6.6
    assert req["position_size_pct"] == 15.0
    assert req["symbol"] == "AAPL"
    assert req["condition_logic"] == "AND"
    assert len(req["entry_conditions"]) == 1


def test_build_default_replay_config():
    config = build_default_replay_config()
    assert "stop_loss_pct" in config
    assert "take_profit_pct" in config
    assert config["stop_loss_pct"] > 0
    assert config["take_profit_pct"] > 0
    assert config["source"] == "atr_config_snapshot"
