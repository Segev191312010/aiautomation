"""Autopilot mode semantics regression tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from ai_learning import check_auto_tighten
from ai_params import ai_params
from api_contracts import AutopilotModeRequest, GuardrailConfigResponse
from autopilot_api import _sync_mode_runtime, set_autopilot_mode
from config import DEFAULT_AI_FALLBACK_MODEL, DEFAULT_AI_PRIMARY_MODEL
from config import cfg


@pytest.fixture(autouse=True)
def restore_runtime_flags():
    prev_mode = cfg.AUTOPILOT_MODE
    prev_enabled = cfg.AI_AUTONOMY_ENABLED
    prev_shadow_flag = cfg.AI_SHADOW_MODE
    prev_param_shadow = ai_params.shadow_mode
    prev_jwt_secret = cfg.JWT_SECRET
    prev_bootstrap_secret = getattr(cfg, "JWT_BOOTSTRAP_SECRET", "")
    prev_is_paper = cfg.IS_PAPER
    prev_sim_mode = cfg.SIM_MODE
    prev_anthropic_key = cfg.ANTHROPIC_API_KEY
    prev_optimizer = cfg.AI_MODEL_OPTIMIZER
    prev_narrative = cfg.AI_MODEL_NARRATIVE
    prev_regime = cfg.AI_MODEL_REGIME
    prev_portfolio = cfg.AI_MODEL_PORTFOLIO
    prev_fallback = cfg.AI_MODEL_FALLBACK
    prev_fallback_enabled = cfg.AI_FALLBACK_ENABLED
    try:
        yield
    finally:
        cfg.AUTOPILOT_MODE = prev_mode
        cfg.AI_AUTONOMY_ENABLED = prev_enabled
        cfg.AI_SHADOW_MODE = prev_shadow_flag
        ai_params.shadow_mode = prev_param_shadow
        cfg.JWT_SECRET = prev_jwt_secret
        cfg.JWT_BOOTSTRAP_SECRET = prev_bootstrap_secret
        cfg.IS_PAPER = prev_is_paper
        cfg.SIM_MODE = prev_sim_mode
        cfg.ANTHROPIC_API_KEY = prev_anthropic_key
        cfg.AI_MODEL_OPTIMIZER = prev_optimizer
        cfg.AI_MODEL_NARRATIVE = prev_narrative
        cfg.AI_MODEL_REGIME = prev_regime
        cfg.AI_MODEL_PORTFOLIO = prev_portfolio
        cfg.AI_MODEL_FALLBACK = prev_fallback
        cfg.AI_FALLBACK_ENABLED = prev_fallback_enabled


@pytest.mark.parametrize(
    ("mode", "autonomy_enabled", "shadow_mode"),
    [
        ("OFF", False, True),
        ("PAPER", True, False),
        ("LIVE", True, False),
    ],
)
def test_sync_mode_runtime_keeps_mode_semantics_consistent(mode: str, autonomy_enabled: bool, shadow_mode: bool):
    _sync_mode_runtime(mode)  # type: ignore[arg-type]

    assert cfg.AUTOPILOT_MODE == mode
    assert cfg.AI_AUTONOMY_ENABLED is autonomy_enabled
    assert cfg.AI_SHADOW_MODE is shadow_mode
    assert ai_params.shadow_mode is shadow_mode


@pytest.mark.anyio
async def test_set_autopilot_mode_blocks_unconfigured_ai(anyio_backend):
    cfg.JWT_SECRET = "strong-random-secret"
    cfg.JWT_BOOTSTRAP_SECRET = ""
    cfg.IS_PAPER = True
    cfg.SIM_MODE = False
    cfg.ANTHROPIC_API_KEY = ""

    with pytest.raises(HTTPException) as exc:
        await set_autopilot_mode(AutopilotModeRequest(mode="PAPER", reason="test"))

    assert exc.value.status_code == 400
    assert exc.value.detail["ai_capability"] == "unconfigured"
    assert any("ANTHROPIC_API_KEY" in e for e in exc.value.detail["errors"])


@pytest.mark.anyio
async def test_set_autopilot_mode_allows_degraded_ai(anyio_backend):
    cfg.JWT_SECRET = "strong-random-secret"
    cfg.JWT_BOOTSTRAP_SECRET = ""
    cfg.IS_PAPER = True
    cfg.SIM_MODE = False
    cfg.ANTHROPIC_API_KEY = "test-key"
    cfg.AI_MODEL_OPTIMIZER = DEFAULT_AI_PRIMARY_MODEL
    cfg.AI_MODEL_NARRATIVE = DEFAULT_AI_PRIMARY_MODEL
    cfg.AI_MODEL_REGIME = DEFAULT_AI_PRIMARY_MODEL
    cfg.AI_MODEL_PORTFOLIO = DEFAULT_AI_PRIMARY_MODEL
    cfg.AI_MODEL_FALLBACK = DEFAULT_AI_FALLBACK_MODEL
    cfg.AI_FALLBACK_ENABLED = False

    with patch(
        "autopilot_api.update_autopilot_config",
        new=AsyncMock(return_value=GuardrailConfigResponse(autopilot_mode="PAPER")),
    ), patch("autopilot_api.log_ai_action", new=AsyncMock()):
        result = await set_autopilot_mode(AutopilotModeRequest(mode="PAPER", reason="test"))

    assert result["autopilot_mode"] == "PAPER"
    assert cfg.AUTOPILOT_MODE == "PAPER"


@pytest.mark.anyio
async def test_ai_status_surfaces_capability_fields(anyio_backend):
    cfg.AUTOPILOT_MODE = "PAPER"
    cfg.ANTHROPIC_API_KEY = ""

    with patch(
        "ai_guardrails._load_guardrails_from_db",
        new=AsyncMock(return_value=GuardrailConfigResponse(autopilot_mode="PAPER")),
    ), patch(
        "ai_guardrails.GuardrailEnforcer._count_today_changes",
        new=AsyncMock(return_value=0),
    ), patch(
        "ai_guardrails.GuardrailEnforcer._last_change_at",
        new=AsyncMock(return_value=None),
    ):
        from ai_guardrails import get_ai_status_dict

        status = await get_ai_status_dict()

    assert status["ai_capability"] == "unconfigured"
    assert status["ai_provider"] == "anthropic"
    assert status["ai_provider_configured"] is False
    assert any("ANTHROPIC_API_KEY" in error for error in status["ai_capability_errors"])


@pytest.mark.anyio
async def test_auto_tighten_level2_reverts_to_paper_mode(anyio_backend):
    config = GuardrailConfigResponse(
        autopilot_mode="LIVE",
        auto_tighten_enabled=True,
        guardrails_currently_tightened=True,
        auto_tighten_bad_hit_rate_7d=0.45,
        auto_tighten_min_decisions_7d=40,
        auto_tighten_bad_hit_rate_30d=0.50,
        auto_tighten_min_decisions_30d=100,
    )
    metrics_7d = {
        "hit_rate": 0.60,
        "scored_decisions": 50,
    }
    metrics_30d = {
        "hit_rate": 0.40,
        "scored_decisions": 120,
    }

    with patch("ai_learning._load_guardrails_from_db", new=AsyncMock(return_value=config)), patch(
        "ai_learning.evaluate_past_decisions",
        new=AsyncMock(side_effect=[metrics_7d, metrics_30d]),
    ), patch("ai_learning.save_guardrails_to_db", new=AsyncMock()) as mock_save, patch(
        "ai_learning.log_ai_action",
        new=AsyncMock(),
    ):
        result = await check_auto_tighten()

    saved_config = mock_save.await_args.args[0]
    assert result["actions_taken"] == ["level2_paper_revert"]
    assert saved_config.autopilot_mode == "PAPER"
    assert ai_params.shadow_mode is False  # PAPER = AI still active (creates paper rules), no live orders
