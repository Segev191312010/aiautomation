"""Local bootstrap and authenticated identity routes."""
from __future__ import annotations

import hmac
import ipaddress
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request

from auth import create_token, get_current_user
from config import resolve_jwt_bootstrap_secret

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])
_LOOPBACK_NETS = (ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128"))


def _is_loopback(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in _LOOPBACK_NETS)


@router.get("/me")
async def auth_me(user=Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "settings": user.settings}


@router.post("/token")
async def auth_token(request: Request):
    try:
        bootstrap_secret = resolve_jwt_bootstrap_secret()
    except ValueError as exc:
        log.error("Bootstrap authentication configuration is invalid")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not bootstrap_secret:
        raise HTTPException(status_code=503, detail="Bootstrap authentication is disabled")

    client_host = request.client.host if request.client else ""
    allow_remote = os.getenv("BOOTSTRAP_ALLOW_REMOTE", "").lower() in {"1", "true", "yes"}
    if not allow_remote and not _is_loopback(client_host):
        raise HTTPException(status_code=403, detail="Bootstrap auth is restricted to loopback")

    provided_secret = request.headers.get("X-Bootstrap-Secret", "")
    if not hmac.compare_digest(provided_secret, bootstrap_secret):
        raise HTTPException(status_code=401, detail="Invalid bootstrap secret")
    return {"access_token": create_token("demo"), "token_type": "bearer"}
