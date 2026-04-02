"""Account & position routes — /api/account/*, /api/positions/*"""
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from config import cfg
from database import get_trades
from ibkr_client import ibkr
from simulation import sim_engine

log = logging.getLogger(__name__)

router = APIRouter(tags=["positions"], dependencies=[Depends(get_current_user)])


@router.get("/api/account/summary")
async def get_account_summary():
    if cfg.SIM_MODE:
        account = await sim_engine.get_account()
        return account.model_dump()
    if ibkr.is_connected():
        try:
            summary = await ibkr.get_account_summary()
            return summary.model_dump()
        except Exception as exc:
            log.warning("Account fetch failed: %s", exc)
    raise HTTPException(503, "IBKR not connected")


@router.get("/api/account")
async def get_account():
    if not ibkr.is_connected():
        raise HTTPException(503, "IBKR not connected")
    return (await ibkr.get_account_summary()).model_dump()


@router.get("/api/positions")
async def get_positions():
    if cfg.SIM_MODE:
        positions = await sim_engine.get_positions()
        return [p.model_dump() for p in positions]
    if not ibkr.is_connected():
        raise HTTPException(503, "IBKR not connected")
    return [p.model_dump() for p in await ibkr.get_positions()]


@router.get("/api/positions/summary")
async def get_positions_summary():
    """EOD summary: reasoning for each position."""
    if not ibkr.is_connected():
        raise HTTPException(503, "IBKR not connected")

    positions = await ibkr.get_positions()
    trades = await get_trades(limit=500)
    acct = await ibkr.get_account_summary()

    trade_by_sym: dict[str, Any] = {}
    for t in trades:
        if t.action == "BUY" and t.status == "FILLED" and t.symbol not in trade_by_sym:
            trade_by_sym[t.symbol] = t

    bracket_orders: dict[str, dict] = {}
    for ib_trade in ibkr.ib.openTrades():
        sym = ib_trade.contract.symbol
        if sym not in bracket_orders:
            bracket_orders[sym] = {}
        if ib_trade.order.orderType == "STP":
            bracket_orders[sym]["sl"] = ib_trade.order.auxPrice
        elif ib_trade.order.orderType == "LMT" and ib_trade.order.action == "SELL":
            bracket_orders[sym]["tp"] = ib_trade.order.lmtPrice

    summaries = []
    for pos in positions:
        sym = pos.symbol
        entry_trade = trade_by_sym.get(sym)
        entry_date = entry_trade.timestamp if entry_trade else None
        rule_name = entry_trade.rule_name if entry_trade else "Unknown"

        hold_days = 0
        if entry_date:
            try:
                from datetime import datetime as dt
                entry_dt = dt.fromisoformat(entry_date.replace("Z", "+00:00"))
                hold_days = (dt.now(entry_dt.tzinfo) - entry_dt).days
            except Exception:
                pass

        brackets = bracket_orders.get(sym, {})
        pnl = pos.unrealized_pnl
        pnl_pct = ((pos.market_price / pos.avg_cost) - 1) * 100 if pos.avg_cost > 0 else 0

        summaries.append({
            "symbol": sym, "entry_date": entry_date, "hold_time_days": hold_days,
            "qty": pos.qty, "avg_cost": round(pos.avg_cost, 2),
            "current_price": round(pos.market_price, 2),
            "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
            "rule_trigger": rule_name,
            "sl_price": brackets.get("sl"), "tp_price": brackets.get("tp"),
            "pct_of_account": round(abs(pos.market_value) / acct.balance * 100, 2) if acct.balance > 0 else 0,
        })

    return {"positions_summary": summaries, "account": acct.model_dump()}


@router.get("/api/positions/tracked")
async def get_tracked_positions():
    """Open positions monitored by the exit manager, enriched with live stop levels."""
    from database import get_open_positions
    from position_tracker import compute_trail_stop
    from market_data import get_latest_price, get_historical_bars
    from indicators import _atr

    positions = await get_open_positions()
    result = []
    for pos in positions:
        try:
            price = await get_latest_price(pos.symbol) or pos.entry_price
            df = await get_historical_bars(pos.symbol, "60 D", "1D")
            if df is not None and len(df) >= 14:
                current_atr = float(_atr(df["high"], df["low"], df["close"], 14).iloc[-1])
            else:
                current_atr = pos.atr_at_entry
            trail_stop = compute_trail_stop(pos, current_atr)
            effective_stop = max(pos.hard_stop_price, trail_stop)
        except Exception:
            price = pos.entry_price
            trail_stop = pos.hard_stop_price
            effective_stop = pos.hard_stop_price

        result.append({
            **pos.model_dump(),
            "current_price": round(price, 4),
            "trail_stop_price": round(trail_stop, 4),
            "effective_stop_price": round(effective_stop, 4),
            "unrealized_pnl": round((price - pos.entry_price) * pos.quantity, 2),
            "unrealized_pct": round(((price - pos.entry_price) / pos.entry_price) * 100, 2),
        })
    return result
