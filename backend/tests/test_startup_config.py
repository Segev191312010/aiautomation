"""Configuration and startup regressions for the V1 service."""
from __future__ import annotations

import pytest

from config import DEFAULT_DEV_JWT_SECRET, Config, _validate_config, cfg
from startup import validate_startup


@pytest.fixture(autouse=True)
def _clear_legacy_authority_environment(monkeypatch):
    for name in (
        "AUTOTRADER_MODE",
        "AUTOPILOT_MODE",
        "LIVE_ALLOWED",
        "AI_AUTONOMY_ENABLED",
        "AI_SHADOW_MODE",
        "CLAUDE_LIVE_TRADING_ENABLED",
        "ENABLE_AUTO_TRADING",
        "AUTO_EXECUTION_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)


def test_config_rejects_empty_database_path():
    config = Config()
    config.DB_PATH = ""
    with pytest.raises(ValueError, match="DB_PATH"):
        _validate_config(config)


@pytest.mark.anyio
async def test_development_secret_is_warning_only(monkeypatch, anyio_backend):
    monkeypatch.setattr(cfg, "DB_PATH", ":memory:")
    monkeypatch.setattr(cfg, "APP_ENV", "development")
    monkeypatch.setattr(cfg, "JWT_SECRET", DEFAULT_DEV_JWT_SECRET)
    monkeypatch.setattr(cfg, "JWT_BOOTSTRAP_SECRET_FILE", "")

    result = await validate_startup()

    assert result["errors"] == []
    assert any("development value" in warning for warning in result["warnings"])


@pytest.mark.anyio
async def test_production_rejects_development_secret(monkeypatch, anyio_backend):
    monkeypatch.setattr(cfg, "DB_PATH", ":memory:")
    monkeypatch.setattr(cfg, "APP_ENV", "production")
    monkeypatch.setattr(cfg, "JWT_SECRET", DEFAULT_DEV_JWT_SECRET)
    monkeypatch.setattr(cfg, "JWT_BOOTSTRAP_SECRET_FILE", "")

    with pytest.raises(RuntimeError, match="strong secret"):
        await validate_startup()


def test_config_has_no_execution_authority_switches():
    for name in ("AUTOPILOT_MODE", "IS_PAPER", "SIM_MODE", "MOCK_MODE", "LIVE_ALLOWED"):
        assert not hasattr(cfg, name)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "name",
    [
        "AUTOTRADER_MODE",
        "AUTOPILOT_MODE",
        "LIVE_ALLOWED",
        "AI_AUTONOMY_ENABLED",
        "AI_SHADOW_MODE",
        "CLAUDE_LIVE_TRADING_ENABLED",
        "ENABLE_AUTO_TRADING",
        "AUTO_EXECUTION_ENABLED",
    ],
)
async def test_startup_rejects_legacy_authority_environment(monkeypatch, name):
    monkeypatch.setattr(cfg, "DB_PATH", ":memory:")
    monkeypatch.setattr(cfg, "APP_ENV", "development")
    monkeypatch.setattr(cfg, "JWT_BOOTSTRAP_SECRET_FILE", "")
    monkeypatch.setattr(cfg, "STRICT_CONFIG", False)
    monkeypatch.setenv(name, "PAPER")

    with pytest.raises(RuntimeError, match="authority validation failed"):
        await validate_startup()


@pytest.mark.anyio
async def test_data_environment_does_not_count_as_authority(monkeypatch, anyio_backend):
    monkeypatch.setattr(cfg, "DB_PATH", ":memory:")
    monkeypatch.setattr(cfg, "APP_ENV", "development")
    monkeypatch.setattr(cfg, "JWT_BOOTSTRAP_SECRET_FILE", "")
    monkeypatch.setattr(cfg, "STRICT_CONFIG", False)
    monkeypatch.setenv("IBKR_HOST", "127.0.0.1")
    monkeypatch.setenv("IBKR_PORT", "7497")
    monkeypatch.setenv("MARKET_DATA_MODE", "historical")

    result = await validate_startup()

    assert result["errors"] == []
