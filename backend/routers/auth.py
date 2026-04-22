"""Auth routes — /api/auth/*"""
import ipaddress
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request

from auth import create_token, get_current_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_LOOPBACK_NETS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)


def _is_loopback(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in _LOOPBACK_NETS)


@router.get("/me")
async def auth_me(user=Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "settings": user.settings}


@router.post("/token")
async def auth_token(request: Request):
    """Issue a demo token (for frontend bootstrap).

    This is NOT a real login flow. It exists so a single-operator dev setup
    can mint a session without a password UI. Hardened so the bootstrap
    secret alone is insufficient:

      1. JWT_BOOTSTRAP_SECRET must be configured and match (existing check).
      2. Request must originate from loopback, unless BOOTSTRAP_ALLOW_REMOTE=1.
      3. Refused outright when AUTOPILOT_MODE=LIVE — real money never runs on
         a bootstrap token.

    Replace with a proper login flow before exposing the service remotely.
    """
    from config import cfg

    bootstrap_secret = getattr(cfg, "JWT_BOOTSTRAP_SECRET", None)
    if not bootstrap_secret:
        log.error("JWT_BOOTSTRAP_SECRET not configured — /api/auth/token disabled")
        raise HTTPException(
            status_code=503,
            detail="Bootstrap authentication is disabled. Set JWT_BOOTSTRAP_SECRET in .env",
        )

    if getattr(cfg, "AUTOPILOT_MODE", "OFF") == "LIVE":
        log.error("SECURITY: /api/auth/token refused — AUTOPILOT_MODE=LIVE")
        raise HTTPException(
            status_code=503,
            detail="Bootstrap auth is disabled while AUTOPILOT_MODE=LIVE. Use a real session.",
        )

    client_host = request.client.host if request.client else ""
    allow_remote = os.getenv("BOOTSTRAP_ALLOW_REMOTE", "").lower() in {"1", "true", "yes"}
    if not allow_remote and not _is_loopback(client_host):
        log.warning(
            "SECURITY: /api/auth/token refused — non-loopback origin %s (set BOOTSTRAP_ALLOW_REMOTE=1 to override)",
            client_host,
        )
        raise HTTPException(status_code=403, detail="Bootstrap auth is restricted to loopback")

    provided_secret = request.headers.get("X-Bootstrap-Secret")
    if not provided_secret or provided_secret != bootstrap_secret:
        log.warning("SECURITY: Invalid/missing bootstrap secret on /api/auth/token")
        raise HTTPException(status_code=401, detail="Invalid bootstrap secret")

    if not getattr(cfg, "SIM_MODE", True):
        log.warning("SECURITY: Demo token issued in LIVE trading mode")
    token = create_token("demo")
    return {"access_token": token, "token_type": "bearer"}
