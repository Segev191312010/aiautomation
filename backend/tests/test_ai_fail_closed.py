from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest


def _rule(symbol: str):
    from models import Rule

    return Rule(
        name=f"{symbol} rule",
        symbol=symbol,
        enabled=True,
        conditions=[],
        logic="AND",
        action={"type": "BUY", "asset_type": "STK", "quantity": 1, "order_type": "MKT"},
        cooldown_minutes=15,
    )


def test_signal_scorer_failure_skips_only_affected_candidate_and_records_evidence(monkeypatch):
    import bot_runner
    from signal_scorer import signal_scorer

    bad_rule = _rule("BAD")
    good_rule = _rule("GOOD")
    degraded = Mock()
    bot_error = Mock()
    published = []

    def score(symbol, _bars, _action):
        if symbol == "BAD":
            raise ValueError("indicator unavailable")
        return {"symbol": symbol, "composite_score": 82.0}

    monkeypatch.setattr(signal_scorer, "set_ai_weights", Mock())
    monkeypatch.setattr(signal_scorer, "score_signal", Mock(side_effect=score))
    monkeypatch.setattr(
        signal_scorer,
        "rank_signals",
        Mock(side_effect=lambda rows, **_kwargs: rows),
    )
    monkeypatch.setattr(bot_runner, "record_degraded_event", degraded)
    monkeypatch.setattr(bot_runner, "record_bot_error", bot_error)
    monkeypatch.setattr(bot_runner.event_bus, "publish", published.append)
    monkeypatch.setattr(bot_runner.event_logger, "log_event", Mock())
    metric_record = Mock()
    monkeypatch.setattr(bot_runner.metrics, "record", metric_record)

    candidates = bot_runner._score_rule_candidates(
        [(bad_rule, "BAD"), (good_rule, "GOOD")],
        {"BAD": object(), "GOOD": object()},
        datetime.now(timezone.utc),
    )

    assert [candidate["symbol"] for candidate in candidates] == ["GOOD"]
    assert candidates[0]["score"] == 82.0
    assert all(candidate["symbol"] != "BAD" for candidate in candidates)
    degraded.assert_called_once()
    assert "BAD" in bot_error.call_args.args[0]
    metric_record.assert_any_call("signal_scoring_error", 1.0)
    assert any(
        getattr(event, "metric_type", "") == "signal_scoring_error"
        and event.symbol == "BAD"
        and event.rule_id == bad_rule.id
        for event in published
    )


def test_signal_ranker_failure_skips_entire_batch_without_neutral_candidates(monkeypatch):
    import bot_runner
    from signal_scorer import signal_scorer

    rule = _rule("AAPL")
    monkeypatch.setattr(signal_scorer, "set_ai_weights", Mock())
    monkeypatch.setattr(
        signal_scorer,
        "score_signal",
        Mock(return_value={"symbol": "AAPL", "composite_score": 90.0}),
    )
    monkeypatch.setattr(
        signal_scorer,
        "rank_signals",
        Mock(side_effect=RuntimeError("ranking unavailable")),
    )
    monkeypatch.setattr(bot_runner, "record_degraded_event", Mock())
    monkeypatch.setattr(bot_runner, "record_bot_error", Mock())
    monkeypatch.setattr(bot_runner.event_bus, "publish", Mock())
    monkeypatch.setattr(bot_runner.metrics, "record", Mock())

    candidates = bot_runner._score_rule_candidates(
        [(rule, "AAPL")],
        {"AAPL": object()},
        datetime.now(timezone.utc),
    )

    assert candidates == []
    bot_runner.record_degraded_event.assert_called_once()


async def _run_optimizer(monkeypatch, *, start_result="run-1", item_result=None):
    import ai_optimizer

    decisions = {
        "confidence": 0.8,
        "reasoning": "test",
        "min_score": {"value": 60, "reason": "quality"},
    }
    apply_decisions = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(ai_optimizer, "_optimizer_running", False)
    monkeypatch.setattr(
        ai_optimizer,
        "get_autopilot_config_dict",
        AsyncMock(return_value={"emergency_stop": False}),
    )
    monkeypatch.setattr(ai_optimizer, "_build_context", AsyncMock(return_value={"trade_count": 1}))
    monkeypatch.setattr(ai_optimizer, "_get_ai_decisions", AsyncMock(return_value=decisions))
    monkeypatch.setattr(ai_optimizer, "start_decision_run", AsyncMock(return_value=start_result))
    monkeypatch.setattr(
        ai_optimizer,
        "record_decision_items",
        AsyncMock(return_value=item_result if item_result is not None else ["item-1"]),
    )
    monkeypatch.setattr(ai_optimizer, "finalize_decision_run", finalize)
    monkeypatch.setattr(ai_optimizer, "_apply_decisions", apply_decisions)
    result = await ai_optimizer.run_full_optimization()
    return result, apply_decisions, finalize


@pytest.mark.anyio
async def test_optimizer_blocks_application_when_run_persistence_returns_no_id(monkeypatch, anyio_backend):
    result, apply_decisions, finalize = await _run_optimizer(
        monkeypatch,
        start_result=None,
    )

    assert result["success"] is False
    assert result["error"] == "decision_ledger_unavailable"
    apply_decisions.assert_not_awaited()
    finalize.assert_not_awaited()


@pytest.mark.anyio
async def test_optimizer_blocks_application_and_marks_run_error_on_partial_item_batch(
    monkeypatch,
    anyio_backend,
):
    result, apply_decisions, finalize = await _run_optimizer(
        monkeypatch,
        item_result=[],
    )

    assert result["success"] is False
    assert result["error"] == "decision_ledger_unavailable"
    assert "incomplete" in result["detail"]
    apply_decisions.assert_not_awaited()
    finalize.assert_awaited_once()
    assert finalize.call_args.kwargs["status"] == "error"
    assert "ledger_persistence_failed" in finalize.call_args.kwargs["error"]
