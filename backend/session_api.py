"""Short-lived local session bootstrap for browser and desktop renderers."""
from __future__ import annotations

import ipaddress
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from auth import DEMO_USER_ID, create_token
from config import cfg


router = APIRouter(prefix="/api/session", tags=["session"])

# A desktop launcher generates this value per launch, supplies it to the
# sidecar through the process environment, and injects the same value into the
# renderer at runtime (for example through IPC or a scrubbed URL fragment). It
# is deliberately not a VITE_* build variable and is never persisted.
SESSION_BOOTSTRAP_TOKEN_ENV = "TRADEBOT_SESSION_BOOTSTRAP_TOKEN"
TRUST_PROXY_HEADERS_ENV = "TRADEBOT_TRUST_PROXY_HEADERS"
SESSION_TOKEN_TTL_SECONDS = 15 * 60
_PRIVATE_PROXY_NETS = tuple(
    ipaddress.ip_network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)


class SessionBootstrapRequest(BaseModel):
    launch_token: str | None = Field(default=None, min_length=16, max_length=512)


class SessionBootstrapResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    expires_in_seconds: int


def _is_loopback_address(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def _is_private_proxy_address(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in _PRIVATE_PROXY_NETS)


def _is_loopback_origin(origin: str) -> bool:
    try:
        hostname = urlsplit(origin).hostname
    except ValueError:
        return False
    if not hostname:
        return False
    return hostname.lower() == "localhost" or _is_loopback_address(hostname)


def _configured_launch_token() -> str | None:
    value = os.getenv(SESSION_BOOTSTRAP_TOKEN_ENV, "").strip()
    return value or None


def _request_is_loopback(request: Request) -> bool:
    client_host = request.client.host if request.client else ""
    if _is_loopback_address(client_host):
        return True
    if os.getenv(TRUST_PROXY_HEADERS_ENV, "").lower() not in {"1", "true", "yes"}:
        return False
    if not _is_private_proxy_address(client_host):
        return False
    forwarded = request.headers.get("X-Forwarded-For", "").strip()
    # The supported Nginx manifest overwrites this header with one address.
    # Reject chains so an appended/spoofed first value cannot grant authority.
    return (
        bool(forwarded)
        and "," not in forwarded
        and (_is_loopback_address(forwarded) or _is_private_proxy_address(forwarded))
    )


def _safe_development_bootstrap() -> bool:
    """Allow zero-config bootstrap only for local non-real-money operation."""
    return (
        getattr(cfg, "AUTOPILOT_MODE", "OFF") != "LIVE"
        and (bool(getattr(cfg, "SIM_MODE", False)) or bool(getattr(cfg, "IS_PAPER", False)))
    )


@router.post("/bootstrap", response_model=SessionBootstrapResponse)
async def bootstrap_session(
    request: Request,
    response: Response,
    payload: SessionBootstrapRequest | None = None,
) -> SessionBootstrapResponse:
    """Issue one short-lived session for the local operator dashboard."""
    if not _request_is_loopback(request):
        raise HTTPException(status_code=403, detail="Session bootstrap is restricted to loopback")

    expected = _configured_launch_token()
    provided = payload.launch_token if payload else None
    if expected:
        if len(expected) < 16:
            raise HTTPException(
                status_code=503,
                detail="Configured session launch capability is too short",
            )
        if not provided or not secrets.compare_digest(provided, expected):
            raise HTTPException(status_code=401, detail="Invalid session launch capability")
    else:
        if not _safe_development_bootstrap():
            raise HTTPException(
                status_code=503,
                detail=(
                    "Session bootstrap requires a per-launch capability outside "
                    "paper or simulation mode"
                ),
            )
        origin = request.headers.get("Origin")
        if origin and not _is_loopback_origin(origin):
            raise HTTPException(status_code=403, detail="Untrusted session bootstrap origin")

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=SESSION_TOKEN_TTL_SECONDS)
    token = create_token(DEMO_USER_ID, expires_at=expires_at)
    response.headers["Cache-Control"] = "no-store"
    return SessionBootstrapResponse(
        access_token=token,
        expires_at=expires_at,
        expires_in_seconds=SESSION_TOKEN_TTL_SECONDS,
    )
