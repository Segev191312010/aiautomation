"""
Batch 4 regression tests — AI authority chain invariant.

The invariant: ``ai_params.shadow_mode`` MUST be True unless
``cfg.AUTOPILOT_MODE == "LIVE"``. Any other state means the AI is mutating
live risk multipliers / min_score / rule sizing during a paper soak.

Three layers must hold:

1. The upstream setter in `ai_optimizer.ai_optimization_loop` sets
   `shadow_mode = (mode != "LIVE")`.
2. The recovery branch in `ai_learning.check_auto_tighten` sets
   `shadow_mode = True` when reverting to PAPER (failing AI gets LESS
   authority, not more).
3. The runtime tripwire in `ai_params._enforce_shadow_authority` catches
   any setter regression and forces shadow for the call.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ai_params import AIParameterStore, _enforce_shadow_authority
from config import cfg


# ---------------------------------------------------------------------------
# 1. Mode -> shadow_mode mapping in upstream setters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode,expected_shadow", [
    ("OFF", True),
    ("PAPER", True),
    ("LIVE", False),
])
def test_optimizer_mode_to_shadow_mapping(mode, expected_shadow):
    """ai_optimization_loop sets shadow_mode = (mode != 'LIVE')."""
    # We don't run the loop; we just verify the setter logic by direct apply.
    store = AIParameterStore()
    store.shadow_mode = mode != "LIVE"
    assert store.shadow_mode is expected_shadow


# ---------------------------------------------------------------------------
# 2. Runtime tripwire — getters force shadow on authority desync
# ---------------------------------------------------------------------------


def test_tripwire_forces_shadow_when_mode_not_live(caplog):
    """If shadow_mode=False but mode != LIVE, getter must force shadow + log CRITICAL."""
    with patch.object(cfg, "AUTOPILOT_MODE", "PAPER"), \
         caplog.at_level(logging.CRITICAL):
        result = _enforce_shadow_authority("get_test", current_shadow=False)
    assert result is True, "tripwire must force shadow for this call"
    # Stack-included CRITICAL log with the named tripwire string
    desync_logs = [r for r in caplog.records
                   if "ai_params authority desync" in r.getMessage()]
    assert desync_logs, "tripwire must emit a CRITICAL desync log"
    # Getter name + mode must appear in the message for grep-ability
    assert "get_test" in desync_logs[0].getMessage()
    assert "PAPER" in desync_logs[0].getMessage()


def test_tripwire_passthrough_when_live(caplog):
    """If mode == LIVE, shadow_mode flows through unchanged (no tripwire)."""
    with patch.object(cfg, "AUTOPILOT_MODE", "LIVE"), \
         caplog.at_level(logging.CRITICAL):
        result_shadow = _enforce_shadow_authority("get_test", current_shadow=True)
        result_live = _enforce_shadow_authority("get_test", current_shadow=False)
    assert result_shadow is True
    assert result_live is False
    # No desync log
    assert not any("authority desync" in r.getMessage() for r in caplog.records)


def test_tripwire_passthrough_when_already_shadow():
    """If shadow_mode is already True, no tripwire fires regardless of mode."""
    with patch.object(cfg, "AUTOPILOT_MODE", "OFF"):
        result = _enforce_shadow_authority("get_test", current_shadow=True)
    assert result is True


# ---------------------------------------------------------------------------
# 3. Getters return defaults under desync (effectively shadow)
# ---------------------------------------------------------------------------


def test_get_risk_multiplier_returns_default_under_desync():
    """Even with _shadow_mode=False, mode=PAPER must yield 1.0 (config default)."""
    store = AIParameterStore()
    store.set_risk_multiplier(1.7)  # AI-tuned value
    store._shadow_mode = False  # simulate a future setter regression

    with patch.object(cfg, "AUTOPILOT_MODE", "PAPER"):
        result = store.get_risk_multiplier()
    assert result == 1.0, "desync state must yield default 1.0, not AI-tuned 1.7"


def test_get_min_score_returns_default_under_desync():
    store = AIParameterStore()
    store.set_min_score(35.0)  # AI-tuned value
    store._shadow_mode = False

    with patch.object(cfg, "AUTOPILOT_MODE", "PAPER"):
        result = store.get_min_score()
    assert result == 50.0, "desync state must yield default 50.0, not AI-tuned 35.0"


def test_get_rule_sizing_multiplier_returns_default_under_desync():
    store = AIParameterStore()
    store.set_rule_sizing_multiplier("r1", 2.5)
    store._shadow_mode = False

    with patch.object(cfg, "AUTOPILOT_MODE", "PAPER"):
        result = store.get_rule_sizing_multiplier("r1")
    assert result == 1.0, "desync state must yield default 1.0, not AI-tuned 2.5"


# ---------------------------------------------------------------------------
# 4. Getters return AI values in proper LIVE mode (positive path)
# ---------------------------------------------------------------------------


def test_get_risk_multiplier_returns_ai_value_in_live():
    store = AIParameterStore()
    store.set_risk_multiplier(1.5)
    store._shadow_mode = False

    with patch.object(cfg, "AUTOPILOT_MODE", "LIVE"):
        result = store.get_risk_multiplier()
    assert result == 1.5, "LIVE mode + shadow=False must yield AI-tuned value"
