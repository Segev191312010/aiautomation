"""
Health check endpoints for liveness and readiness probes.

Routes
------
GET /api/health          -- liveness probe (always 200 if process is alive)
GET /api/health/ready    -- readiness probe (checks DB + memory)
GET /api/health/detailed -- extended check including subsystem states
"""
from __future__ import annotations

import os
import sys
import time
import logging
from typing import Any

from fastapi import APIRouter

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/health", tags=["health"])

# Recorded once at import time so uptime can be calculated.
_start_time = time.time()


def _process_memory_mb() -> float:
    """
    Return the current process RSS in MiB.

    Uses ``/proc/self/status`` on Linux, ``PROCESS_MEMORY_COUNTERS`` via
    ``ctypes`` on Windows, and falls back to 0.0 when neither is available.
    """
    # --- Linux / macOS (procfs) ---
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    return round(kb / 1024, 1)
    except OSError:
        pass

    # --- Windows: query WorkingSetSize via ctypes ---
    if sys.platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.wintypes.DWORD),
                    ("PageFaultCount", ctypes.wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            psapi = ctypes.windll.psapi  # type: ignore[attr-defined]
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            h_process = kernel32.GetCurrentProcess()
            pmc = PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(pmc)
            if psapi.GetProcessMemoryInfo(h_process, ctypes.byref(pmc), pmc.cb):
                return round(pmc.WorkingSetSize / 1024 / 1024, 1)
        except Exception:  # noqa: BLE001
            pass

    return 0.0


async def _check_database() -> dict[str, Any]:
    """Ping the SQLite database and return a status dict."""
    try:
        from database import get_db  # local import avoids circular dependency

        t0 = time.perf_counter()
        async with get_db() as conn:
            await conn.execute("SELECT 1")
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        return {"status": "ok", "latency_ms": latency_ms}
    except Exception as exc:  # noqa: BLE001
        log.warning("health/db check failed: %s", exc)
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def liveness():
    """
    Liveness probe.

    Returns 200 as long as the Python process is alive and the event loop is
    responding.  Kubernetes / load-balancers use this to decide whether to
    restart the container.
    """
    return {"status": "ok"}


@router.get("/ready")
async def readiness():
    """
    Readiness probe.

    Checks all hard dependencies (currently: SQLite database).  Returns 200
    when all checks pass, 503 when any check reports an error.
    """
    checks: dict[str, Any] = {}
    checks["database"] = await _check_database()

    memory_mb = _process_memory_mb()
    uptime_s = round(time.time() - _start_time)

    overall = (
        "ok"
        if all(c.get("status") == "ok" for c in checks.values())
        else "degraded"
    )

    payload: dict[str, Any] = {
        "status": overall,
        "uptime_seconds": uptime_s,
        "memory_mb": memory_mb,
        "checks": checks,
    }

    status_code = 200 if overall == "ok" else 503
    from fastapi.responses import JSONResponse

    return JSONResponse(payload, status_code=status_code)


@router.get("/detailed")
async def detailed():
    """
    Extended health report.

    Includes everything from the readiness probe plus runtime metadata such as
    Python version, platform, and application version.
    """
    from config import cfg

    base: dict[str, Any] = {}

    # Reuse the readiness checks
    db_check = await _check_database()
    memory_mb = _process_memory_mb()
    uptime_s = round(time.time() - _start_time)

    checks: dict[str, Any] = {"database": db_check}

    overall = (
        "ok"
        if all(c.get("status") == "ok" for c in checks.values())
        else "degraded"
    )

    base = {
        "status": overall,
        "uptime_seconds": uptime_s,
        "memory_mb": memory_mb,
        "checks": checks,
        "runtime": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "pid": os.getpid(),
        },
        "app": {
            "version": cfg.APP_VERSION,
            "mode": "paper" if cfg.IS_PAPER else "live",
            "sim_mode": cfg.SIM_MODE,
            "ibkr_port": cfg.IBKR_PORT,
        },
    }

    status_code = 200 if overall == "ok" else 503
    from fastapi.responses import JSONResponse

    return JSONResponse(base, status_code=status_code)


@router.get("/bot")
async def bot_health():
    """Bot/autopilot health report for operator visibility and alerting."""
    from config import cfg
    from bot_runner import get_bot_health
    from fastapi.responses import JSONResponse

    payload = get_bot_health()
    payload["monitoring_enabled"] = cfg.ENABLE_BOT_HEALTH_MONITORING

    status_code = 200
    if payload.get("is_running") and payload.get("stale_warning"):
        status_code = 503

    return JSONResponse(payload, status_code=status_code)
