"""Settings routes — /api/settings"""
from fastapi import APIRouter, Depends, Request

from auth import get_current_user
from settings import get_settings, update_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_user_settings(user=Depends(get_current_user)):
    return await get_settings(user.id)


@router.put("")
async def update_user_settings(request: Request, user=Depends(get_current_user)):
    body = await request.json()
    return await update_settings(user.id, body)
