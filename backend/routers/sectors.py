"""Sector rotation routes — /api/sectors/*"""
from fastapi import APIRouter

from sector_rotation import get_sector_rotation, get_sector_leaders, get_rotation_heatmap

router = APIRouter(prefix="/api/sectors", tags=["sectors"])


@router.get("/rotation")
async def sector_rotation(lookback_days: int = 90):
    """Sector RS ratio, momentum, and quadrant placement vs SPY."""
    return await get_sector_rotation(lookback_days)


@router.get("/heatmap")
async def sector_heatmap():
    """Multi-timeframe sector performance grid."""
    return await get_rotation_heatmap()


@router.get("/{sector_etf}/leaders")
async def sector_leaders(sector_etf: str, top_n: int = 10, period: str = "3mo"):
    """Top performing stocks within a sector."""
    return await get_sector_leaders(sector_etf.upper(), top_n, period)
