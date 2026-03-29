"""Auth routes — /api/auth/*"""
from fastapi import APIRouter, Depends

from auth import create_token, get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
async def auth_me(user=Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "settings": user.settings}


@router.post("/token")
async def auth_token():
    """Issue a demo token (for frontend bootstrap). Full login in Stage 8."""
    token = create_token("demo")
    return {"access_token": token, "token_type": "bearer"}
