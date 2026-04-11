"""Risk Management & Portfolio Analytics API endpoints."""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import Optional

from auth import get_current_user
from risk_config import DEFAULT_LIMITS, RiskLimits
from risk_manager import check_trade_risk, calculate_position_size, check_drawdown
from portfolio_analytics import (
    compute_realized_pnl, compute_unrealized_pnl, compute_daily_pnl,
    compute_sector_exposure, compute_correlation_matrix, compute_performance_metrics,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["risk"], dependencies=[Depends(get_current_user)])

_user_limits: dict[int, RiskLimits] = {}


def _get_limits(user_id: int = 1) -> RiskLimits:
    return _user_limits.get(user_id, DEFAULT_LIMITS)


class PositionSizeRequest(BaseModel):
    entry_price: float
    stop_price: Optional[float] = None
    account_value: float = 100000
    risk_pct: float = 1.0
    method: str = "fixed_fractional"


class RiskSettingsUpdate(BaseModel):
    max_position_pct: Optional[float] = None
    max_sector_pct: Optional[float] = None
    max_daily_loss_pct: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    max_open_positions: Optional[int] = None
    sizing_method: Optional[str] = None


@router.get("/api/risk/portfolio")
async def portfolio_analytics():
    from database import get_trades
    trades = await get_trades()
    trade_dicts = [t.model_dump() for t in trades]
    pnl = compute_realized_pnl(trade_dicts)
    daily = compute_daily_pnl(pnl.get("matched_trades", []))
    perf = compute_performance_metrics(pnl.get("matched_trades", []))
    dd = check_drawdown([{"value": d["cumulative"] + 100000} for d in daily] if daily else [])
    return {
        "pnl": {k: v for k, v in pnl.items() if k != "matched_trades"},
        "daily_pnl": daily,
        "performance": perf,
        "drawdown": {
            "current_pct": dd.current_drawdown_pct,
            "max_pct": dd.max_drawdown_pct,
            "peak": dd.peak_value,
            "trough": dd.trough_value,
        },
    }


@router.get("/api/risk/check/{symbol}")
async def risk_check(symbol: str, qty: int = 10, side: str = "BUY"):
    limits = _get_limits()
    result = check_trade_risk(symbol, qty, side, [], 100000, limits)
    return {"status": result.status, "reasons": result.reasons}


@router.post("/api/risk/position-size")
async def position_size(req: PositionSizeRequest):
    return calculate_position_size(
        req.entry_price, req.stop_price, req.account_value, req.risk_pct, req.method,
    )


@router.get("/api/risk/drawdown")
async def drawdown_status():
    from database import get_trades
    trades = await get_trades()
    pnl = compute_realized_pnl([t.model_dump() for t in trades])
    daily = compute_daily_pnl(pnl.get("matched_trades", []))
    dd = check_drawdown([{"value": d["cumulative"] + 100000} for d in daily] if daily else [])
    return {
        "current_pct": dd.current_drawdown_pct,
        "max_pct": dd.max_drawdown_pct,
        "peak": dd.peak_value,
        "trough": dd.trough_value,
        "duration_days": dd.drawdown_duration_days,
    }


@router.get("/api/risk/correlation")
async def correlation(symbols: str = Query("", description="Comma-separated symbols")):
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if len(syms) < 2:
        return {"error": "Need at least 2 symbols", "symbols": [], "matrix": []}
    result = compute_correlation_matrix(syms)
    return result or {"symbols": syms, "matrix": []}


@router.get("/api/risk/sector-exposure")
async def sector_exposure():
    return {"sectors": []}


@router.get("/api/analytics/pnl")
async def pnl_summary():
    from database import get_trades
    trades = await get_trades()
    result = compute_realized_pnl([t.model_dump() for t in trades])
    return {k: v for k, v in result.items() if k != "matched_trades"}


@router.get("/api/analytics/pnl/daily")
async def daily_pnl(days: int = 90):
    from database import get_trades
    trades = await get_trades()
    pnl = compute_realized_pnl([t.model_dump() for t in trades])
    return compute_daily_pnl(pnl.get("matched_trades", []), days)


@router.get("/api/analytics/performance")
async def performance():
    from database import get_trades
    trades = await get_trades()
    pnl = compute_realized_pnl([t.model_dump() for t in trades])
    return compute_performance_metrics(pnl.get("matched_trades", []))


@router.get("/api/analytics/trades/matched")
async def matched_trades():
    from database import get_trades
    trades = await get_trades()
    pnl = compute_realized_pnl([t.model_dump() for t in trades])
    return {
        "trades": pnl.get("matched_trades", []),
        "summary": {
            "total_pnl": pnl["total_pnl"],
            "win_rate": pnl["win_rate"],
            "trade_count": pnl["trade_count"],
        },
    }


@router.put("/api/risk/settings")
async def update_risk_settings(req: RiskSettingsUpdate):
    current = _get_limits()
    for k, v in req.model_dump(exclude_none=True).items():
        if hasattr(current, k):
            setattr(current, k, v)
    _user_limits[1] = current
    return {"status": "updated", "limits": current.__dict__}


@router.get("/api/risk/settings")
async def get_risk_settings():
    return _get_limits().__dict__
