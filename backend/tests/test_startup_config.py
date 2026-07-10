"""Startup and config validation regressions."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ai_params import ai_params
from api_contracts import GuardrailConfigResponse
from config import DEFAULT_AI_FALLBACK_MODEL, DEFAULT_AI_PRIMARY_MODEL, cfg, _validate_config
from startup import DEFAULT_DEV_JWT_SECRET, validate_autopilot_matrix, validate_startup


@pytest.fixture
def restore_cfg():
    previous = {
        "DB_PATH": cfg.DB_PATH,
        "JWT_SECRET": cfg.JWT_SECRET,
        "STRICT_CONFIG": cfg.STRICT_CONFIG,
        "AUTOPILOT_MODE": cfg.AUTOPILOT_MODE,
        "AI_AUTONOMY_ENABLED": cfg.AI_AUTONOMY_ENABLED,
        "AI_SHADOW_MODE": cfg.AI_SHADOW_MODE,
        "ANTHROPIC_API_KEY": cfg.ANTHROPIC_API_KEY,
        "AI_MODEL_OPTIMIZER": cfg.AI_MODEL_OPTIMIZER,
        "AI_MODEL_NARRATIVE": cfg.AI_MODEL_NARRATIVE,
        "AI_MODEL_REGIME": cfg.AI_MODEL_REGIME,
        "AI_MODEL_PORTFOLIO": cfg.AI_MODEL_PORTFOLIO,
        "AI_MODEL_FALLBACK": cfg.AI_MODEL_FALLBACK,
        "AI_FALLBACK_ENABLED": cfg.AI_FALLBACK_ENABLED,
        "IS_PAPER": cfg.IS_PAPER,
        "IBKR_PORT": cfg.IBKR_PORT,
        "JWT_BOOTSTRAP_SECRET": getattr(cfg, "JWT_BOOTSTRAP_SECRET", ""),
        "SIM_MODE": cfg.SIM_MODE,
    }
    previous_param_shadow_mode = ai_params.shadow_mode
    try:
        yield
    finally:
        for key, value in previous.items():
            setattr(cfg, key, value)
        ai_params.shadow_mode = previous_param_shadow_mode


def test_validate_config_rejects_unknown_autopilot_mode(restore_cfg):
    cfg.AUTOPILOT_MODE = "PAPRE"

    with pytest.raises(ValueError, match="AUTOPILOT_MODE='PAPRE' is invalid"):
        _validate_config(cfg)


def test_ai_model_defaults_are_current_and_centralized(restore_cfg):
    assert cfg.AI_MODEL_OPTIMIZER == DEFAULT_AI_PRIMARY_MODEL
    assert cfg.AI_MODEL_NARRATIVE == DEFAULT_AI_PRIMARY_MODEL
    assert cfg.AI_MODEL_REGIME == DEFAULT_AI_PRIMARY_MODEL
    assert cfg.AI_MODEL_PORTFOLIO == DEFAULT_AI_PRIMARY_MODEL
    assert cfg.AI_MODEL_FALLBACK == DEFAULT_AI_FALLBACK_MODEL
    assert "20250514" not in "|".join(
        [
            cfg.AI_MODEL_OPTIMIZER,
            cfg.AI_MODEL_NARRATIVE,
            cfg.AI_MODEL_REGIME,
            cfg.AI_MODEL_PORTFOLIO,
            cfg.AI_MODEL_FALLBACK,
        ]
    )


@pytest.mark.anyio
async def test_validate_startup_warns_on_default_jwt_secret_off_mode(restore_cfg, anyio_backend):
    """With AUTOPILOT_MODE=OFF, default JWT_SECRET is a warning (not error)."""
    cfg.DB_PATH = ":memory:"
    cfg.JWT_SECRET = DEFAULT_DEV_JWT_SECRET
    cfg.STRICT_CONFIG = False
    cfg.AUTOPILOT_MODE = "OFF"
    cfg.IS_PAPER = True
    cfg.IBKR_PORT = 7497
    cfg.SIM_MODE = False

    result = await validate_startup()

    assert any("JWT_SECRET is the default development value" in w for w in result["warnings"])
    assert not result["errors"]


@pytest.mark.anyio
async def test_validate_startup_errors_on_default_jwt_secret_paper_mode(restore_cfg, anyio_backend):
    """C6 safety fix: PAPER or LIVE mode with default JWT_SECRET is an error."""
    cfg.DB_PATH = ":memory:"
    cfg.JWT_SECRET = DEFAULT_DEV_JWT_SECRET
    cfg.STRICT_CONFIG = False
    cfg.AUTOPILOT_MODE = "PAPER"
    cfg.IS_PAPER = True
    cfg.IBKR_PORT = 7497
    cfg.SIM_MODE = False

    result = await validate_startup()

    assert any("non-default JWT_SECRET" in e for e in result["errors"])


# ── Autopilot matrix validator ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_validate_startup_errors_on_missing_ai_key_paper_mode(restore_cfg, anyio_backend):
    cfg.DB_PATH = ":memory:"
    cfg.JWT_SECRET = "strong-random-secret"
    cfg.STRICT_CONFIG = False
    cfg.AUTOPILOT_MODE = "PAPER"
    cfg.ANTHROPIC_API_KEY = ""
    cfg.IS_PAPER = True
    cfg.IBKR_PORT = 7497
    cfg.SIM_MODE = False

    result = await validate_startup()

    assert any("ANTHROPIC_API_KEY" in e for e in result["errors"])


@pytest.mark.anyio
async def test_validate_startup_errors_on_retired_ai_model_paper_mode(restore_cfg, anyio_backend):
    cfg.DB_PATH = ":memory:"
    cfg.JWT_SECRET = "strong-random-secret"
    cfg.STRICT_CONFIG = False
    cfg.AUTOPILOT_MODE = "PAPER"
    cfg.ANTHROPIC_API_KEY = "test-key"
    cfg.AI_MODEL_OPTIMIZER = "claude-sonnet-4-20250514"
    cfg.IS_PAPER = True
    cfg.IBKR_PORT = 7497
    cfg.SIM_MODE = False

    result = await validate_startup()

    assert any("retired" in e for e in result["errors"])


@pytest.mark.anyio
async def test_validate_startup_warns_on_retired_ai_model_when_off(restore_cfg, anyio_backend):
    cfg.DB_PATH = ":memory:"
    cfg.JWT_SECRET = DEFAULT_DEV_JWT_SECRET
    cfg.STRICT_CONFIG = False
    cfg.AUTOPILOT_MODE = "OFF"
    cfg.ANTHROPIC_API_KEY = ""
    cfg.AI_MODEL_OPTIMIZER = "claude-sonnet-4-20250514"
    cfg.IS_PAPER = True
    cfg.IBKR_PORT = 7497
    cfg.SIM_MODE = False

    result = await validate_startup()

    assert result["errors"] == []
    assert any("retired" in w for w in result["warnings"])


@pytest.mark.anyio
async def test_persisted_paper_mode_without_ai_key_is_forced_off(
    restore_cfg,
    anyio_backend,
):
    import main

    cfg.AUTOPILOT_MODE = "OFF"
    cfg.JWT_SECRET = "strong-random-secret"
    cfg.JWT_BOOTSTRAP_SECRET = ""
    cfg.IS_PAPER = True
    cfg.SIM_MODE = False
    cfg.ANTHROPIC_API_KEY = ""

    with patch(
        "ai_guardrails._load_guardrails_from_db",
        new=AsyncMock(return_value=GuardrailConfigResponse(autopilot_mode="PAPER")),
    ):
        applied = await main._sync_persisted_autopilot_mode()

    assert applied is False
    assert cfg.AUTOPILOT_MODE == "OFF"
    assert cfg.AI_AUTONOMY_ENABLED is False
    assert cfg.AI_SHADOW_MODE is True
    assert ai_params.shadow_mode is True


@pytest.mark.anyio
async def test_persisted_mode_load_failure_is_forced_off(
    restore_cfg,
    anyio_backend,
):
    import main

    class BrokenDatabaseContext:
        async def __aenter__(self):
            raise OSError("database unavailable")

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    cfg.AUTOPILOT_MODE = "PAPER"
    cfg.AI_AUTONOMY_ENABLED = True
    cfg.AI_SHADOW_MODE = False
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
    ai_params.shadow_mode = False

    with patch("ai_guardrails.get_db", return_value=BrokenDatabaseContext()):
        applied = await main._sync_persisted_autopilot_mode()

    assert applied is False
    assert cfg.AUTOPILOT_MODE == "OFF"
    assert cfg.AI_AUTONOMY_ENABLED is False
    assert cfg.AI_SHADOW_MODE is True
    assert ai_params.shadow_mode is True


@pytest.mark.anyio
async def test_persisted_paper_mode_with_retired_model_is_forced_off(
    restore_cfg,
    anyio_backend,
):
    import main

    cfg.AUTOPILOT_MODE = "OFF"
    cfg.JWT_SECRET = "strong-random-secret"
    cfg.JWT_BOOTSTRAP_SECRET = ""
    cfg.IS_PAPER = True
    cfg.SIM_MODE = False
    cfg.ANTHROPIC_API_KEY = "test-key"
    cfg.AI_MODEL_OPTIMIZER = "claude-sonnet-4-20250514"

    with patch(
        "ai_guardrails._load_guardrails_from_db",
        new=AsyncMock(return_value=GuardrailConfigResponse(autopilot_mode="PAPER")),
    ):
        applied = await main._sync_persisted_autopilot_mode()

    assert applied is False
    assert cfg.AUTOPILOT_MODE == "OFF"
    assert cfg.AI_AUTONOMY_ENABLED is False


@pytest.mark.anyio
async def test_persisted_safe_paper_mode_is_applied_before_side_effects(
    restore_cfg,
    anyio_backend,
):
    import main

    cfg.AUTOPILOT_MODE = "OFF"
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

    with patch(
        "ai_guardrails._load_guardrails_from_db",
        new=AsyncMock(return_value=GuardrailConfigResponse(autopilot_mode="PAPER")),
    ):
        applied = await main._sync_persisted_autopilot_mode()

    assert applied is True
    assert cfg.AUTOPILOT_MODE == "PAPER"
    assert cfg.AI_AUTONOMY_ENABLED is True
    assert cfg.AI_SHADOW_MODE is False
    assert ai_params.shadow_mode is False


@pytest.mark.anyio
async def test_lifespan_rejects_persisted_mode_before_runtime_services(
    restore_cfg,
    anyio_backend,
):
    import main

    class RuntimeServiceStarted(RuntimeError):
        pass

    cfg.AUTOPILOT_MODE = "OFF"
    cfg.JWT_SECRET = "strong-random-secret"
    cfg.JWT_BOOTSTRAP_SECRET = ""
    cfg.IS_PAPER = True
    cfg.SIM_MODE = False
    cfg.ANTHROPIC_API_KEY = ""

    async def assert_forced_off_before_simulation() -> None:
        assert cfg.AUTOPILOT_MODE == "OFF"
        assert cfg.AI_AUTONOMY_ENABLED is False
        assert cfg.AI_SHADOW_MODE is True
        raise RuntimeServiceStarted("simulation startup boundary reached")

    with patch("startup.validate_startup", new=AsyncMock()), patch(
        "main.init_db",
        new=AsyncMock(),
    ), patch(
        "ai_guardrails._load_guardrails_from_db",
        new=AsyncMock(return_value=GuardrailConfigResponse(autopilot_mode="PAPER")),
    ), patch(
        "db.direct_candidates.purge_expired_candidates",
        new=AsyncMock(return_value=0),
    ), patch.object(
        ai_params,
        "load_from_db",
        new=AsyncMock(return_value=False),
    ), patch.object(
        main.sim_engine,
        "initialize",
        new=AsyncMock(side_effect=assert_forced_off_before_simulation),
    ):
        with pytest.raises(RuntimeServiceStarted, match="simulation startup boundary"):
            async with main._run_lifespan(main.app):
                pass


def _matrix(**overrides) -> list[str]:
    kwargs: dict = dict(
        mode="OFF",
        is_paper=True,
        sim_mode=False,
        jwt_secret="strong-random-secret",
        jwt_bootstrap_secret=None,
    )
    kwargs.update(overrides)
    return validate_autopilot_matrix(**kwargs)


def test_matrix_off_mode_always_safe():
    assert _matrix(mode="OFF", jwt_secret=DEFAULT_DEV_JWT_SECRET) == []


def test_matrix_unknown_mode_rejected():
    errors = _matrix(mode="WILD")
    assert any("invalid" in e.lower() for e in errors)


def test_matrix_paper_requires_strong_jwt():
    errors = _matrix(mode="PAPER", jwt_secret=DEFAULT_DEV_JWT_SECRET)
    assert any("JWT_SECRET" in e for e in errors)


def test_matrix_paper_rejects_live_broker_without_sim():
    errors = _matrix(mode="PAPER", is_paper=False, sim_mode=False)
    assert any("live-money broker" in e for e in errors)


def test_matrix_live_rejects_is_paper_broker():
    errors = _matrix(mode="LIVE", is_paper=True, sim_mode=False)
    assert any("IS_PAPER=true" in e for e in errors)


def test_matrix_live_rejects_sim_mode():
    errors = _matrix(mode="LIVE", is_paper=False, sim_mode=True)
    assert any("SIM_MODE=true" in e for e in errors)


def test_matrix_live_rejects_bootstrap_secret_present():
    errors = _matrix(
        mode="LIVE",
        is_paper=False,
        sim_mode=False,
        jwt_bootstrap_secret="anything-set",
    )
    assert any("JWT_BOOTSTRAP_SECRET" in e for e in errors)


def test_matrix_live_accepts_clean_live_combo():
    errors = _matrix(
        mode="LIVE",
        is_paper=False,
        sim_mode=False,
        jwt_secret="strong-random-secret",
        jwt_bootstrap_secret=None,
    )
    assert errors == []
