"""Swing Screener Dashboard — REST endpoints."""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from swing_screeners import fetch_and_compute_dashboard, fetch_and_compute_section
from models import (
    ATRMatrixRow, BreadthMetrics, Club97Entry, GuruScreenerResult,
    StageDistribution, StockbeeMover, SwingDashboardResponse,
    TrendGradeDistribution,
)

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/swing",
    tags=["swing"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/dashboard", response_model=SwingDashboardResponse)
async def dashboard():
    """Full swing screener dashboard — all sections."""
    try:
        return await fetch_and_compute_dashboard()
    except HTTPException:
        raise
    except Exception:
        log.exception("Swing dashboard computation failed")
        raise HTTPException(500, "Swing dashboard computation failed")


@router.get("/breadth", response_model=BreadthMetrics)
async def breadth():
    """Market breadth metrics for 4 universes."""
    try:
        return await fetch_and_compute_section("breadth")
    except HTTPException:
        raise
    except Exception:
        log.exception("Breadth computation failed")
        raise HTTPException(500, "Breadth computation failed")


@router.get("/screener/{name}", response_model=list[GuruScreenerResult])
async def guru_screener(name: Literal["qullamaggie", "minervini", "oneil"]):
    """Guru-inspired screener."""
    try:
        result = await fetch_and_compute_section(f"guru_{name}")
        return result if result is not None else []
    except HTTPException:
        raise
    except Exception:
        log.exception("Guru screener %s failed", name)
        raise HTTPException(500, "Screener computation failed")


@router.get("/atr-matrix", response_model=list[ATRMatrixRow])
async def atr_matrix():
    """ATR extension matrix for sector SPDRs."""
    try:
        return await fetch_and_compute_section("atr_matrix")
    except HTTPException:
        raise
    except Exception:
        log.exception("ATR matrix computation failed")
        raise HTTPException(500, "ATR matrix computation failed")


@router.get("/club97", response_model=list[Club97Entry])
async def club97():
    """97 Club — top 3% on all three RS timeframes."""
    try:
        return await fetch_and_compute_section("club97")
    except HTTPException:
        raise
    except Exception:
        log.exception("97 Club computation failed")
        raise HTTPException(500, "97 Club computation failed")


@router.get("/stockbee/{scan}", response_model=list[StockbeeMover])
async def stockbee(scan: Literal["9m_movers", "weekly_20pct", "daily_4pct"]):
    """Stockbee scan."""
    try:
        return await fetch_and_compute_section(f"stockbee_{scan}")
    except HTTPException:
        raise
    except Exception:
        log.exception("Stockbee scan %s failed", scan)
        raise HTTPException(500, "Stockbee scan failed")


@router.get("/stages", response_model=StageDistribution)
async def stages():
    """Weinstein stage analysis distribution."""
    try:
        return await fetch_and_compute_section("stages")
    except HTTPException:
        raise
    except Exception:
        log.exception("Stage analysis failed")
        raise HTTPException(500, "Stage analysis failed")


@router.get("/grades", response_model=TrendGradeDistribution)
async def grades():
    """Relative trend strength grades (A+ to F)."""
    try:
        return await fetch_and_compute_section("grades")
    except HTTPException:
        raise
    except Exception:
        log.exception("Trend grades failed")
        raise HTTPException(500, "Trend grades failed")
