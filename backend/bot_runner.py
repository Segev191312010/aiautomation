"""
Bot runner — main async loop.

Every BOT_INTERVAL_SECONDS:
  1. Clear bar cache
  2. Expand universe rules into symbol lists
  3. Fetch bars for all required symbols
  4. Evaluate all rules (single-symbol and universe)
  5. Execute triggered rules
  6. Update last_triggered / symbol_cooldowns
  7. Broadcast status event via WebSocket
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
from order_executor import OrderError, place_order
from models import Rule
from screener import load_universe

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


def _expand_universe(universe_id: str) -> list[str]:
    """Expand a universe identifier to a list of symbols."""
    if universe_id == "all":
        symbols: set[str] = set()
        for uid in ("sp500", "nasdaq100", "etfs"):
            symbols.update(load_universe(uid))
        return sorted(symbols)
    return load_universe(universe_id)


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

    # ── Collect all symbols needed ────────────────────────────────────────────
    # Single-symbol rules: just use rule.symbol
    # Universe rules: expand the universe to its symbol list
    all_symbols: set[str] = set()
    universe_cache: dict[str, list[str]] = {}  # universe_id -> [symbols]

    for r in enabled:
        if r.universe:
            if r.universe not in universe_cache:
                universe_cache[r.universe] = _expand_universe(r.universe)
            all_symbols.update(s.upper() for s in universe_cache[r.universe])
        elif r.symbol:
            all_symbols.add(r.symbol.upper())

    log.info(
        "Cycle: %d rules (%d single-symbol, %d universe), %d unique symbols to fetch",
        len(enabled),
        sum(1 for r in enabled if r.symbol),
        sum(1 for r in enabled if r.universe),
        len(all_symbols),
    )

    # ── Fetch bars ────────────────────────────────────────────────────────────
    clear_bar_cache()
    bars_by_symbol: dict = {}
    sem = asyncio.Semaphore(15)

    async def _fetch_one(sym: str):
        async with sem:
            try:
                return sym, await get_historical_bars(sym, duration="60 D", bar_size="1D")
            except Exception as exc:
                log.error("Failed to fetch bars for %s: %s", sym, exc)
                return sym, None

    # Fetch in batches to avoid overwhelming the event loop for 500+ symbols
    symbol_list = sorted(all_symbols)
    results = await asyncio.gather(*[_fetch_one(s) for s in symbol_list])
    for sym, df in results:
        if df is not None:
            bars_by_symbol[sym] = df

    log.info("Fetched bars for %d / %d symbols", len(bars_by_symbol), len(all_symbols))

    # ── Evaluate rules ────────────────────────────────────────────────────────
    triggered = evaluate_all(enabled, bars_by_symbol, universe_cache)

    # ── Score and rank signals ───────────────────────────────────────────────
    try:
        from signal_scorer import signal_scorer
        scored = []
        for rule, sym in triggered:
            if sym in bars_by_symbol:
                result = signal_scorer.score_signal(sym, bars_by_symbol[sym], rule.action.type)
                result["_rule"] = rule
                result["_symbol"] = sym
                scored.append(result)
        ranked = signal_scorer.rank_signals(scored, top_n=5, min_score=50)
        if ranked:
            log.info("Signal scores: %s", ", ".join(f"{r['symbol']}={r['composite_score']}" for r in ranked))
        triggered = [(r["_rule"], r["_symbol"]) for r in ranked]
    except Exception as exc:
        log.warning("Signal scoring failed, using unranked: %s", exc)

    # ── Execute triggered rules ───────────────────────────────────────────────
    total_signals = 0
    orders_placed = 0
    max_orders_per_cycle = 5
    for rule, trigger_symbol in triggered:
        total_signals += 1
        if orders_placed >= max_orders_per_cycle:
            log.info("Max orders per cycle (%d) reached — deferring remaining signals", max_orders_per_cycle)
            break

        # For universe rules, set the symbol on the rule copy for order placement
        order_rule = rule.model_copy()
        if rule.universe:
            order_rule.symbol = trigger_symbol

        # Cash check — skip if we can't afford it
        available_cash = 0
        try:
            from ibkr_client import ibkr
            acct = await ibkr.get_account_summary()
            available_cash = acct.get('AvailableFunds', 0) if acct else 0
            if isinstance(available_cash, str):
                available_cash = float(available_cash)
            if available_cash < 100:  # minimum $100 to place an order
                log.warning("Insufficient cash ($%.2f) — skipping remaining signals", available_cash)
                break
        except Exception:
            pass  # If we can't check, proceed anyway

        # Risk check
        try:
            from risk_manager import check_trade_risk
            from risk_config import DEFAULT_LIMITS
            positions = []
            try:
                positions = [p.__dict__ if hasattr(p, '__dict__') else p for p in (await ibkr.get_positions() or [])]
            except Exception:
                pass
            risk_result = check_trade_risk(
                order_rule.symbol, order_rule.action.quantity,
                order_rule.action.type, positions,
                available_cash, DEFAULT_LIMITS
            )
            if risk_result.status == "BLOCK":
                log.warning("Risk BLOCKED %s %s: %s", order_rule.action.type, order_rule.symbol, risk_result.reasons)
                continue
        except Exception as e:
            log.debug("Risk check skipped: %s", e)

        try:
            trade = await place_order(order_rule)
            orders_placed += 1
        except OrderError as exc:
            log.error("Order failed for rule '%s' on %s: %s", rule.name, trigger_symbol, exc)
            trade = None

        # Update cooldown tracking
        now_iso = datetime.now(timezone.utc).isoformat()
        if rule.universe:
            # Per-symbol cooldown for universe rules
            rule.symbol_cooldowns[trigger_symbol] = now_iso
        else:
            rule.last_triggered = now_iso
        await save_rule(rule)

        # Notify via WebSocket
        try:
            from notification_service import notification_service
            await notification_service.notify_signal({
                "rule_name": rule.name,
                "symbol": trigger_symbol,
                "action": rule.action.type,
                "qty": rule.action.quantity,
            })
        except Exception:
            pass

        if trade:
            await _emit({
                "type": "signal",
                "rule_id": rule.id,
                "rule_name": rule.name,
                "symbol": trigger_symbol,
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
        "symbols_scanned": len(bars_by_symbol),
        "signals": total_signals,
        "last_run": _last_run,
        "next_run": _next_run,
    })

    log.info(
        "Cycle complete — %d rules, %d symbols scanned, %d signals",
        len(enabled), len(bars_by_symbol), total_signals,
    )


async def _emit(payload: dict) -> None:
    if _broadcast:
        await _broadcast(payload)
