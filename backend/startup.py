"""
Startup validation and initialization checks.

Call ``await validate_startup()`` inside the FastAPI ``lifespan`` function
(or any other startup hook) to surface misconfiguration early.

Behavior
--------
- Warnings  : logged but never abort the process.
- Errors    : logged and always abort startup. A safety error is not advisory
              and cannot be bypassed with ``STRICT_CONFIG=false``.

``validate_autopilot_matrix`` is also exported so runtime mode changes
(e.g. DB-sync after startup, or operator mode flips) can enforce the same
invariants on the new mode without re-running the whole startup path.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import IO, TypedDict

from db.execution_lease import (
    Lease,
    acquire_execution_lease,
    release_execution_lease,
    renew_execution_lease,
    LEASE_HEARTBEAT_SECONDS,
    LEASE_TTL_SECONDS,
)

log = logging.getLogger(__name__)

DEFAULT_DEV_JWT_SECRET = "trading-dev-secret-MUST-SET-IN-ENV"
MIN_JWT_SECRET_BYTES = 32
# Stage 9A is explicitly NO-GO for real-money AI.  This code-owned fence has
# no environment override, so ordinary runtime credentials/configuration
# cannot turn an unapproved release into LIVE.
STAGE_9A_LIVE_RELEASE_APPROVED = False

_execution_lock_handles: dict[str, IO[str]] = {}
_execution_lease: Lease | None = None


class StartupResult(TypedDict):
    errors: list[str]
    warnings: list[str]


def _has_strong_jwt_secret(secret: str) -> bool:
    """Return whether the legacy HS256 secret meets the containment floor.

    This is not approval of the demo JWT flow for production. ADR 0008 still
    requires an established identity/session system. The length floor only
    prevents trivially forgeable empty/short secrets while that migration is
    pending.
    """
    if secret == DEFAULT_DEV_JWT_SECRET:
        return False
    return len((secret or "").encode("utf-8")) >= MIN_JWT_SECRET_BYTES


def _is_ephemeral_sqlite_path(db_path: str) -> bool:
    """Detect SQLite in-memory URIs that cannot provide durable shared state."""
    normalized = (db_path or "").strip().lower()
    return (
        not normalized
        or normalized == ":memory:"
        or normalized.startswith("file::memory:")
        or (normalized.startswith("file:") and "mode=memory" in normalized)
    )


def real_money_broker_configured(
    *,
    is_paper: bool,
    sim_mode: bool,
    ibkr_port: int | None = None,
) -> bool:
    """Conservatively identify a configuration capable of reaching real money."""
    if sim_mode:
        return False
    return not is_paper or ibkr_port in {7496, 4001}


def _execution_lock_path(db_path: str, explicit_path: str | None = None) -> str:
    configured = explicit_path or os.getenv("EXECUTION_LOCK_PATH", "").strip()
    if configured:
        return str(Path(configured).expanduser().resolve())
    if _is_ephemeral_sqlite_path(db_path):
        return str((Path.cwd() / ".tradebot-execution.lock").resolve())
    database_path = Path(db_path).expanduser().resolve()
    return str(database_path.with_name(f"{database_path.name}.execution.lock"))


def _lock_file_nonblocking(handle: IO[str]) -> None:
    if os.name == "nt":  # pragma: no cover - production image is Linux
        import msvcrt

        handle.seek(0)
        if not handle.read(1):
            handle.seek(0)
            handle.write("\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle: IO[str]) -> None:
    if os.name == "nt":  # pragma: no cover - production image is Linux
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def acquire_execution_process_lock(
    *,
    db_path: str,
    lock_path: str | None = None,
) -> str:
    """Hold a host/shared-volume singleton lock for the process lifetime.

    This catches undeclared Uvicorn/Gunicorn workers that do not set WORKERS.
    It is immediate containment, not the durable cross-host lease/fencing
    design from ADR 0006.
    """
    resolved = _execution_lock_path(db_path, lock_path)
    if resolved in _execution_lock_handles:
        raise RuntimeError(
            "This process already owns the execution lock. Refusing to start "
            "a second broker/background lifecycle."
        )

    try:
        handle = open(resolved, "a+", encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            f"Cannot open execution-owner lock at {resolved!r}: {exc}"
        ) from exc

    try:
        _lock_file_nonblocking(handle)
    except (BlockingIOError, OSError) as exc:
        handle.close()
        raise RuntimeError(
            "Another process already owns the execution lock. Refusing to "
            "start duplicate broker/background loops."
        ) from exc

    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()}\n")
    handle.flush()
    _execution_lock_handles[resolved] = handle
    log.info("execution owner lock acquired: %s", resolved)
    return resolved


def release_execution_process_lock(
    *,
    db_path: str,
    lock_path: str | None = None,
) -> None:
    """Release a lock acquired by :func:`acquire_execution_process_lock`."""
    resolved = _execution_lock_path(db_path, lock_path)
    handle = _execution_lock_handles.pop(resolved, None)
    if handle is None:
        return
    try:
        _unlock_file(handle)
    finally:
        handle.close()
    log.info("execution owner lock released: %s", resolved)


async def acquire_execution_lease_and_lock(
    *,
    db_path: str,
    lock_path: str | None = None,
) -> Lease:
    """Acquire both the durable execution lease and the file lock fallback.

    The lease provides cross-host ownership via SQLite; the file lock is a
    fallback for the common single-host case and catches workers that bypass
    the DB lease path. This must be called from an async context (lifespan).
    """
    global _execution_lease
    if _execution_lease is not None:
        raise RuntimeError("Execution lease already held by this process.")

    # File lock first: cheap, immediate, same-host protection.
    acquire_execution_process_lock(db_path=db_path, lock_path=lock_path)

    try:
        lease = await acquire_execution_lease()
    except Exception:
        # Roll back file lock if DB lease fails so caller can retry cleanly.
        release_execution_process_lock(db_path=db_path, lock_path=lock_path)
        raise

    _execution_lease = lease
    log.info("execution lease + lock acquired: owner=%s token=%s...", lease.owner_id, lease.fencing_token[:8])
    return lease


async def release_execution_lease_and_lock(
    *,
    db_path: str,
    lock_path: str | None = None,
) -> None:
    """Release both the durable lease and the file lock fallback."""
    global _execution_lease
    if _execution_lease is not None:
        await release_execution_lease(_execution_lease.fencing_token)
        _execution_lease = None
    release_execution_process_lock(db_path=db_path, lock_path=lock_path)


async def renew_execution_lease_heartbeat() -> Lease | None:
    """Renew the process execution lease if we hold one."""
    global _execution_lease
    if _execution_lease is None:
        return None
    renewed = await renew_execution_lease(
        _execution_lease.fencing_token,
        ttl_seconds=LEASE_TTL_SECONDS,
    )
    if renewed is None:
        log.error("execution lease renewal failed — ownership may be lost")
    _execution_lease = renewed
    return renewed


def get_execution_fencing_token() -> str | None:
    """Return the current process fencing token, or None if not leased."""
    lease = _execution_lease
    return lease.fencing_token if lease is not None else None


def validate_autopilot_matrix(
    *,
    mode: str,
    is_paper: bool,
    sim_mode: bool,
    jwt_secret: str,
    jwt_bootstrap_secret: str | None,
    ibkr_port: int | None = None,
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

    broker_live = real_money_broker_configured(
        is_paper=is_paper,
        sim_mode=sim_mode,
        ibkr_port=ibkr_port,
    )

    if mode == "LIVE" and not STAGE_9A_LIVE_RELEASE_APPROVED:
        errors.append(
            "AUTOPILOT_MODE=LIVE is disabled by the Stage 9A release fence. "
            "Critical pre-live risks and human approvals remain open; use OFF "
            "or PAPER."
        )

    if broker_live and not STAGE_9A_LIVE_RELEASE_APPROVED:
        errors.append(
            "Real-money broker connectivity is disabled by the Stage 9A "
            "release fence. Use SIM_MODE or an IBKR paper account."
        )

    if is_paper and not sim_mode and ibkr_port in {7496, 4001}:
        errors.append(
            f"IS_PAPER=true cannot use known live IBKR port {ibkr_port}. "
            "Refusing a flag/port mismatch that could reach real money."
        )

    # PAPER/LIVE grant AI authority. A real-money broker remains reachable by
    # manual routes even while AI is OFF, so that state also requires hardened
    # authentication.
    if not _has_strong_jwt_secret(jwt_secret) and (
        mode in ("PAPER", "LIVE") or broker_live
    ):
        errors.append(
            f"AUTOPILOT_MODE={mode} with the configured broker/AI authority "
            f"requires JWT_SECRET to be at least {MIN_JWT_SECRET_BYTES} bytes "
            "and not the development placeholder. Set a cryptographically "
            "random secret before enabling broker-capable operation."
        )

    # Bootstrap-token auth is a development convenience, not a real login
    # flow. Refuse it whenever real-money manual orders are reachable or LIVE
    # AI authority is requested.
    if jwt_bootstrap_secret and (broker_live or mode == "LIVE"):
        errors.append(
            f"AUTOPILOT_MODE={mode} with real-money/LIVE authority cannot use "
            "JWT_BOOTSTRAP_SECRET. Remove it and use the approved production "
            "identity/session flow."
        )

    if mode == "PAPER" and broker_live:
        errors.append(
            "AUTOPILOT_MODE=PAPER with IS_PAPER=false and SIM_MODE=false would "
            "send AI-authorized orders to a real-money broker. Use a paper "
            "account/SIM_MODE, or complete the LIVE approval path."
        )

    if mode == "OFF":
        return errors

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
    return errors


def validate_execution_topology(*, workers: int, lease_acquired: bool = True) -> list[str]:
    """Return fatal errors for unsupported monolith worker topology.

    Every Uvicorn worker currently starts its own IBKR client, reconciliation,
    alert, optimizer, learning, and bot lifespan loops. With the durable
    execution lease (ADR 0006) a multi-worker topology still risks concurrent
    broker clients and duplicated background loops, so it remains unsupported.
    """
    if workers == 1:
        return []
    return [
        f"WORKERS={workers} is unsupported: the current monolith must run "
        "exactly one Uvicorn worker. Background execution ownership is now "
        "leased/fenced, but background tasks are not distributed yet. Set WORKERS=1."
    ]


async def validate_startup() -> StartupResult:
    """
    Run all startup checks.

    Returns a dict with two keys:
      - ``errors``   -- list of fatal configuration problems.
      - ``warnings`` -- list of non-fatal advisories.

    Any error calls ``sys.exit(1)`` after logging. ``STRICT_CONFIG`` cannot
    weaken a safety invariant.
    """
    # Deferred import: config triggers dotenv load, keep it lazy for tests.
    from config import cfg

    errors: list[str] = []
    warnings: list[str] = []

    # ------------------------------------------------------------------
    # 1. JWT secret + autopilot mode matrix (C6 + autopilot-matrix safety)
    # ------------------------------------------------------------------
    if not _has_strong_jwt_secret(cfg.JWT_SECRET) and cfg.AUTOPILOT_MODE == "OFF":
        warnings.append(
            f"JWT_SECRET is weaker than the {MIN_JWT_SECRET_BYTES}-byte "
            "containment floor. Set a cryptographically random secret."
        )
    errors.extend(
        validate_autopilot_matrix(
            mode=cfg.AUTOPILOT_MODE,
            is_paper=cfg.IS_PAPER,
            sim_mode=cfg.SIM_MODE,
            jwt_secret=cfg.JWT_SECRET,
            jwt_bootstrap_secret=getattr(cfg, "JWT_BOOTSTRAP_SECRET", "") or None,
            ibkr_port=cfg.IBKR_PORT,
        )
    )

    # ------------------------------------------------------------------
    # 2. Database accessibility
    # ------------------------------------------------------------------
    try:
        import aiosqlite

        if _is_ephemeral_sqlite_path(cfg.DB_PATH):
            raise ValueError(
                "in-memory SQLite cannot provide durable/shared execution safety state"
            )
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
    if cfg.IS_PAPER and cfg.IBKR_PORT in live_ports and cfg.SIM_MODE:
        warnings.append(
            f"IS_PAPER=true but IBKR_PORT={cfg.IBKR_PORT} is a live-trading "
            "port. SIM_MODE prevents connection, but correct the mismatch."
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
    # 5. Direct-trades intent token gate (live / staging environments)
    # ------------------------------------------------------------------
    # /api/autopilot/direct-trades/execute lets an authenticated caller place
    # an arbitrary AIDirectTrade. The HTTP path now forces skip_safety=False,
    # but in any non-dev environment we additionally require an explicit
    # X-Intent-Token header matched against DIRECT_TRADE_INTENT_TOKEN. If the
    # env is "live" or "staging" and the token is empty, refuse to boot.
    env_name = (os.getenv("ENV") or "").strip().lower()
    if env_name in {"live", "staging"} and not getattr(cfg, "DIRECT_TRADE_INTENT_TOKEN", ""):
        errors.append(
            f"ENV='{env_name}' requires DIRECT_TRADE_INTENT_TOKEN to be set. "
            "Refusing to boot — direct-trades HTTP endpoint would be ungated."
        )

    # ------------------------------------------------------------------
    # 6. Single execution-owner topology (Stage 9A) + lease acquisition
    # ------------------------------------------------------------------
    # The durable rate limiter is shared across processes, but it does not
    # solve duplicated broker clients/background loops or stale-owner
    # submission. WORKERS=1 remains a hard invariant until background tasks are
    # distributed. Then acquire the durable cross-host lease so this process
    # is the recognized execution owner.
    topology_errors: list[str] = []
    raw_workers = os.getenv("WORKERS", "1") or "1"
    try:
        workers = int(raw_workers)
    except ValueError:
        topology_errors.append(
            f"WORKERS={raw_workers!r} is invalid; the current monolith requires WORKERS=1."
        )
    else:
        topology_errors.extend(validate_execution_topology(workers=workers))
    errors.extend(topology_errors)

    if not errors:
        # If this process already owns the lease (e.g., multiple startup
        # validation calls in tests), treat that as success rather than a
        # duplicate-ownership error.
        if get_execution_fencing_token() is None:
            try:
                await acquire_execution_lease_and_lock(db_path=cfg.DB_PATH)
            except RuntimeError as exc:
                errors.append(f"Could not acquire execution lease: {exc}")

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
        log.error("Startup errors are always fatal — exiting.")
        sys.exit(1)

    return StartupResult(errors=errors, warnings=warnings)
