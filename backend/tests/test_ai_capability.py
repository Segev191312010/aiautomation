"""AI capability-state validation for Phase A8."""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from config import DEFAULT_AI_FALLBACK_MODEL, DEFAULT_AI_PRIMARY_MODEL
from ai_capability import resolve_ai_capability, startup_ai_capability_errors_warnings


@dataclass
class CapabilityConfig:
    AUTOPILOT_MODE: str = "OFF"
    ANTHROPIC_API_KEY: str = ""
    AI_MODEL_OPTIMIZER: str = DEFAULT_AI_PRIMARY_MODEL
    AI_MODEL_NARRATIVE: str = DEFAULT_AI_PRIMARY_MODEL
    AI_MODEL_REGIME: str = DEFAULT_AI_PRIMARY_MODEL
    AI_MODEL_PORTFOLIO: str = DEFAULT_AI_PRIMARY_MODEL
    AI_MODEL_FALLBACK: str = DEFAULT_AI_FALLBACK_MODEL
    AI_FALLBACK_ENABLED: bool = True


def test_ai_capability_imports_config_defaults():
    import ai_capability

    assert hasattr(ai_capability, "DEFAULT_AI_PRIMARY_MODEL")
    assert hasattr(ai_capability, "DEFAULT_AI_FALLBACK_MODEL")


def test_ai_capability_package_imports_config_defaults(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))

    ai_capability = importlib.import_module("backend.ai_capability")

    assert hasattr(ai_capability, "DEFAULT_AI_PRIMARY_MODEL")
    assert hasattr(ai_capability, "DEFAULT_AI_FALLBACK_MODEL")


def test_disabled_mode_is_not_blocked_without_provider_key():
    capability = resolve_ai_capability(CapabilityConfig(AUTOPILOT_MODE="OFF"))

    assert capability.state == "disabled"
    assert capability.provider_configured is False
    assert capability.errors == ()


def test_paper_mode_without_provider_key_is_unconfigured():
    capability = resolve_ai_capability(CapabilityConfig(AUTOPILOT_MODE="PAPER"))

    assert capability.state == "unconfigured"
    assert any("ANTHROPIC_API_KEY" in error for error in capability.errors)


def test_configured_supported_models_are_ready():
    capability = resolve_ai_capability(
        CapabilityConfig(AUTOPILOT_MODE="PAPER", ANTHROPIC_API_KEY="test-key"),
        today=date(2026, 7, 9),
    )

    assert capability.state == "ready"
    assert capability.primary_model == DEFAULT_AI_PRIMARY_MODEL
    assert capability.fallback_model == DEFAULT_AI_FALLBACK_MODEL
    assert capability.errors == ()
    assert capability.warnings == ()


def test_unknown_model_is_invalid_when_ai_enabled():
    capability = resolve_ai_capability(
        CapabilityConfig(
            AUTOPILOT_MODE="PAPER",
            ANTHROPIC_API_KEY="test-key",
            AI_MODEL_OPTIMIZER="claude-made-up-model",
        )
    )

    assert capability.state == "invalid_model"
    assert any("unknown" in error for error in capability.errors)


def test_retired_model_is_invalid_when_ai_enabled():
    capability = resolve_ai_capability(
        CapabilityConfig(
            AUTOPILOT_MODE="PAPER",
            ANTHROPIC_API_KEY="test-key",
            AI_MODEL_OPTIMIZER="claude-sonnet-4-20250514",
        ),
        today=date(2026, 7, 9),
    )

    assert capability.state == "invalid_model"
    assert any("retired" in error for error in capability.errors)
    assert any(DEFAULT_AI_PRIMARY_MODEL in error for error in capability.errors)


def test_deprecated_model_warns_before_block_window():
    capability = resolve_ai_capability(
        CapabilityConfig(
            AUTOPILOT_MODE="PAPER",
            ANTHROPIC_API_KEY="test-key",
            AI_MODEL_OPTIMIZER="claude-opus-4-1-20250805",
        ),
        today=date(2026, 6, 20),
    )

    assert capability.state == "degraded"
    assert capability.errors == ()
    assert any("deprecated" in warning for warning in capability.warnings)


def test_deprecated_model_blocks_inside_retirement_window():
    capability = resolve_ai_capability(
        CapabilityConfig(
            AUTOPILOT_MODE="PAPER",
            ANTHROPIC_API_KEY="test-key",
            AI_MODEL_OPTIMIZER="claude-opus-4-1-20250805",
        ),
        today=date(2026, 7, 10),
    )

    assert capability.state == "invalid_model"
    assert any("within 30 days" in error for error in capability.errors)


def test_disabled_mode_surfaces_invalid_model_as_warning_only():
    errors, warnings, capability = startup_ai_capability_errors_warnings(
        CapabilityConfig(
            AUTOPILOT_MODE="OFF",
            AI_MODEL_OPTIMIZER="claude-sonnet-4-20250514",
        )
    )

    assert capability.state == "disabled"
    assert errors == []
    assert any("retired" in warning for warning in warnings)


def test_fallback_disabled_is_degraded_not_invalid():
    capability = resolve_ai_capability(
        CapabilityConfig(
            AUTOPILOT_MODE="PAPER",
            ANTHROPIC_API_KEY="test-key",
            AI_FALLBACK_ENABLED=False,
        )
    )

    assert capability.state == "degraded"
    assert any("AI_FALLBACK_ENABLED=false" in warning for warning in capability.warnings)


def test_circuit_breaker_tripped_is_degraded_not_invalid():
    capability = resolve_ai_capability(
        CapabilityConfig(AUTOPILOT_MODE="PAPER", ANTHROPIC_API_KEY="test-key"),
        failure_status={"breaker_tripped": True},
    )

    assert capability.state == "degraded"
    assert any("circuit breaker" in warning for warning in capability.warnings)
