"""Startup and config validation regressions."""
from __future__ import annotations

import pytest

from config import cfg, _validate_config
from startup import DEFAULT_DEV_JWT_SECRET, validate_startup


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

    assert any("Refusing to start" in e for e in result["errors"])
