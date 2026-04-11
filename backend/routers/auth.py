"""Auth routes — /api/auth/*"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from auth import create_token, get_current_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
async def auth_me(user=Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "settings": user.settings}


@router.post("/token")
async def auth_token(request: Request):
    """Issue a demo token (for frontend bootstrap). Full login in Stage 8.

    Requires a bootstrap secret (JWT_BOOTSTRAP_SECRET env var) to prevent
    unauthenticated token issuance. The secret must match on both sides.
    """
    from config import cfg

    bootstrap_secret = getattr(cfg, "JWT_BOOTSTRAP_SECRET", None)
    if not bootstrap_secret:
        log.error("JWT_BOOTSTRAP_SECRET not configured — /api/auth/token disabled")
        raise HTTPException(
            status_code=503,
            detail="Bootstrap authentication is disabled. Set JWT_BOOTSTRAP_SECRET in .env",
        )

    provided_secret = request.headers.get("X-Bootstrap-Secret")
    if not provided_secret or provided_secret != bootstrap_secret:
        log.warning("SECURITY: Invalid/missing bootstrap secret on /api/auth/token")
        raise HTTPException(status_code=401, detail="Invalid bootstrap secret")

    if not getattr(cfg, "SIM_MODE", True):
        log.warning("SECURITY: Demo token issued in LIVE trading mode")
    token = create_token("demo")
    return {"access_token": token, "token_type": "bearer"}
