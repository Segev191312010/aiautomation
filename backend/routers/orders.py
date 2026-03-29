"""Order routes — /api/orders/*"""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import cfg
from market_data import get_latest_price
from models import Rule, TradeAction
from order_executor import OrderError, cancel_order, get_open_orders, place_order

router = APIRouter(prefix="/api/orders", tags=["orders"])


class ManualOrderRequest(BaseModel):
    symbol: str
    action: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0)
    order_type: Literal["MKT", "LMT"] = "MKT"
    limit_price: float | None = None
    asset_type: Literal["STK", "OPT", "FUT"] = "STK"


@router.get("")
async def get_orders():
    return await get_open_orders()


@router.delete("/{order_id}")
async def cancel_order_route(order_id: int):
    ok = await cancel_order(order_id)
    if not ok:
        raise HTTPException(404, "Order not found")
    return {"cancelled": True}


@router.post("/manual", status_code=201)
async def place_manual_order(body: ManualOrderRequest):
    """Place a manual order — routes to sim if SIM_MODE, else IBKR."""
    if cfg.SIM_MODE:
        from simulation import sim_engine
        sym = body.symbol.upper()
        price = await get_latest_price(sym)
        if price is None:
            # Yahoo fallback
            try:
                from yahoo_data import yf_quotes
                quotes = await yf_quotes(sym, source="sim_order_price")
                if quotes and quotes[0].get("price"):
                    price = quotes[0]["price"]
            except Exception:
                pass
        if price is None:
            raise HTTPException(503, "No market data available for " + sym)
        ok, msg = await sim_engine.execute_order(
            symbol=sym, action=body.action, qty=float(body.quantity), price=price,
        )
        if not ok:
            raise HTTPException(400, msg)
        return {"success": True, "message": msg, "sim": True}

    from ibkr_client import ibkr
    if not ibkr.is_connected():
        raise HTTPException(503, "IBKR not connected — start IB Gateway first")

    rule = Rule(
        name="Manual", symbol=body.symbol.upper(), enabled=True, conditions=[],
        action=TradeAction(
            type=body.action, asset_type=body.asset_type,
            quantity=body.quantity, order_type=body.order_type,
            limit_price=body.limit_price,
        ),
        cooldown_minutes=0,
    )
    try:
        trade = await place_order(rule, source="manual", require_autopilot_authority=False)
    except OrderError as exc:
        raise HTTPException(400, str(exc))
    if not trade:
        raise HTTPException(502, "Order placement failed — check IBKR logs")
    return trade.model_dump()

