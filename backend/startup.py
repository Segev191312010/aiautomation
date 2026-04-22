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

``validate_autopilot_matrix`` is also exported so runtime mode changes
(e.g. DB-sync after startup, or operator mode flips) can enforce the same
invariants on the new mode without re-running the whole startup path.
"""
from __future__ import annotations

import logging
import sys
from typing import TypedDict

log = logging.getLogger(__name__)

DEFAULT_DEV_JWT_SECRET = "trading-dev-secret-MUST-SET-IN-ENV"


class StartupResult(TypedDict):
    errors: list[str]
    warnings: list[str]


def validate_autopilot_matrix(
    *,
    mode: str,
    is_paper: bool,
    sim_mode: bool,
    jwt_secret: str,
    jwt_bootstrap_secret: str | None,
) -> list[str]:
    """Check AUTOPILOT_MODE × IS_PAPER × SIM_MODE × auth for safe combinations.

    Returns a list of human-readable error strings; empty list == safe.
    Callable both from initial startup validation and from runtime mode
    changes (DB sync, operator flip). Keep this pure so it can be unit
    tested without mutating cfg.
    """
    errors: list[str] = []
    mode = (mode or "OFF").upper()

    if mode not in ("OFF", "PAPER", "LIVE"):
        errors.append(f"AUTOPILOT_MODE='{mode}' is invalid. Must be OFF, PAPER, or LIVE.")
        return errors

    if mode == "OFF":
        # Inactive AI — nothing else to check.
        return errors

    # PAPER and LIVE both grant AI authority. Refuse the default JWT_SECRET.
    if jwt_secret == DEFAULT_DEV_JWT_SECRET:
        errors.append(
            f"AUTOPILOT_MODE={mode} requires a non-default JWT_SECRET. "
            "Set JWT_SECRET in .env to a strong random string before enabling AI authority."
        )

    if mode == "LIVE":
        # Real-money AI is only safe when:
        #   - broker is live (IS_PAPER=false) AND
        #   - sim interception is off (SIM_MODE=false).
        # Any other combination means AI thinks it's live but orders land
        # somewhere else — surface the mismatch loudly.
        if is_paper and not sim_mode:
            errors.append(
                "AUTOPILOT_MODE=LIVE with IS_PAPER=true routes AI orders to the "
                "paper broker. Use AUTOPILOT_MODE=PAPER instead, or set IS_PAPER=false."
            )
        if sim_mode:
            errors.append(
                "AUTOPILOT_MODE=LIVE with SIM_MODE=true sends AI orders to the "
                "virtual account. Real-money authority must not run in SIM_MODE."
            )
        # Bootstrap-token auth is not a login flow; do not let real money run
        # on a system where an unauthenticated local caller can mint a token.
        # Callers can still set it for dev, but LIVE explicitly rejects it.
        if jwt_bootstrap_secret:
            errors.append(
                "AUTOPILOT_MODE=LIVE with JWT_BOOTSTRAP_SECRET configured is "
                "unsafe. Remove JWT_BOOTSTRAP_SECRET from .env and use a real "
                "login flow before enabling live AI authority."
            )

    return errors


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
    # 1. JWT secret + autopilot mode matrix (C6 + autopilot-matrix safety)
    # ------------------------------------------------------------------
    if cfg.JWT_SECRET == DEFAULT_DEV_JWT_SECRET and cfg.AUTOPILOT_MODE == "OFF":
        warnings.append(
            "JWT_SECRET is the default development value. "
            "Set a strong random secret before going to production."
        )
    errors.extend(
        validate_autopilot_matrix(
            mode=cfg.AUTOPILOT_MODE,
            is_paper=cfg.IS_PAPER,
            sim_mode=cfg.SIM_MODE,
            jwt_secret=cfg.JWT_SECRET,
            jwt_bootstrap_secret=getattr(cfg, "JWT_BOOTSTRAP_SECRET", "") or None,
        )
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
    log.info("autopilot: %s", getattr(cfg, "AUTOPILOT_MODE", "OFF"))
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
