"""Autopilot mode semantics regression tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from ai_learning import check_auto_tighten
from ai_params import ai_params
from api_contracts import AutopilotModeRequest, GuardrailConfigResponse
from autopilot_api import _sync_mode_runtime, set_autopilot_mode
from config import cfg


@pytest.fixture(autouse=True)
def restore_runtime_flags():
    prev_mode = cfg.AUTOPILOT_MODE
    prev_enabled = cfg.AI_AUTONOMY_ENABLED
    prev_shadow_flag = cfg.AI_SHADOW_MODE
    prev_param_shadow = ai_params.shadow_mode
    try:
        yield
    finally:
        cfg.AUTOPILOT_MODE = prev_mode
        cfg.AI_AUTONOMY_ENABLED = prev_enabled
        cfg.AI_SHADOW_MODE = prev_shadow_flag
        ai_params.shadow_mode = prev_param_shadow


@pytest.mark.parametrize(
    ("mode", "autonomy_enabled", "cfg_shadow", "params_shadow"),
    [
        # cfg.AI_SHADOW_MODE: OFF semantics (only True when autopilot is OFF).
        # ai_params.shadow_mode: LIVE-only semantics (Batch 4 invariant — AI
        # mutates live risk params ONLY in LIVE; OFF and PAPER are shadow).
        ("OFF", False, True, True),
        ("PAPER", True, False, True),
        ("LIVE", True, False, False),
    ],
)
def test_sync_mode_runtime_keeps_mode_semantics_consistent(
    mode: str, autonomy_enabled: bool, cfg_shadow: bool, params_shadow: bool
):
    _sync_mode_runtime(mode)  # type: ignore[arg-type]

    assert cfg.AUTOPILOT_MODE == mode
    assert cfg.AI_AUTONOMY_ENABLED is autonomy_enabled
    assert cfg.AI_SHADOW_MODE is cfg_shadow
    assert ai_params.shadow_mode is params_shadow


@pytest.mark.anyio
async def test_live_mode_api_is_blocked_by_stage_9a_release_fence(anyio_backend):
    persist = AsyncMock()
    with patch.object(cfg, "IS_PAPER", False), patch.object(
        cfg, "SIM_MODE", False
    ), patch.object(
        cfg, "JWT_SECRET", "test-only-random-secret-at-least-32-bytes"
    ), patch.object(
        cfg, "JWT_BOOTSTRAP_SECRET", ""
    ), patch(
        "autopilot_api.update_autopilot_config",
        new=persist,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await set_autopilot_mode(
                AutopilotModeRequest(mode="LIVE", reason="must remain blocked")
            )

    assert exc_info.value.status_code == 400
    assert "Stage 9A release fence" in str(exc_info.value.detail)
    persist.assert_not_awaited()


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
    # Batch 4 authority invariant: AI gets LESS authority on recovery, not
    # more. Level 2 paper-revert MUST keep shadow_mode=True so the same AI
    # that just failed cannot continue to mutate risk multipliers / min_score.
    assert ai_params.shadow_mode is True
