"""Simulation & replay routes — /api/simulation/*"""
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import get_current_user
from config import cfg
from simulation import replay_engine, sim_engine

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/simulation",
    tags=["simulation"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/account")
async def sim_account():
    positions = await sim_engine.get_positions()
    account = await sim_engine.get_account(positions)
    return account.model_dump()


@router.get("/positions")
async def sim_positions():
    positions = await sim_engine.get_positions()
    return [p.model_dump() for p in positions]


@router.get("/orders")
async def sim_orders(limit: int = 100):
    orders = await sim_engine.get_orders(limit)
    return [o.model_dump() for o in orders]


class SimOrderRequest(BaseModel):
    symbol: str
    action: Literal["BUY", "SELL"]
    qty: float = Field(gt=0)
    price: float = Field(gt=0)


@router.post("/order", status_code=201)
async def sim_place_order(body: SimOrderRequest):
    ok, msg = await sim_engine.execute_order(
        symbol=body.symbol.upper(), action=body.action, qty=body.qty, price=body.price,
    )
    if not ok:
        raise HTTPException(400, msg)
    return {"success": True, "message": msg}


@router.post("/reset")
async def sim_reset():
    await sim_engine.reset()
    return {"reset": True, "initial_cash": cfg.SIM_INITIAL_CASH}


@router.get("/playback")
async def playback_state():
    return replay_engine.state.model_dump()


class LoadReplayRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10, pattern=r'^[A-Za-z0-9.\-]+$')
    period: Literal["1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "max"] = "1y"
    interval: Literal["1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo"] = "1d"


@router.post("/playback/load")
async def playback_load(body: LoadReplayRequest):
    sym = body.symbol.upper()
    bars: list[dict] = []
    try:
        from yahoo_data import yf_bars
        bars = await yf_bars(sym, body.period, body.interval)
    except Exception as exc:
        log.warning("Yahoo bars failed for replay (%s): %s", sym, exc)

    if not bars:
        raise HTTPException(404, f"No replay data for {sym}")

    await replay_engine.load(sym, bars)
    return replay_engine.state.model_dump()


@router.post("/playback/play")
async def playback_play():
    await replay_engine.play()
    return replay_engine.state.model_dump()


@router.post("/playback/pause")
async def playback_pause():
    await replay_engine.pause()
    return replay_engine.state.model_dump()


@router.post("/playback/stop")
async def playback_stop():
    await replay_engine.stop()
    return replay_engine.state.model_dump()


class SpeedRequest(BaseModel):
    speed: int = Field(ge=1, le=100)


@router.post("/playback/speed")
async def playback_speed(body: SpeedRequest):
    replay_engine.set_speed(body.speed)
    return {"speed": replay_engine.state.speed}
