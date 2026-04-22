"""Startup and config validation regressions."""
from __future__ import annotations

import pytest

from config import cfg, _validate_config
from startup import DEFAULT_DEV_JWT_SECRET, validate_autopilot_matrix, validate_startup


@pytest.fixture
def restore_cfg():
    previous = {
        "DB_PATH": cfg.DB_PATH,
        "JWT_SECRET": cfg.JWT_SECRET,
        "STRICT_CONFIG": cfg.STRICT_CONFIG,
        "AUTOPILOT_MODE": cfg.AUTOPILOT_MODE,
        "IS_PAPER": cfg.IS_PAPER,
        "IBKR_PORT": cfg.IBKR_PORT,
        "SIM_MODE": cfg.SIM_MODE,
    }
    try:
        yield
    finally:
        for key, value in previous.items():
            setattr(cfg, key, value)


def test_validate_config_rejects_unknown_autopilot_mode(restore_cfg):
    cfg.AUTOPILOT_MODE = "PAPRE"

    with pytest.raises(ValueError, match="AUTOPILOT_MODE='PAPRE' is invalid"):
        _validate_config(cfg)


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
