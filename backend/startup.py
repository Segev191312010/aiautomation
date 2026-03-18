"""
Startup validation and initialization checks.

Call ``await validate_startup()`` inside the FastAPI ``lifespan`` function
(or any other startup hook) to surface misconfiguration early.

Behavior
--------
- Warnings  : logged but never abort the process.
- Errors    : logged; if ``cfg.STRICT_CONFIG`` is ``True`` the process exits
              with code 1 so the container/supervisor restarts with a clear
              reason in the log.
"""
from __future__ import annotations

import logging
import sys
from typing import TypedDict

log = logging.getLogger(__name__)


class StartupResult(TypedDict):
    errors: list[str]
    warnings: list[str]


async def validate_startup() -> StartupResult:
    """
    Run all startup checks.

    Returns a dict with two keys:
      - ``errors``   -- list of fatal configuration problems.
      - ``warnings`` -- list of non-fatal advisories.

    When ``cfg.STRICT_CONFIG`` is ``True`` and there are errors the function
    calls ``sys.exit(1)`` *after* logging, so the problem is clearly visible
    in the process log before shutdown.
    """
    # Deferred import: config triggers dotenv load, keep it lazy for tests.
    from config import cfg

    errors: list[str] = []
    warnings: list[str] = []

    # ------------------------------------------------------------------
    # 1. JWT secret strength
    # ------------------------------------------------------------------
    default_secret = "trading-dev-secret-change-in-prod"
    if cfg.JWT_SECRET == default_secret:
        warnings.append(
            "JWT_SECRET is the default development value. "
            "Set a strong random secret before going to production."
        )

    # ------------------------------------------------------------------
    # 2. Database accessibility
    # ------------------------------------------------------------------
    try:
        import aiosqlite

        async with aiosqlite.connect(cfg.DB_PATH) as db:
            await db.execute("SELECT 1")
        log.info("database check: OK  path=%s", cfg.DB_PATH)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Database not accessible at '{cfg.DB_PATH}': {exc}")

    # ------------------------------------------------------------------
    # 3. IBKR port / paper mode consistency
    # ------------------------------------------------------------------
    live_ports = {7496, 4001}
    paper_ports = {7497, 4002}
    if cfg.IS_PAPER and cfg.IBKR_PORT in live_ports:
        # config.py raises in STRICT_CONFIG mode, so this is the non-strict
        # path — demote to a warning so we surface it without double-exiting.
        warnings.append(
            f"IS_PAPER=true but IBKR_PORT={cfg.IBKR_PORT} is a live-trading port. "
            "Connections to a live account may be rejected or charged real money."
        )
    if not cfg.IS_PAPER and cfg.IBKR_PORT in paper_ports:
        warnings.append(
            f"IS_PAPER=false but IBKR_PORT={cfg.IBKR_PORT} is a paper-trading port. "
            "Live orders will not reach a real account."
        )

    # ------------------------------------------------------------------
    # 4. SIM_MODE vs IS_PAPER advisory
    # ------------------------------------------------------------------
    if cfg.SIM_MODE and not cfg.IS_PAPER:
        warnings.append(
            "SIM_MODE=true and IS_PAPER=false: orders go to the virtual account, "
            "but IBKR is configured for live trading. "
            "Ensure this is intentional."
        )

    # ------------------------------------------------------------------
    # Summary log
    # ------------------------------------------------------------------
    log.info("=== Trading Platform Startup ===")
    log.info("version  : %s", cfg.APP_VERSION)
    log.info("mode     : %s", "PAPER" if cfg.IS_PAPER else "LIVE")
    log.info("sim_mode : %s", "ON" if cfg.SIM_MODE else "OFF")
    log.info("ibkr_port: %d", cfg.IBKR_PORT)
    log.info("database : %s", cfg.DB_PATH)
    log.info("strict   : %s", cfg.STRICT_CONFIG)

    for w in warnings:
        log.warning("STARTUP WARNING: %s", w)

    for e in errors:
        log.error("STARTUP ERROR: %s", e)

    if errors:
        log.error(
            "Startup validation failed with %d error(s).", len(errors)
        )
        if cfg.STRICT_CONFIG:
            log.error("STRICT_CONFIG=true — exiting to force a clean restart.")
            sys.exit(1)

    return StartupResult(errors=errors, warnings=warnings)
