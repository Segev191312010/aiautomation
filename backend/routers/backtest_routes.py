"""Backtest routes — /api/backtest/*"""
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from database import save_backtest, get_backtests, get_backtest, delete_backtest
from models import BacktestRequest, BacktestSaveRequest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backtest", tags=["backtest"], dependencies=[Depends(get_current_user)])


@router.post("/run")
async def api_backtest_run(req: BacktestRequest):
    """Run a backtest and return results. Does NOT save automatically."""
    from backtester import run_backtest
    try:
        result = await run_backtest(
            entry_conditions=req.entry_conditions,
            exit_conditions=req.exit_conditions,
            symbol=req.symbol.upper(),
            period=req.period,
            interval=req.interval,
            initial_capital=req.initial_capital,
            position_size_pct=req.position_size_pct,
            stop_loss_pct=req.stop_loss_pct,
            take_profit_pct=req.take_profit_pct,
            condition_logic=req.condition_logic,
            exit_mode=req.exit_mode,
            atr_stop_mult=req.atr_stop_mult,
            atr_trail_mult=req.atr_trail_mult,
            start_date=req.start_date,
            end_date=req.end_date,
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.error("Backtest failed: %s", e, exc_info=True)
        raise HTTPException(500, "Internal error during backtest execution")


@router.post("/save")
async def api_backtest_save(req: BacktestSaveRequest, user=Depends(get_current_user)):
    """Save a backtest result for later retrieval."""
    created_at = datetime.now(timezone.utc).isoformat()
    strategy_data = json.dumps({
        "entry_conditions": [c.model_dump() for c in req.result.entry_conditions],
        "exit_conditions": [c.model_dump() for c in req.result.exit_conditions],
        "condition_logic": req.result.condition_logic,
        "position_size_pct": req.result.position_size_pct,
        "stop_loss_pct": req.result.stop_loss_pct,
        "take_profit_pct": req.result.take_profit_pct,
    })
    result_data = req.result.model_dump_json()
    backtest_id = str(uuid.uuid4())
    await save_backtest(
        backtest_id=backtest_id,
        user_id=user.id,
        name=req.name,
        strategy_data=strategy_data,
        result_data=result_data,
        created_at=created_at,
    )
    return {"id": backtest_id, "saved": True}


@router.get("/history")
async def api_backtest_history(user=Depends(get_current_user)):
    """List saved backtests."""
    return await get_backtests(user_id=user.id)


@router.get("/{backtest_id}")
async def api_backtest_get(backtest_id: str):
    """Retrieve a specific saved backtest."""
    result = await get_backtest(backtest_id)
    if not result:
        raise HTTPException(404, "Backtest not found")
    return result


@router.delete("/{backtest_id}")
async def api_backtest_delete(backtest_id: str):
    """Delete a saved backtest."""
    deleted = await delete_backtest(backtest_id)
    return {"deleted": deleted}
