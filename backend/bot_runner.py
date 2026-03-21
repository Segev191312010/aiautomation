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
from database import get_rules, save_rule, get_trades, get_open_positions, save_open_position, delete_open_position
from market_data import get_historical_bars, clear_bar_cache, get_latest_price
from rule_engine import evaluate_all
from order_executor import OrderError, place_order
from models import Rule, TradeAction
from position_tracker import check_exits, update_watermarks
from screener import load_universe
from events import (
    EventBus, EventType, EventQueue,
    MarketEvent, SignalEvent, OrderEvent, FillEvent, RegimeEvent, MetricEvent,
    ibkr_bar_to_market_event,
)
from event_logger import EventLogger, MetricsCollector

log = logging.getLogger(__name__)

# ── Global event infrastructure ──────────────────────────────────────────────
event_bus = EventBus()
event_logger = EventLogger()
metrics = MetricsCollector()

# WebSocket broadcast callback — set by main.py
_broadcast: Optional[Callable] = None

_running = False
_task: Optional[asyncio.Task] = None
_last_run: Optional[str] = None
_next_run: Optional[str] = None

# ── Liquidity pre-screen cache ─────────────────────────────────────────────
# Rebuilt once per day from the full us_all universe.
# Filters: last close > $5, average 5-day volume > 500k shares.
_PRESCREEN_MIN_PRICE  = 5.0
_PRESCREEN_MIN_VOLUME = 500_000
_PRESCREEN_TTL        = 86_400          # refresh every 24 h
_screened_symbols: list[str] = []
_screened_at: float = 0.0               # epoch timestamp of last refresh


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
    # us_all and any other named universe load directly from their JSON file
    return load_universe(universe_id)


async def _prescreen_universe(candidates: list[str]) -> list[str]:
    """
    Rapidly filter a large symbol list down to liquid, in-range stocks.

    Downloads 5 days of daily bars for all candidates in one batch call,
    then keeps only symbols where:
      - last close  > _PRESCREEN_MIN_PRICE  (default $5  — filters penny stocks)
      - avg volume  > _PRESCREEN_MIN_VOLUME (default 500k — filters illiquid names)

    Results are cached for _PRESCREEN_TTL seconds (24 h by default).
    """
    global _screened_symbols, _screened_at
    import time as _time_mod
    import asyncio as _asyncio

    now = _time_mod.time()
    if _screened_symbols and (now - _screened_at) < _PRESCREEN_TTL:
        log.info("Pre-screen cache hit — %d liquid symbols", len(_screened_symbols))
        return _screened_symbols

    log.info("Pre-screening %d symbols (price>$%.0f, vol>%s) …",
             len(candidates), _PRESCREEN_MIN_PRICE,
             f"{_PRESCREEN_MIN_VOLUME:,}")

    try:
        import yfinance as yf
        import pandas as pd

        liquid: list[str] = []
        BATCH = 1000

        loop = _asyncio.get_running_loop()
        for i in range(0, len(candidates), BATCH):
            batch = candidates[i:i + BATCH]
            try:
                raw = await loop.run_in_executor(
                    None,
                    lambda b=batch: yf.download(
                        b, period="5d", interval="1d",
                        auto_adjust=True, progress=False,
                        group_by="ticker", threads=True,
                    )
                )
                if raw.empty:
                    continue

                if isinstance(raw.columns, pd.MultiIndex):
                    for sym in batch:
                        try:
                            sym_df = raw[sym].dropna(how="all")
                            if sym_df.empty:
                                continue
                            last_close  = float(sym_df["Close"].iloc[-1])
                            avg_volume  = float(sym_df["Volume"].mean())
                            if last_close >= _PRESCREEN_MIN_PRICE and avg_volume >= _PRESCREEN_MIN_VOLUME:
                                liquid.append(sym.upper())
                        except Exception:
                            pass
                else:
                    # Single symbol returned as flat df
                    sym = batch[0]
                    try:
                        last_close = float(raw["Close"].iloc[-1])
                        avg_volume = float(raw["Volume"].mean())
                        if last_close >= _PRESCREEN_MIN_PRICE and avg_volume >= _PRESCREEN_MIN_VOLUME:
                            liquid.append(sym.upper())
                    except Exception:
                        pass

                log.info("Pre-screen batch %d/%d done — %d liquid so far",
                         i // BATCH + 1, -(-len(candidates) // BATCH), len(liquid))
            except Exception as exc:
                log.warning("Pre-screen batch %d failed: %s", i // BATCH, exc)

        _screened_symbols = sorted(liquid)
        _screened_at = now
        log.info("Pre-screen complete: %d / %d symbols pass liquidity filter",
                 len(_screened_symbols), len(candidates))
        return _screened_symbols

    except Exception as exc:
        log.error("Pre-screen failed, falling back to full list: %s", exc)
        return candidates


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

        # Check if AI optimization is due
        try:
            from ai_optimizer import should_recompute, run_full_optimization
            if should_recompute():
                log.info("AI optimization due — running before cycle")
                await run_full_optimization()
        except Exception as exc:
            log.debug("AI optimization check skipped: %s", exc)

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

    # Load open positions BEFORE bar fetch so their symbols are included
    open_positions = await get_open_positions()

    # ── Collect all symbols needed ────────────────────────────────────────────
    # Single-symbol rules: just use rule.symbol
    # Universe rules: expand the universe to its symbol list
    # Open positions: always fetch bars so exit checks work
    all_symbols: set[str] = set()
    universe_cache: dict[str, list[str]] = {}  # universe_id -> [symbols]

    for r in enabled:
        if r.universe:
            if r.universe not in universe_cache:
                raw_list = _expand_universe(r.universe)
                # For large universes (us_all), pre-screen to liquid names only
                if len(raw_list) > 500:
                    raw_list = await _prescreen_universe(raw_list)
                universe_cache[r.universe] = raw_list
            all_symbols.update(s.upper() for s in universe_cache[r.universe])
        elif r.symbol:
            all_symbols.add(r.symbol.upper())

    for pos in open_positions:
        all_symbols.add(pos.symbol.upper())

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
    symbol_list = sorted(all_symbols)

    if len(symbol_list) > 50:
        # Bulk-fetch via yfinance.download() for large universes — single HTTP call
        # per batch of up to 1000 symbols, far fewer requests than one-by-one.
        log.info("Bulk fetching %d symbols via yfinance.download()", len(symbol_list))
        try:
            import yfinance as yf
            import pandas as pd

            BATCH = 800  # yfinance handles ~800 symbols per call reliably
            for i in range(0, len(symbol_list), BATCH):
                batch = symbol_list[i:i + BATCH]
                try:
                    raw = await asyncio.get_running_loop().run_in_executor(
                        None,
                        lambda b=batch: yf.download(
                            b, period="1y", interval="1d",
                            auto_adjust=True, progress=False,
                            group_by="ticker", threads=True,
                        )
                    )
                    if raw.empty:
                        continue
                    # When multiple tickers: columns are (field, symbol)
                    if isinstance(raw.columns, pd.MultiIndex):
                        for sym in batch:
                            try:
                                df = raw[sym].copy().dropna(how="all")
                                if df.empty or len(df) < 2:
                                    continue
                                df.index.name = "time"
                                df = df.reset_index()
                                df.columns = [c.lower() for c in df.columns]
                                # adj close rename removed — auto_adjust=True already handles this
                                bars_by_symbol[sym.upper()] = df
                            except Exception:
                                pass
                    else:
                        # Single ticker returned as flat df
                        sym = batch[0]
                        df = raw.copy().dropna(how="all")
                        if not df.empty and len(df) >= 2:
                            df.index.name = "time"
                            df = df.reset_index()
                            df.columns = [c.lower() for c in df.columns]
                            # adj close rename removed — auto_adjust=True already handles this
                            bars_by_symbol[sym.upper()] = df
                except Exception as exc:
                    log.warning("Bulk fetch batch %d failed: %s", i // BATCH, exc)
        except Exception as exc:
            log.error("yfinance bulk fetch failed: %s", exc)
    else:
        # Small symbol list — fetch individually (IBKR or yfinance fallback)
        sem = asyncio.Semaphore(15)

        async def _fetch_one(sym: str):
            async with sem:
                try:
                    return sym, await get_historical_bars(sym, duration="60 D", bar_size="1D")
                except Exception as exc:
                    log.error("Failed to fetch bars for %s: %s", sym, exc)
                    return sym, None

        results = await asyncio.gather(*[_fetch_one(s) for s in symbol_list])
        for sym, df in results:
            if df is not None:
                bars_by_symbol[sym] = df

    log.info("Fetched bars for %d / %d symbols", len(bars_by_symbol), len(all_symbols))

    # ── Phase 1: Process exits for tracked positions ──────────────────────────
    try:
        await _process_exits(open_positions, bars_by_symbol)
    except Exception as exc:
        log.exception("Exit processing failed: %s", exc)

    if not enabled:
        log.debug("No enabled rules — skipping entry evaluation")
        await _emit({
            "type": "bot",
            "status": "running",
            "rules_enabled": 0,
            "rules_checked": 0,
            "signals": 0,
        })
        return

    # ── Emit MarketEvents for all fetched bars ─────────────────────────────────
    now = datetime.now(timezone.utc)
    for sym, df in bars_by_symbol.items():
        if len(df) > 0:
            row = df.iloc[-1]
            me = MarketEvent(
                timestamp=now, type=EventType.MARKET, symbol=sym,
                open=float(row.get("open", 0)), high=float(row.get("high", 0)),
                low=float(row.get("low", 0)), close=float(row.get("close", 0)),
                volume=float(row.get("volume", 0)),
            )
            event_bus.publish(me)
            event_logger.log_event(me)
    metrics.record("bars_fetched", len(bars_by_symbol))

    # ── Phase 2: Evaluate entry rules ─────────────────────────────────────────
    triggered = evaluate_all(enabled, bars_by_symbol, universe_cache)

    # ── Score and rank signals ───────────────────────────────────────────────
    try:
        from signal_scorer import signal_scorer
        from ai_params import ai_params

        # Inject AI signal weights if available (scorer detects regime internally)
        signal_scorer.set_ai_weights(None)  # cleared; scorer will check ai_params per-regime

        scored = []
        for rule, sym in triggered:
            if sym in bars_by_symbol:
                result = signal_scorer.score_signal(sym, bars_by_symbol[sym], rule.action.type)
                result["_rule"] = rule
                result["_symbol"] = sym
                scored.append(result)
        ai_min_score = ai_params.get_min_score()
        ranked = signal_scorer.rank_signals(scored, top_n=5, min_score=ai_min_score)
        if ranked:
            log.info("Signal scores: %s", ", ".join(f"{r['symbol']}={r['composite_score']}" for r in ranked))
            # Emit SignalEvents
            for r in ranked:
                sig_event = SignalEvent(
                    timestamp=now, type=EventType.SIGNAL,
                    symbol=r["symbol"], rule_id=r["_rule"].id,
                    rule_name=r["_rule"].name,
                    signal_type="LONG" if r["_rule"].action.type == "BUY" else "EXIT",
                    strength=r["composite_score"] / 100.0,
                    raw_score=r["composite_score"],
                )
                event_bus.publish(sig_event)
                event_logger.log_event(sig_event)
            metrics.record("signals_scored", len(ranked))
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
        available_cash = 0.0
        try:
            if cfg.SIM_MODE:
                from simulation import sim_engine
                sim_acct = await sim_engine.get_account()
                available_cash = float(sim_acct.net_liquidation)
            else:
                from ibkr_client import ibkr
                acct = await ibkr.get_account_summary()
                available_cash = float(acct.balance) if acct else 0.0
            if available_cash < 100:
                log.warning("Insufficient cash ($%.2f) — skipping remaining signals", available_cash)
                break
        except Exception as exc:
            log.debug("Cash check failed, proceeding: %s", exc)

        # Risk check
        try:
            from risk_manager import check_trade_risk
            from risk_config import DEFAULT_LIMITS
            positions = []
            try:
                if not cfg.SIM_MODE:
                    positions = [p.__dict__ if hasattr(p, '__dict__') else p for p in (await ibkr.get_positions() or [])]
                else:
                    from simulation import sim_engine
                    positions = [p.model_dump() for p in await sim_engine.get_positions()]
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

        # ── Dynamic position sizing: 0.5% of account NetLiquidation ─────────
        try:
            sym_upper = order_rule.symbol.upper()
            if sym_upper in bars_by_symbol:
                price = float(bars_by_symbol[sym_upper]["close"].iloc[-1])
            else:
                price = await get_latest_price(sym_upper) or 0.0
            if price > 0:
                if cfg.SIM_MODE:
                    from simulation import sim_engine
                    account_val = (await sim_engine.get_account()).net_liquidation
                else:
                    from ibkr_client import ibkr as _ibkr
                    account_val = (await _ibkr.get_account_summary()).balance
                computed_qty = max(1, int(account_val * cfg.POSITION_SIZE_PCT / price))
                # Apply AI sizing multiplier for this rule
                ai_sizing = ai_params.get_rule_sizing_multiplier(order_rule.id)
                if ai_sizing != 1.0:
                    computed_qty = max(1, int(computed_qty * ai_sizing))
                order_rule = order_rule.model_copy()
                order_rule.action = order_rule.action.model_copy(
                    update={"quantity": computed_qty}
                )
                log.info(
                    "Sizing %s: $%.0f × %.1f%% / $%.4f = %d shares (ai_mult=%.2f)",
                    sym_upper, account_val, cfg.POSITION_SIZE_PCT * 100, price, computed_qty, ai_sizing,
                )
        except Exception as exc:
            log.warning("Position sizing failed, using rule quantity: %s", exc)

        # Emit OrderEvent
        order_event = OrderEvent(
            timestamp=now, type=EventType.ORDER,
            symbol=order_rule.symbol, order_type=order_rule.action.order_type,
            quantity=order_rule.action.quantity, direction="LONG" if order_rule.action.type == "BUY" else "SHORT",
            rule_id=rule.id,
        )
        event_bus.publish(order_event)
        event_logger.log_event(order_event)

        try:
            trade = await place_order(order_rule)
            orders_placed += 1
            # Emit FillEvent if trade was placed (PENDING or FILLED)
            if trade and trade.fill_price:
                fill_event = FillEvent(
                    timestamp=now, type=EventType.FILL,
                    symbol=trade.symbol, quantity=trade.quantity,
                    fill_price=trade.fill_price, commission=1.0,
                    direction="LONG" if trade.action == "BUY" else "SHORT",
                    rule_id=rule.id, order_id=trade.order_id,
                )
                event_bus.publish(fill_event)
                event_logger.log_event(fill_event)
                metrics.record("fill_price", trade.fill_price)
            metrics.record("orders_placed", orders_placed)
        except (OrderError, RuntimeError) as exc:
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

    # Emit cycle metrics
    metrics.record("cycle_signals", total_signals)
    metrics.record("cycle_orders", orders_placed)
    metrics.record("cycle_symbols", len(bars_by_symbol))
    metrics.record("event_bus_total", event_bus.event_count)
    metrics.record("event_log_total", event_logger.event_count)

    log.info(
        "Cycle complete — %d rules, %d symbols, %d signals, %d orders | events: %d logged",
        len(enabled), len(bars_by_symbol), total_signals, orders_placed, event_logger.event_count,
    )


MAX_EXIT_ATTEMPTS = 3
EXIT_PENDING_TIMEOUT = 90  # seconds


async def _process_exits(open_positions: list, bars_by_symbol: dict) -> None:
    """
    Hardened exit lifecycle — position NEVER deleted until exit is FILLED.

    Order of operations:
      1. Update watermarks
      2. Reconcile any pending exit orders (FILLED/CANCELLED/PENDING timeout)
      3. Evaluate positions for new exits (only if no pending exit)
      4. Retry cap: max 3 attempts, then notify for manual intervention
    """
    if not open_positions:
        return

    # Step 1: Update watermarks
    for pos in update_watermarks(open_positions, bars_by_symbol):
        await save_open_position(pos)

    for pos in open_positions:
        sym = pos.symbol.upper()

        # Step 2: Reconcile pending exit orders
        if pos.exit_pending_order_id:
            await _reconcile_pending_exit(pos)
            continue  # don't evaluate for new exit this cycle

        # Step 3: Check retry cap
        if pos.exit_attempts >= MAX_EXIT_ATTEMPTS:
            # Already capped — skip, manual intervention required
            continue

        # Step 4: Evaluate exit conditions
        df = bars_by_symbol.get(sym)
        if df is None:
            try:
                df = await get_historical_bars(sym, duration="60 D", bar_size="1D")
            except Exception as exc:
                log.warning("Cannot fetch bars for exit check %s: %s", sym, exc)
                continue
        if df is None or len(df) < 2:
            continue

        current_price = float(df["close"].iloc[-1])
        should_exit, reason = check_exits(pos, df, current_price)
        if not should_exit:
            continue

        qty = int(pos.quantity)
        if qty < 1:
            log.warning("Position %s has qty=%s (<1) — removing from tracker", pos.symbol, pos.quantity)
            await _emit({"type": "exit", "symbol": pos.symbol, "reason": "qty_below_1",
                         "action": "SELL" if pos.side == "BUY" else "BUY",
                         "qty": 0, "entry_price": pos.entry_price, "exit_price": 0, "pnl": 0})
            await delete_open_position(pos.id)
            continue

        # Step 5: Place fresh exit order
        await _place_exit_order(pos, sym, qty, current_price, reason)


async def _reconcile_pending_exit(pos) -> None:
    """Resolve a position's pending exit order."""
    from database import get_trade_by_order_id
    now = datetime.now(timezone.utc)

    trade = await get_trade_by_order_id(pos.exit_pending_order_id, symbol=pos.symbol)
    if not trade:
        # Trade record not found — stale order_id, clear and retry
        pos.exit_pending_order_id = None
        pos.exit_attempts += 1
        pos.last_exit_error = "Trade record not found for pending order"
        await save_open_position(pos)
        return

    if trade.status == "FILLED":
        # B3 FIX: Mark as resolved BEFORE delete — prevents double event on delete failure
        pos.exit_pending_order_id = None
        await save_open_position(pos)

        current_price = trade.fill_price or pos.entry_price
        if pos.side == "BUY":
            pnl = round((current_price - pos.entry_price) * pos.quantity, 2)
        else:
            pnl = round((pos.entry_price - current_price) * pos.quantity, 2)
        await _emit({
            "type": "exit", "symbol": pos.symbol, "reason": "pending_fill",
            "action": "SELL" if pos.side == "BUY" else "BUY",
            "qty": int(pos.quantity), "entry_price": pos.entry_price,
            "exit_price": current_price, "pnl": pnl,
        })
        log.info("EXIT FILLED %s pnl=%.2f (reconciled pending)", pos.symbol, pnl)
        await delete_open_position(pos.id)
        return

    if trade.status in ("CANCELLED", "ERROR"):
        # Failed — clear pending, increment attempts, keep position
        pos.exit_pending_order_id = None
        pos.exit_attempts += 1
        pos.last_exit_error = f"Exit order {trade.status}"
        pos.last_exit_attempt_at = now.isoformat()
        await save_open_position(pos)
        log.warning("Exit %s for %s — attempt %d, retrying next cycle",
                     trade.status, pos.symbol, pos.exit_attempts)
        await _check_retry_cap(pos)
        return

    # Still PENDING — check timeout
    if pos.last_exit_attempt_at:
        try:
            placed_at = datetime.fromisoformat(pos.last_exit_attempt_at.replace("Z", "+00:00"))
            elapsed = (now - placed_at).total_seconds()
        except (ValueError, TypeError):
            elapsed = 0
    else:
        elapsed = EXIT_PENDING_TIMEOUT + 1  # force timeout if no timestamp

    if elapsed < EXIT_PENDING_TIMEOUT:
        log.debug("Exit pending for %s (%.0fs elapsed, waiting)", pos.symbol, elapsed)
        return

    # Timed out — attempt cancel
    try:
        from order_executor import cancel_order
        await cancel_order(pos.exit_pending_order_id)
        log.warning("Cancelled timed-out exit order %d for %s", pos.exit_pending_order_id, pos.symbol)
    except Exception as exc:
        log.warning("Failed to cancel exit order %d: %s", pos.exit_pending_order_id, exc)

    pos.exit_pending_order_id = None
    pos.exit_attempts += 1
    pos.last_exit_error = f"Exit order timed out after {EXIT_PENDING_TIMEOUT}s"
    pos.last_exit_attempt_at = now.isoformat()
    await save_open_position(pos)
    await _check_retry_cap(pos)


async def _place_exit_order(pos, sym: str, qty: int, current_price: float, reason: str) -> None:
    """Place a fresh exit order and track it on the position."""
    exit_action = "SELL" if pos.side == "BUY" else "BUY"
    exit_rule = Rule(
        id=pos.rule_id,
        name=f"EXIT:{pos.rule_name}",
        symbol=sym,
        enabled=True,
        conditions=[],
        logic="AND",
        action=TradeAction(
            type=exit_action,  # type: ignore[arg-type]
            asset_type="STK",
            quantity=qty,
            order_type="MKT",
        ),
        cooldown_minutes=0,
    )
    now = datetime.now(timezone.utc)
    try:
        exit_trade = await place_order(exit_rule)
        if not exit_trade:
            pos.exit_attempts += 1
            pos.last_exit_error = "place_order returned None"
            pos.last_exit_attempt_at = now.isoformat()
            await save_open_position(pos)
            return

        if exit_trade.status == "FILLED":
            # B8 FIX: Use actual fill_price, not stale bar close
            fill = exit_trade.fill_price or current_price
            if pos.side == "BUY":
                pnl = round((fill - pos.entry_price) * pos.quantity, 2)
            else:
                pnl = round((pos.entry_price - fill) * pos.quantity, 2)
            await _emit({
                "type": "exit", "symbol": sym, "reason": reason,
                "action": exit_action, "qty": qty,
                "entry_price": pos.entry_price, "exit_price": fill, "pnl": pnl,
            })
            log.info("EXIT %s qty=%d reason='%s' pnl=%.2f", sym, qty, reason, pnl)
            await delete_open_position(pos.id)
        else:
            # PENDING — track the order_id for reconciliation next cycle
            pos.exit_pending_order_id = exit_trade.order_id
            pos.last_exit_attempt_at = now.isoformat()
            await save_open_position(pos)
            log.info("Exit order PENDING for %s (order_id=%s)", sym, exit_trade.order_id)

    except (OrderError, RuntimeError) as exc:
        pos.exit_attempts += 1
        pos.last_exit_error = str(exc)
        pos.last_exit_attempt_at = now.isoformat()
        await save_open_position(pos)
        log.error("Exit order FAILED for %s (attempt %d): %s", sym, pos.exit_attempts, exc)
        await _check_retry_cap(pos)


async def _check_retry_cap(pos) -> None:
    """Emit notification + WebSocket alert if retry cap reached."""
    if pos.exit_attempts >= MAX_EXIT_ATTEMPTS:
        msg = (
            f"Exit failed {pos.exit_attempts}x for {pos.symbol} "
            f"(rule: {pos.rule_name}). Last error: {pos.last_exit_error}. "
            f"Manual close required."
        )
        log.critical("EXIT RETRY CAP: %s", msg)
        # Notify via WebSocket so dashboard shows it
        await _emit({
            "type": "error",
            "message": f"EXIT RETRY CAP: {msg}",
            "symbol": pos.symbol,
            "severity": "critical",
        })


async def _emit(payload: dict) -> None:
    if _broadcast:
        await _broadcast(payload)
