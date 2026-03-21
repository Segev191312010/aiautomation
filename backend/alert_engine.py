"""
Alert engine — async background loop that evaluates enabled alerts.

Every ALERT_CHECK_INTERVAL_SECONDS:
  1. Fetch all enabled alerts (all users)
  2. Collect unique symbols, batch-fetch current prices
  3. Evaluate each alert condition (PRICE or technical indicator)
  4. Fire triggered alerts: persist history, update state, broadcast WS event

# NOTE: Migrate to Celery/RQ if scanning >500 symbols in multi-user mode
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import pandas as pd

from config import cfg
from database import (
    get_enabled_alerts_all,
    save_alert,
    save_alert_history,
)
from market_data import get_historical_bars, get_latest_price
from models import Alert, AlertHistory, Condition
from rule_engine import _evaluate_condition

log = logging.getLogger(__name__)

# ── Module-level state ─────────────────────────────────────────────────────

_broadcast: Optional[Callable] = None
_running = False
_task: Optional[asyncio.Task] = None
_prev_prices: dict[str, float] = {}


# ── Public API ─────────────────────────────────────────────────────────────

def set_broadcast(cb: Callable) -> None:
    global _broadcast
    _broadcast = cb


def is_running() -> bool:
    return _running


async def start() -> None:
    """Start the alert engine loop. Idempotent — no-op if already running."""
    global _running, _task
    if _running:
        return
    _running = True
    _task = asyncio.create_task(_loop())
    log.info(
        "Alert engine started (interval=%ds)", cfg.ALERT_CHECK_INTERVAL_SECONDS
    )


async def stop() -> None:
    """Stop the alert engine loop. Idempotent — no-op if not running."""
    global _running, _task
    if not _running:
        return
    _running = False
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    log.info("Alert engine stopped")


# ── Main loop ──────────────────────────────────────────────────────────────

async def _loop() -> None:
    while _running:
        t0 = time.monotonic()
        try:
            await _check_cycle()
        except Exception:
            log.exception("Alert cycle error")
        duration = time.monotonic() - t0
        if duration > cfg.ALERT_CHECK_INTERVAL_SECONDS:
            log.warning(
                "Alert cycle took %.1fs, exceeds interval %ds",
                duration,
                cfg.ALERT_CHECK_INTERVAL_SECONDS,
            )
        # Sleep only the remainder — prevents timing drift
        remaining = max(0, cfg.ALERT_CHECK_INTERVAL_SECONDS - duration)
        await asyncio.sleep(remaining)


async def _check_cycle() -> None:
    """Run one evaluation cycle over all enabled alerts."""
    alerts = await get_enabled_alerts_all()
    if not alerts:
        return

    # Collect unique symbols
    symbols = list({a.symbol.upper() for a in alerts})

    # Batch-fetch prices: IBKR → Yahoo Finance
    prices: dict[str, float] = {}
    for sym in symbols:
        price = await get_latest_price(sym)
        if price is not None:
            prices[sym] = price

    # Bars cache for indicator-based alerts (lazy-loaded per symbol)
    bars_cache: dict[str, pd.DataFrame] = {}

    checked = len(alerts)
    fired = 0
    failed = 0

    for alert in alerts:
        try:
            did_fire = await _evaluate_alert(alert, prices, bars_cache)
            if did_fire:
                fired += 1
        except Exception as exc:
            failed += 1
            log.error("Alert %s eval failed: %s", alert.id, exc)

    # Update previous prices for cross detection
    for sym, price in prices.items():
        _prev_prices[sym] = price

    log.info(
        "Alert cycle: checked=%d symbols=%d fired=%d failed=%d",
        checked,
        len(symbols),
        fired,
        failed,
    )


async def _evaluate_alert(
    alert: Alert,
    prices: dict[str, float],
    bars_cache: dict[str, pd.DataFrame],
) -> bool:
    """Evaluate a single alert. Returns True if it fired."""
    sym = alert.symbol.upper()
    current_price = prices.get(sym)
    if current_price is None:
        return False

    # Cooldown check for recurring alerts
    if alert.alert_type == "recurring" and alert.last_triggered:
        last = datetime.fromisoformat(alert.last_triggered)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        cooldown_end = last + timedelta(minutes=alert.cooldown_minutes)
        if datetime.now(timezone.utc) < cooldown_end:
            return False

    cond = alert.condition
    triggered = False

    if cond.indicator == "PRICE":
        triggered = _evaluate_price_condition(cond, sym, current_price)
    else:
        # Technical indicator — needs DataFrame
        if sym not in bars_cache:
            try:
                bars_cache[sym] = await get_historical_bars(
                    sym, duration="60 D", bar_size="1D"
                )
            except Exception as exc:
                log.warning("Failed to fetch bars for %s: %s", sym, exc)
                bars_cache[sym] = pd.DataFrame()
        df = bars_cache[sym]
        if df is not None and not df.empty and len(df) >= 2:
            cache: dict = {}
            triggered = _evaluate_condition(cond, df, cache)

    if triggered:
        await _fire_alert(alert, current_price)
        return True

    return False


def _evaluate_price_condition(
    cond: Condition, sym: str, price: float
) -> bool:
    """Evaluate a PRICE condition using current + previous prices for crosses."""
    op = cond.operator.lower().strip()
    try:
        threshold = float(cond.value)
    except (ValueError, TypeError):
        return False

    prev = _prev_prices.get(sym)

    if op == "crosses_above":
        return prev is not None and prev <= threshold and price > threshold
    if op == "crosses_below":
        return prev is not None and prev >= threshold and price < threshold
    if op in (">", "gt"):
        return price > threshold
    if op in ("<", "lt"):
        return price < threshold
    if op in (">=", "gte"):
        return price >= threshold
    if op in ("<=", "lte"):
        return price <= threshold
    if op in ("==", "eq", "="):
        return abs(price - threshold) < 0.01

    log.warning("Unknown price operator: %s", op)
    return False


async def _fire_alert(alert: Alert, price: float) -> None:
    """Handle a triggered alert: persist state, log history, broadcast."""
    now = datetime.now(timezone.utc).isoformat()
    summary = _condition_summary(alert.condition)

    # Update alert state BEFORE broadcast (invariant: persist first)
    alert.last_triggered = now
    if alert.alert_type == "one_shot":
        alert.enabled = False
    await save_alert(alert, alert.user_id)

    # Log history
    hist = AlertHistory(
        alert_id=alert.id,
        alert_name=alert.name,
        symbol=alert.symbol,
        condition_summary=summary,
        price_at_trigger=price,
        fired_at=now,
    )
    await save_alert_history(hist, alert.user_id)

    # Broadcast via WebSocket
    await _emit(
        {
            "type": "alert_fired",
            "alert_id": alert.id,
            "name": alert.name,
            "symbol": alert.symbol,
            "condition_summary": summary,
            "price": price,
            "timestamp": now,
        }
    )

    log.info(
        "Alert FIRED: [%s] %s on %s @ %.2f", alert.id, summary, alert.symbol, price
    )


def _condition_summary(cond: Condition) -> str:
    """Human-readable condition string: e.g. 'RSI(14) < 30' or 'PRICE > 250.0'."""
    params_str = ", ".join(str(v) for v in cond.params.values()) if cond.params else ""
    ind = f"{cond.indicator}({params_str})" if params_str else cond.indicator
    return f"{ind} {cond.operator} {cond.value}"


async def _emit(payload: dict) -> None:
    """Broadcast a WebSocket event if broadcast callback is set."""
    if _broadcast:
        await _broadcast(payload)
