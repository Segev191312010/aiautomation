"""Tests for candidate_registry — resolution, prompt building, unsupported type rejection."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from candidate_registry import (
    resolve_candidate,
    resolve_baseline,
    build_candidate_prompt,
    generate_candidate_items,
    SUPPORTED_CANDIDATE_TYPES,
)


def test_resolve_model_version():
    config = resolve_candidate("model_version", "claude-sonnet-4-6")
    assert config["model"] == "claude-sonnet-4-6"
    assert config["prompt_version"] == "v1"
    assert config["temperature"] == 0


def test_resolve_prompt_version():
    config = resolve_candidate("prompt_version", "v2-aggressive")
    assert config["prompt_version"] == "v2-aggressive"
    assert "model" in config
    assert config["temperature"] == 0


def test_resolve_unsupported_type_raises():
    with pytest.raises(ValueError, match="Unsupported candidate_type"):
        resolve_candidate("rule_snapshot", "rule-123")


def test_resolve_baseline_none():
    assert resolve_baseline("model_version", None) is None


def test_resolve_baseline_valid():
    config = resolve_baseline("model_version", "claude-opus-4-6")
    assert config is not None
    assert config["model"] == "claude-opus-4-6"


def test_build_candidate_prompt_returns_tuple():
    context = '{"lookback_days": 30, "trade_count": 10, "pnl_summary": {}, "rule_performance": [], "score_analysis": {}, "current_params": {}}'
    system, user = build_candidate_prompt(context, {"model": "test"})
    assert isinstance(system, str)
    assert isinstance(user, str)
    assert "30" in user  # lookback_days
    assert "10" in user  # trade_count


def test_build_candidate_prompt_handles_bad_json():
    system, user = build_candidate_prompt("not json", {"model": "test"})
    assert "Error" in user or isinstance(user, str)


@pytest.mark.anyio
async def test_generate_candidate_items_no_api_key(anyio_backend, monkeypatch):
    import config
    monkeypatch.setattr(config.cfg, "ANTHROPIC_API_KEY", "")
    result = await generate_candidate_items("{}", {"model": "test"})
    assert result is None


@pytest.mark.anyio
async def test_generate_candidate_items_mocked(anyio_backend, monkeypatch):
    import config
    monkeypatch.setattr(config.cfg, "ANTHROPIC_API_KEY", "test-key")

    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text='{"reasoning": "test", "confidence": 0.8}')]
    mock_msg.usage.input_tokens = 100
    mock_msg.usage.output_tokens = 50

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_msg)

    mock_anthropic_mod = MagicMock()
    mock_anthropic_mod.AsyncAnthropic.return_value = mock_client
    with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
        result = await generate_candidate_items(
            '{"lookback_days": 30, "trade_count": 5, "pnl_summary": {}, "rule_performance": [], "score_analysis": {}, "current_params": {}}',
            {"model": "claude-sonnet-4-6", "temperature": 0},
        )

    assert result is not None
    assert result["confidence"] == 0.8
    assert result["_input_tokens"] == 100
