"""Order routes — /api/orders/*"""
import math
from numbers import Real

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from config import cfg
from manual_order_validation import (
    ManualOrderPolicyError,
    ManualOrderRequest,
    validate_manual_order_notional,
)
from market_data import get_latest_price
from models import Rule, TradeAction
from order_executor import OrderError, cancel_order, get_open_orders, place_order

router = APIRouter(
    prefix="/api/orders",
    tags=["orders"],
    dependencies=[Depends(get_current_user)],
)

_MANUAL_MARKET_SLIPPAGE = 0.005


def _positive_finite_price(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    price = float(value)
    return price if math.isfinite(price) and price > 0 else None


async def _get_manual_order_price(symbol: str) -> float | None:
    """Resolve a finite positive quote, returning None when every source fails."""
    try:
        price = _positive_finite_price(await get_latest_price(symbol))
    except Exception:
        price = None
    if price is not None:
        return price

    try:
        from yahoo_data import yf_quotes

        quotes = await yf_quotes(symbol, source="manual_order_price")
        if quotes:
            return _positive_finite_price(quotes[0].get("price"))
    except Exception:
        pass
    return None


def _enforce_manual_order_notional(quantity: int, price: float | None) -> None:
    try:
        validate_manual_order_notional(quantity, price)
    except ManualOrderPolicyError as exc:
        raise HTTPException(422, str(exc)) from exc


def _bounded_market_limit_price(reference_price: float, action: str) -> float:
    """Convert a manual market request into a cent-rounded protective limit."""
    multiplier = 1 + _MANUAL_MARKET_SLIPPAGE if action == "BUY" else 1 - _MANUAL_MARKET_SLIPPAGE
    raw = reference_price * multiplier
    cents = math.ceil(raw * 100) if action == "BUY" else math.floor(raw * 100)
    price = cents / 100
    if price <= 0:
        raise HTTPException(422, "Unable to derive a positive protective limit price")
    return price


async def _require_long_position_for_manual_sell(symbol: str, quantity: int) -> None:
    from ibkr_client import ibkr

    try:
        positions = await ibkr.get_positions()
        if not isinstance(positions, list):
            raise TypeError("position response is not a list")
        available = 0.0
        for position in positions:
            if position.symbol.upper() != symbol or position.asset_type != "STK":
                continue
            position_qty = float(position.qty)
            if not math.isfinite(position_qty):
                raise ValueError("position quantity is not finite")
            if position_qty > 0:
                available += position_qty
    except Exception as exc:
        raise HTTPException(503, "Unable to verify existing position for SELL") from exc
    if available < quantity:
        raise HTTPException(
            422,
            f"Manual SELL quantity {quantity} exceeds verified long position {available:g}",
        )


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

        sym = body.symbol
        price = await _get_manual_order_price(sym)
        if price is None:
            raise HTTPException(503, "No market data available for " + sym)
        _enforce_manual_order_notional(body.quantity, price)
        if body.order_type == "LMT":
            assert body.limit_price is not None  # enforced by ManualOrderRequest
            limit_price = float(body.limit_price)
            marketable = price <= limit_price if body.action == "BUY" else price >= limit_price
            if not marketable:
                raise HTTPException(
                    409,
                    "Simulation does not queue resting limit orders; the submitted limit is not marketable",
                )
        ok, msg = await sim_engine.execute_order(
            symbol=sym, action=body.action, qty=float(body.quantity), price=price,
        )
        if not ok:
            raise HTTPException(400, msg)
        return {"success": True, "message": msg, "sim": True}

    from ibkr_client import ibkr
    if not ibkr.is_connected():
        raise HTTPException(503, "IBKR not connected — start IB Gateway first")

    execution_order_type = body.order_type
    execution_limit_price = body.limit_price
    reference_price = body.limit_price
    if body.order_type == "MKT":
        reference_price = await _get_manual_order_price(body.symbol)
        if reference_price is None:
            raise HTTPException(503, "No market data available for " + body.symbol)
        _enforce_manual_order_notional(body.quantity, reference_price)
        execution_limit_price = _bounded_market_limit_price(reference_price, body.action)
        execution_order_type = "LMT"
    _enforce_manual_order_notional(body.quantity, execution_limit_price)

    is_exit = body.action == "SELL"
    if is_exit:
        await _require_long_position_for_manual_sell(body.symbol, body.quantity)

    rule = Rule(
        name="Manual", symbol=body.symbol, enabled=True, conditions=[],
        action=TradeAction(
            type=body.action, asset_type=body.asset_type,
            quantity=body.quantity, order_type=execution_order_type,
            limit_price=execution_limit_price,
        ),
        cooldown_minutes=0,
    )
    try:
        trade = await place_order(
            rule,
            source="manual",
            require_autopilot_authority=False,
            is_exit=is_exit,
            has_existing_position=is_exit,
        )
    except OrderError as exc:
        raise HTTPException(400, str(exc))
    if not trade:
        raise HTTPException(502, "Order placement failed — check IBKR logs")
    return trade.model_dump()
