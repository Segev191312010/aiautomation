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

# Bar cache — reused across cycles, refreshed every 5 minutes
_bars_cache: dict = {}
_bars_cache_ts: float = 0
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


_peak_equity: float = 0.0

async def _run_cycle() -> None:
    global _peak_equity
    from zoneinfo import ZoneInfo
    from datetime import time as dtime
    from risk_manager import get_account_state, check_drawdown_live, check_daily_loss, check_trade_risk, get_sector
    from order_executor import update_positions_snapshot
    from rule_engine import clear_indicator_cache

    # ── Market hours info (log only, don't skip — user wants 24/7 scanning) ──
    now_et = datetime.now(ZoneInfo("America/New_York"))
    market_open = now_et.weekday() < 5 and dtime(9, 30) <= now_et.time() <= dtime(16, 0)

    # ── Account state (one IBKR query per cycle) ─────────────────────────────
    from ibkr_client import ibkr
    if ibkr.is_connected():
        try:
            summary = await ibkr.get_account_summary()
            eq = summary.balance
            ca = summary.cash
            log.info("Account: equity=%.2f cash=%.2f", eq, ca)
            if eq <= 0:
                # Fallback: try accountValues directly
                for av in ibkr.ib.accountValues():
                    if av.tag == "NetLiquidation" and av.currency == "USD":
                        eq = float(av.value)
                    elif av.tag == "TotalCashValue" and av.currency == "USD":
                        ca = float(av.value)
                log.info("Account fallback: equity=%.2f cash=%.2f", eq, ca)
            acct = {
                "equity": eq if eq > 0 else 5000,  # safety fallback
                "cash": ca if ca > 0 else 2000,
                "daily_pnl": summary.realized_pnl,
                "positions": [{"symbol": p.contract.symbol, "qty": p.position, "avg_cost": p.avgCost,
                                "market_price": p.avgCost, "sector": get_sector(p.contract.symbol)}
                               for p in ibkr.ib.positions() if p.position != 0],
            }
        except Exception as e:
            log.warning("Account fetch failed: %s — using $5000 fallback", e)
            acct = {"equity": 5000, "cash": 2000, "daily_pnl": 0, "positions": []}
    else:
        acct = {"equity": 0, "cash": 0, "daily_pnl": 0, "positions": []}
    equity = acct["equity"]

    # Update positions snapshot for cash-only guard in order_executor
    update_positions_snapshot(acct["positions"])

    # ── Peak equity + drawdown circuit breaker ────────────────────────────────
    if equity > _peak_equity:
        _peak_equity = equity
    if _peak_equity > 0 and check_drawdown_live(equity, _peak_equity):
        log.critical("DRAWDOWN BREAKER: equity=%.2f peak=%.2f (%.1f%%) — PAUSING BOT",
                     equity, _peak_equity, ((_peak_equity - equity) / _peak_equity) * 100)
        await _emit({"type": "bot", "status": "paused_drawdown"})
        await stop()
        return

    # ── Daily loss cap (>3% → no new BUYs, but still manage exits) ────────────
    daily_loss_hit = check_daily_loss(acct["daily_pnl"], equity)
    if daily_loss_hit:
        log.warning("DAILY LOSS CAP: pnl=%.2f — entry signals disabled", acct["daily_pnl"])

    # ── Clear indicator cache ─────────────────────────────────────────────────
    clear_indicator_cache()

    rules = await get_rules()
    enabled = [r for r in rules if r.enabled]

    if not enabled:
        log.debug("No enabled rules — skipping cycle")
        await _emit({"type": "bot", "status": "running", "rules_enabled": 0, "rules_checked": 0, "signals": 0})
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

    # ── Fetch bars (batched yfinance download, cached 5 min) ────────────────────
    import time as _time
    global _bars_cache, _bars_cache_ts
    symbol_list = sorted(all_symbols)
    cache_age = _time.time() - _bars_cache_ts if _bars_cache_ts else 999
    if _bars_cache and cache_age < 300:  # reuse cache if < 5 min old
        bars_by_symbol = _bars_cache
        log.info("Using cached bars (%d symbols, %.0fs old)", len(bars_by_symbol), cache_age)
    else:
        clear_bar_cache()

        # Use IBKR for bars if connected, yfinance as fallback
        from ibkr_client import ibkr as _ibkr
        if _ibkr.is_connected():
            log.info("Fetching bars via IBKR for %d symbols...", len(symbol_list))
            sem = asyncio.Semaphore(20)
            async def _fetch_ibkr(sym):
                async with sem:
                    try:
                        df = await get_historical_bars(sym, duration="90 D", bar_size="1 day")
                        return sym, df
                    except Exception:
                        return sym, None
            results = await asyncio.gather(*[_fetch_ibkr(s) for s in symbol_list])
            bars_by_symbol = {s: df for s, df in results if df is not None and len(df) >= 20}
        else:
            log.info("IBKR not connected — falling back to yfinance batch")
            def _batch_download():
                import yfinance as yf
                try:
                    raw = yf.download(symbol_list, period="90d", interval="1d",
                                      group_by="ticker", progress=False, threads=True)
                    result = {}
                    for sym in symbol_list:
                        try:
                            df = raw.copy() if len(symbol_list) == 1 else raw[sym].copy()
                            df = df.dropna(subset=["Close"])
                            if df.empty or len(df) < 20:
                                continue
                            df.columns = [c.lower() for c in df.columns]
                            df = df.reset_index()
                            if "Date" in df.columns:
                                df = df.rename(columns={"Date": "date"})
                            elif "Datetime" in df.columns:
                                df = df.rename(columns={"Datetime": "date"})
                            result[sym] = df
                        except Exception:
                            continue
                    return result
                except Exception as e:
                    log.error("Batch download failed: %s", e)
                    return {}
            bars_by_symbol = await asyncio.to_thread(_batch_download)

        log.info("Fetched bars for %d / %d symbols", len(bars_by_symbol), len(symbol_list))
        _bars_cache = bars_by_symbol
        _bars_cache_ts = _time.time()

    # ── Evaluate rules ────────────────────────────────────────────────────────
    triggered = evaluate_all(enabled, bars_by_symbol, universe_cache)

    # ── Run custom 9 scans (21EMA, Pocket Pivot, VCS, etc.) ──────────────────
    try:
        from custom_indicators import run_all_scans
        from models import Rule, Condition, TradeAction
        scan_hits = 0
        for sym, df in bars_by_symbol.items():
            matches = run_all_scans(df)
            if matches:
                scan_hits += 1
                # Create a synthetic BUY rule for each scan match
                for scan_name in matches:
                    fake_rule = Rule(
                        name=f"Scan: {scan_name}",
                        symbol=sym,
                        enabled=True,
                        conditions=[Condition(indicator="PRICE", params={}, operator=">", value=0)],
                        action=TradeAction(type="BUY", quantity=1, order_type="MKT"),
                        cooldown_minutes=720,
                    )
                    triggered.append((fake_rule, sym))
        if scan_hits:
            log.info("Custom scans matched %d symbols", scan_hits)
    except Exception as exc:
        log.warning("Custom scan error: %s", exc)

    # ── Score and rank signals (regime-aware) ───────────────────────────────
    try:
        from signal_scorer import signal_scorer
        scored = []
        for rule, sym in triggered:
            if sym in bars_by_symbol:
                result = signal_scorer.score_signal(sym, bars_by_symbol[sym], rule.action.type,
                                                    bars_cache=bars_by_symbol)
                # Boost custom scan signals (they passed multi-factor filters)
                if rule.name.startswith("Scan: "):
                    result["composite_score"] = min(100, result["composite_score"] + 15)
                result["_rule"] = rule
                result["_symbol"] = sym
                scored.append(result)
        ranked = signal_scorer.rank_signals(scored, top_n=cfg.MAX_TRADES_PER_CYCLE, min_score=50)
        if ranked:
            log.info("Signals (regime-scored): %s", ", ".join(f"{r['symbol']}={r['composite_score']}" for r in ranked))
        triggered = [(r["_rule"], r["_symbol"]) for r in ranked]
    except Exception as exc:
        log.warning("Signal scoring failed, using unranked: %s", exc)

    # ── Execute triggered rules (dynamic sizing, risk-checked) ────────────────
    total_signals = 0
    orders_placed = 0
    available_cash = acct["cash"]
    log.info("Executing %d signals (max %d per cycle)...", len(triggered), cfg.MAX_TRADES_PER_CYCLE)
    for rule, trigger_symbol in triggered:
      try:
        total_signals += 1
        if orders_placed >= cfg.MAX_TRADES_PER_CYCLE:
            log.info("Max orders/cycle (%d) reached", cfg.MAX_TRADES_PER_CYCLE)
            break

        # Skip BUY if daily loss cap hit
        if daily_loss_hit and rule.action.type == "BUY":
            log.info("Skipping BUY %s — daily loss cap active", trigger_symbol)
            continue

        # For universe rules, set symbol on copy
        order_rule = rule.model_copy()
        if rule.universe:
            order_rule.symbol = trigger_symbol

        # Get real price from bars
        bars_df = bars_by_symbol.get(order_rule.symbol)
        if bars_df is not None and len(bars_df) > 0:
            current_price = float(bars_df["close"].iloc[-1])
        else:
            current_price = 100.0

        # Dynamic position sizing: 1% risk, ATR-based stop
        atr_val = signal_scorer._atr(bars_df, 14) if bars_df is not None else current_price * 0.02
        sl_price = current_price - 2.0 * atr_val
        from risk_manager import calculate_position_size
        size_result = calculate_position_size(current_price, sl_price, equity, cfg.RISK_PER_TRADE_PCT)
        qty = size_result["shares"]
        if qty < 1:
            log.info("Skip %s: cannot size (price=%.2f, atr=%.2f, equity=%.0f)", order_rule.symbol, current_price, atr_val, equity)
            continue
        order_rule.action.quantity = qty

        # Cash check
        order_cost = qty * current_price + 1.0
        if order_cost > available_cash:
            log.info("Insufficient cash for %s ($%.2f needed, $%.2f available)", order_rule.symbol, order_cost, available_cash)
            break

        # Risk check (sector, position count, duplicate, cash-only)
        risk_result = check_trade_risk(
            order_rule.symbol, qty, order_rule.action.type,
            acct["positions"], equity, est_price=current_price,
        )
        if risk_result.status == "BLOCK":
            log.warning("Risk BLOCKED %s %s: %s", order_rule.action.type, order_rule.symbol, risk_result.reasons)
            continue

        try:
            trade = await place_order(order_rule, bars=bars_df)
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
      except Exception as exc:
        log.error("Execution error for %s: %s", trigger_symbol, exc)

    log.info("Cycle done: %d signals, %d orders placed", total_signals, orders_placed)
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
