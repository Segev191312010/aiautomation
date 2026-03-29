"""Bot control + trade log routes — /api/bot/*, /api/trades"""
from fastapi import APIRouter

import bot_runner
from database import get_trades

router = APIRouter(tags=["bot"])


@router.get("/api/trades")
async def get_trade_log(limit: int = 200):
    trades = await get_trades(limit)
    return [t.model_dump() for t in trades]


@router.post("/api/bot/start")
async def start_bot():
    await bot_runner.start()
    return {"running": True}


@router.post("/api/bot/stop")
async def stop_bot():
    await bot_runner.stop()
    return {"running": False}


@router.get("/api/bot/status")
async def bot_status_route():
    return {
        "running": bot_runner.is_running(),
        "last_run": bot_runner.get_last_run(),
        "next_run": bot_runner.get_next_run(),
    }
