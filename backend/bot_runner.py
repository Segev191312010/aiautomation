"""
Bot runner — main async loop.

Every BOT_INTERVAL_SECONDS:
  1. Clear bar cache
  2. Fetch bars for all symbols referenced by enabled rules
  3. Evaluate all rules
  4. Execute triggered rules
  5. Update last_triggered timestamp
  6. Broadcast status event via WebSocket
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional
from config import cfg
from database import get_rules, save_rule, get_trades
from market_data import get_historical_bars, clear_bar_cache
from rule_engine import evaluate_all
from order_executor import place_order
from models import Rule

log = logging.getLogger(__name__)

# WebSocket broadcast callback — set by main.py
_broadcast: Optional[Callable] = None

_running = False
_task: Optional[asyncio.Task] = None
_last_run: Optional[str] = None
_next_run: Optional[str] = None


def set_broadcast(cb: Callable) -> None:
    global _broadcast
    _broadcast = cb


def is_running() -> bool:
    return _running


def get_last_run() -> Optional[str]:
    return _last_run


def get_next_run() -> Optional[str]:
    return _next_run


async def start() -> None:
    global _running, _task
    if _running:
        return
    _running = True
    _task = asyncio.create_task(_loop())
    log.info("Bot runner started (interval=%ds)", cfg.BOT_INTERVAL_SECONDS)


async def stop() -> None:
    global _running, _task
    _running = False
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    log.info("Bot runner stopped")


async def _loop() -> None:
    global _last_run, _next_run
    while _running:
        _last_run = datetime.now(timezone.utc).isoformat()
        try:
            await _run_cycle()
        except Exception as exc:
            log.exception("Error in bot cycle: %s", exc)
            await _emit({"type": "error", "message": str(exc)})

        # Schedule next run
        next_dt = datetime.now(timezone.utc).timestamp() + cfg.BOT_INTERVAL_SECONDS
        _next_run = datetime.fromtimestamp(next_dt, tz=timezone.utc).isoformat()
        await asyncio.sleep(cfg.BOT_INTERVAL_SECONDS)


async def _run_cycle() -> None:
    rules = await get_rules()
    enabled = [r for r in rules if r.enabled]

    if not enabled:
        log.debug("No enabled rules — skipping cycle")
        await _emit({
            "type": "bot",
            "status": "running",
            "rules_enabled": 0,
            "rules_checked": 0,
            "signals": 0,
        })
        return

    # Collect unique symbols
    symbols = list({r.symbol.upper() for r in enabled})

    # Fetch bars (clear cache first to get fresh data)
    clear_bar_cache()
    bars_by_symbol: dict = {}
    sem = asyncio.Semaphore(10)

    async def _fetch_one(sym: str):
        async with sem:
            try:
                return sym, await get_historical_bars(sym, duration="60 D", bar_size="1D")
            except Exception as exc:
                log.error("Failed to fetch bars for %s: %s", sym, exc)
                return sym, None

    results = await asyncio.gather(*[_fetch_one(s) for s in symbols])
    for sym, df in results:
        if df is not None:
            bars_by_symbol[sym] = df

    # Evaluate rules
    triggered = evaluate_all(enabled, bars_by_symbol)

    # Execute triggered rules
    for rule in triggered:
        trade = await place_order(rule)

        # Update rule's last_triggered timestamp
        rule.last_triggered = datetime.now(timezone.utc).isoformat()
        await save_rule(rule)

        if trade:
            await _emit({
                "type": "signal",
                "rule_id": rule.id,
                "rule_name": rule.name,
                "symbol": rule.symbol,
                "action": rule.action.type,
                "qty": rule.action.quantity,
                "trade_id": trade.id,
                "order_id": trade.order_id,
            })

    await _emit({
        "type": "bot",
        "status": "running",
        "rules_enabled": len(enabled),
        "rules_checked": len(enabled),
        "signals": len(triggered),
        "last_run": _last_run,
        "next_run": _next_run,
    })

    log.info("Cycle complete — %d rules checked, %d signals", len(enabled), len(triggered))


async def _emit(payload: dict) -> None:
    if _broadcast:
        await _broadcast(payload)
