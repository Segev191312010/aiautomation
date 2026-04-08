"""Auth routes — /api/auth/*"""
import logging

from fastapi import APIRouter, Depends

from auth import create_token, get_current_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
async def auth_me(user=Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "settings": user.settings}


@router.post("/token")
async def auth_token():
    """Issue a demo token (for frontend bootstrap). Full login in Stage 8."""
    from config import cfg
    if not getattr(cfg, "SIM_MODE", True):
        log.warning("SECURITY: Demo token issued in LIVE trading mode")
    token = create_token("demo")
    return {"access_token": token, "token_type": "bearer"}
