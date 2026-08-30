"""Fail-fast startup validation for the execution-disabled V1 service."""
from __future__ import annotations

import logging
import os
from typing import TypedDict

import aiosqlite

from config import DEFAULT_DEV_JWT_SECRET, cfg, resolve_jwt_bootstrap_secret

log = logging.getLogger(__name__)


class StartupResult(TypedDict):
    errors: list[str]
    warnings: list[str]


LEGACY_AUTHORITY_ENVIRONMENT_VARIABLES = frozenset(
    {
        "AUTOTRADER_MODE",
        "AUTOPILOT_MODE",
        "LIVE_ALLOWED",
        "AI_AUTONOMY_ENABLED",
        "AI_SHADOW_MODE",
        "CLAUDE_LIVE_TRADING_ENABLED",
        "AUTO_TRADING_ENABLED",
        "AUTOMATED_TRADING_ENABLED",
        "AUTO_EXECUTION_ENABLED",
        "EXECUTION_ENABLED",
        "ENABLE_AUTOTRADING",
        "ENABLE_AUTO_TRADING",
        "ENABLE_PAPER_TRADING",
        "ENABLE_LIVE_TRADING",
        "PAPER_TRADING_ENABLED",
        "LIVE_TRADING_ENABLED",
        "ALLOW_PAPER_TRADING",
        "ALLOW_LIVE_TRADING",
        "BOT_AUTOSTART",
        "BOT_ENABLED",
        "RULE_BOT_ENABLED",
    }
)


def _legacy_authority_environment_variables() -> tuple[str, ...]:
    """Return only known authority switches, never general data settings."""
    return tuple(sorted(LEGACY_AUTHORITY_ENVIRONMENT_VARIABLES & os.environ.keys()))


async def validate_startup() -> StartupResult:
    errors: list[str] = []
    warnings: list[str] = []

    forbidden_authority_variables = _legacy_authority_environment_variables()
    if forbidden_authority_variables:
        errors.append(
            "Legacy authority environment variables are unsupported: "
            + ", ".join(forbidden_authority_variables)
        )

    if cfg.JWT_SECRET == DEFAULT_DEV_JWT_SECRET:
        message = "JWT_SECRET is the development value"
        if cfg.APP_ENV in {"production", "staging"}:
            errors.append(f"{message}; configure a strong secret before deployment")
        else:
            warnings.append(f"{message}; local bootstrap only")

    try:
        resolve_jwt_bootstrap_secret()
    except ValueError as exc:
        errors.append(str(exc))

    try:
        async with aiosqlite.connect(cfg.DB_PATH) as db:
            await db.execute("SELECT 1")
    except Exception as exc:
        errors.append(f"Database not accessible at '{cfg.DB_PATH}': {exc}")

    for warning in warnings:
        log.warning("STARTUP WARNING: %s", warning)
    for error in errors:
        log.error("STARTUP ERROR: %s", error)

    # Authority violations are security failures, not configuration advice.
    # They must stop the process even when STRICT_CONFIG is disabled for other
    # local-development validation errors.
    if forbidden_authority_variables:
        raise RuntimeError("Autotrader V1 authority validation failed: " + "; ".join(errors))

    if errors and cfg.STRICT_CONFIG:
        raise RuntimeError("Autotrader V1 startup validation failed: " + "; ".join(errors))

    log.info(
        "Autotrader V1 ready version=%s environment=%s execution_enabled=false",
        cfg.APP_VERSION,
        cfg.APP_ENV,
    )
    return StartupResult(errors=errors, warnings=warnings)
