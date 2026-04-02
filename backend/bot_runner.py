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
import hashlib
import time
from datetime import datetime, timezone
from typing import Callable, Optional
from config import cfg
from database import (
    get_rules,
    save_rule,
    get_trades,
    get_trade,
    get_open_positions,
    save_open_position,
    delete_open_position,
    save_trade,
    finalize_trade_outcome,
)
from market_data import get_historical_bars, clear_bar_cache, get_latest_price
from rule_engine import evaluate_all
from order_executor import OrderError, place_order
from models import Rule, Trade, TradeAction
from position_tracker import check_exits, update_watermarks
from services import order_lifecycle, order_recovery, safety_gate
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

# ── Bot health state ────────────────────────────────────────────────────────
_cycle_day: Optional[str] = None
_cycle_count_today: int = 0
_error_timestamps: list[float] = []
_degraded_timestamps: list[float] = []
_last_error_message: Optional[str] = None
_last_signal_symbol: Optional[str] = None
_last_cycle_started_at: Optional[str] = None
_last_cycle_completed_at: Optional[str] = None
_last_successful_ibkr_heartbeat_at: Optional[str] = None
_last_order_submit_at: Optional[str] = None
_last_fill_event_at: Optional[str] = None
_last_bot_health_emit_at: float = 0.0

# ── Liquidity pre-screen cache ─────────────────────────────────────────────
# Rebuilt once per day from the full us_all universe.
# Filters: last close > $5, average 5-day volume > 500k shares.
_PRESCREEN_MIN_PRICE  = 5.0
_PRESCREEN_MIN_VOLUME = 500_000
_PRESCREEN_TTL        = 86_400          # refresh every 24 h
_prescreen_cache: dict[str, tuple[list[str], float]] = {}
_screened_symbols: list[str] = []  # legacy shim for the renamed helper
_screened_at: float = 0.0


def set_broadcast(cb: Callable) -> None:
    global _broadcast
    _broadcast = cb
    _set_exit_broadcast(cb)


def is_running() -> bool:
    return _running


def get_last_run() -> Optional[str]:
    return _last_run


def get_next_run() -> Optional[str]:
    return _next_run


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prune_timestamps(bucket: list[float], *, window_seconds: int = 86_400) -> None:
    cutoff = time.time() - window_seconds
    while bucket and bucket[0] < cutoff:
        bucket.pop(0)


def _record_cycle_start() -> None:
    global _cycle_day, _cycle_count_today, _last_cycle_started_at
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _cycle_day != today:
        _cycle_day = today
        _cycle_count_today = 0
    _cycle_count_today += 1
    _last_cycle_started_at = _now_iso()


def _record_cycle_complete() -> None:
    global _last_cycle_completed_at
    _last_cycle_completed_at = _now_iso()


def _record_bot_error(message: str) -> None:
    global _last_error_message
    _error_timestamps.append(time.time())
    _prune_timestamps(_error_timestamps)
    _last_error_message = message


def _record_degraded_event() -> None:
    _degraded_timestamps.append(time.time())
    _prune_timestamps(_degraded_timestamps)


def _record_signal_symbol(symbol: str | None) -> None:
    global _last_signal_symbol
    if symbol:
        _last_signal_symbol = symbol.upper()


def _record_ibkr_heartbeat() -> None:
    global _last_successful_ibkr_heartbeat_at
    _last_successful_ibkr_heartbeat_at = _now_iso()


def _record_order_submit() -> None:
    global _last_order_submit_at
    _last_order_submit_at = _now_iso()


def _record_fill_event() -> None:
    global _last_fill_event_at
    _last_fill_event_at = _now_iso()


def get_bot_health() -> dict:
    _prune_timestamps(_error_timestamps)
    _prune_timestamps(_degraded_timestamps)

    now = datetime.now(timezone.utc)
    minutes_since_last_cycle: float | None = None
    stale_warning = False
    ibkr_connected = False

    if _last_cycle_completed_at:
        try:
            last_cycle = datetime.fromisoformat(_last_cycle_completed_at.replace("Z", "+00:00"))
            minutes_since_last_cycle = round((now - last_cycle).total_seconds() / 60.0, 2)
            # Use bot interval (not WS threshold) — bot cycles every BOT_INTERVAL_SECONDS
            stale_threshold = max(cfg.BOT_INTERVAL_SECONDS * 2, 60)
            stale_warning = (now - last_cycle).total_seconds() > stale_threshold
        except (TypeError, ValueError):
            minutes_since_last_cycle = None

    if cfg.SIM_MODE:
        ibkr_connected = False
    else:
        try:
            from ibkr_client import ibkr as _ibkr_health

            ibkr_connected = bool(_ibkr_health.is_connected())
            if ibkr_connected:
                _record_ibkr_heartbeat()
        except Exception as exc:
            log.debug("Health probe: ibkr_connected check failed: %s", exc)
            ibkr_connected = bool(_last_successful_ibkr_heartbeat_at)

    return {
        "is_running": _running,
        "minutes_since_last_cycle": minutes_since_last_cycle,
        "total_cycles_today": _cycle_count_today,
        "error_count_24h": len(_error_timestamps),
        "ibkr_connected": ibkr_connected,
        "stale_warning": stale_warning,
        "last_error_message": _last_error_message,
        "last_signal_symbol": _last_signal_symbol,
        "last_successful_ibkr_heartbeat_at": _last_successful_ibkr_heartbeat_at,
        "last_order_submit_at": _last_order_submit_at,
        "last_fill_event_at": _last_fill_event_at,
        "degraded_mode_count_24h": len(_degraded_timestamps),
    }


async def _emit_bot_health(force: bool = False) -> None:
    global _last_bot_health_emit_at
    if not cfg.ENABLE_BOT_HEALTH_MONITORING:
        return
    now = time.time()
    if not force and (now - _last_bot_health_emit_at) < 60:
        return
    _last_bot_health_emit_at = now
    await _emit({
        "type": "bot_health",
        **get_bot_health(),
    })


def _prescreen_cache_key(candidates: list[str]) -> str:
    normalized = ",".join(sorted(sym.upper() for sym in candidates))
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def _expand_universe(universe_id: str) -> list[str]:
    """Expand a universe identifier to a list of symbols."""
    if universe_id == "all":
        symbols: set[str] = set()
        for uid in ("sp500", "nasdaq100", "etfs"):
            symbols.update(load_universe(uid))
        return sorted(symbols)
    # us_all and any other named universe load directly from their JSON file
    return load_universe(universe_id)


async def _legacy_prescreen_universe_unused(candidates: list[str]) -> list[str]:
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
                        except Exception as exc:
                            log.debug("Pre-screen: malformed bars for %s: %s", sym, exc)
                            _record_degraded_event()
                else:
                    # Single symbol returned as flat df
                    sym = batch[0]
                    try:
                        last_close = float(raw["Close"].iloc[-1])
                        avg_volume = float(raw["Volume"].mean())
                        if last_close >= _PRESCREEN_MIN_PRICE and avg_volume >= _PRESCREEN_MIN_VOLUME:
                            liquid.append(sym.upper())
                    except Exception as exc:
                        log.debug("Pre-screen %s skipped (malformed bars): %s", sym, exc)

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


async def _prescreen_universe(candidates: list[str]) -> list[str]:
    """
    Override the legacy pre-screen helper with a cache keyed by candidate set.

    This avoids cross-contaminating large universes while keeping the call site
    unchanged.
    """
    import asyncio as _asyncio
    import time as _time_mod

    now = _time_mod.time()
    cache_key = _prescreen_cache_key(candidates)
    cached = _prescreen_cache.get(cache_key)
    if cached and (now - cached[1]) < _PRESCREEN_TTL:
        log.info("Pre-screen cache hit for %d candidates -> %d liquid symbols", len(candidates), len(cached[0]))
        return cached[0]

    log.info(
        "Pre-screening %d symbols (price>$%.0f, vol>%s) ...",
        len(candidates),
        _PRESCREEN_MIN_PRICE,
        f"{_PRESCREEN_MIN_VOLUME:,}",
    )

    try:
        import pandas as pd
        import yfinance as yf

        liquid: list[str] = []
        batch_size = 1000
        loop = _asyncio.get_running_loop()

        for i in range(0, len(candidates), batch_size):
            batch = candidates[i:i + batch_size]
            try:
                raw = await loop.run_in_executor(
                    None,
                    lambda b=batch: yf.download(
                        b,
                        period="5d",
                        interval="1d",
                        auto_adjust=True,
                        progress=False,
                        group_by="ticker",
                        threads=True,
                    ),
                )
                if raw.empty:
                    continue

                if isinstance(raw.columns, pd.MultiIndex):
                    for sym in batch:
                        try:
                            sym_df = raw[sym].dropna(how="all")
                            if sym_df.empty:
                                continue
                            last_close = float(sym_df["Close"].iloc[-1])
                            avg_volume = float(sym_df["Volume"].mean())
                            if last_close >= _PRESCREEN_MIN_PRICE and avg_volume >= _PRESCREEN_MIN_VOLUME:
                                liquid.append(sym.upper())
                        except Exception as exc:
                            log.debug("Pre-screen: malformed bars for %s: %s", sym, exc)
                            _record_degraded_event()
                else:
                    sym = batch[0]
                    try:
                        last_close = float(raw["Close"].iloc[-1])
                        avg_volume = float(raw["Volume"].mean())
                        if last_close >= _PRESCREEN_MIN_PRICE and avg_volume >= _PRESCREEN_MIN_VOLUME:
                            liquid.append(sym.upper())
                    except Exception as exc:
                        log.debug("Pre-screen %s skipped (malformed bars): %s", sym, exc)

                log.info(
                    "Pre-screen batch %d/%d done -> %d liquid so far",
                    i // batch_size + 1,
                    -(-len(candidates) // batch_size),
                    len(liquid),
                )
            except Exception as exc:
                log.warning("Pre-screen batch %d failed: %s", i // batch_size, exc)

        screened_symbols = sorted(set(liquid))
        _prescreen_cache[cache_key] = (screened_symbols, now)
        log.info(
            "Pre-screen complete: %d / %d symbols pass liquidity filter",
            len(screened_symbols),
            len(candidates),
        )
        return screened_symbols
    except Exception as exc:
        log.error("Pre-screen failed, falling back to full list: %s", exc)
        _record_degraded_event()
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
        import time as _time
        cycle_start = _time.monotonic()
        _record_cycle_start()
        _last_run = datetime.now(timezone.utc).isoformat()

        # Schedule next_run BEFORE the cycle so status event has correct value
        from datetime import timedelta
        _next_run = (datetime.now(timezone.utc) + timedelta(seconds=cfg.BOT_INTERVAL_SECONDS)).isoformat()

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
            _record_cycle_complete()
        except Exception as exc:
            log.exception("Error in bot cycle: %s", exc)
            _record_bot_error(str(exc))
            await _emit({"type": "error", "message": str(exc)})
        finally:
            await _emit_bot_health(force=True)

        # Sleep only the remaining interval (cycle_duration + sleep = BOT_INTERVAL)
        elapsed = _time.monotonic() - cycle_start
        remaining = max(0, cfg.BOT_INTERVAL_SECONDS - elapsed)
        if remaining > 0:
            await asyncio.sleep(remaining)
        else:
            _record_degraded_event()
            log.warning(
                "Bot cycle exceeded configured interval: elapsed=%.2fs interval=%ss",
                elapsed,
                cfg.BOT_INTERVAL_SECONDS,
            )


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

    # Fast path: use IBKR scanner for real-time market scanning (2-sec cycles)
    _ibkr_connected = False
    if cfg.BOT_INTERVAL_SECONDS <= 30:
        try:
            from ibkr_client import ibkr as _ibkr_scan
            _ibkr_connected = _ibkr_scan.is_connected()
        except Exception as exc:
            log.debug("IBKR scanner check failed, using yfinance fallback: %s", exc)
    if cfg.BOT_INTERVAL_SECONDS <= 30 and _ibkr_connected:
        try:
            from ibkr_scanner import get_scan_symbols
            scanner_symbols = await get_scan_symbols(["hot_us_stocks", "top_gainers", "gap_up"])
            # Add open position symbols (always need to check exits)
            for pos in open_positions:
                if pos.symbol.upper() not in scanner_symbols:
                    scanner_symbols.append(pos.symbol.upper())
            all_symbols = set(scanner_symbols)
            log.info("IBKR scanner: %d symbols for fast cycle", len(all_symbols))
        except Exception as exc:
            log.warning("IBKR scanner failed, falling back to universe: %s", exc)

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
                            except Exception as exc:
                                log.debug("Skipped %s in bulk fetch (parse error): %s", sym, exc)
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
    _exited_this_cycle.clear()
    try:
        await _process_exits(open_positions, bars_by_symbol)
    except Exception as exc:
        log.exception("Exit processing failed: %s", exc)

    try:
        from execution_brain import drain_direct_candidates

        direct_candidates = drain_direct_candidates()
    except Exception as exc:
        log.debug("Direct candidate queue unavailable: %s", exc)
        direct_candidates = []

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
    if cfg.AUTOPILOT_MODE == "OFF":
        log.info("Autopilot OFF — skipping new entries")
        triggered = []
    else:
        triggered = evaluate_all(enabled, bars_by_symbol, universe_cache)

    # ── Score and rank signals ───────────────────────────────────────────────
    rule_candidates: list[dict] = []
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
        rule_candidates = [
            {
                "symbol": str(r["_symbol"]).upper(),
                "trigger_symbol": r["_symbol"],
                "source": "rule",
                "score": float(r["composite_score"]),
                "risk_pct": float(cfg.RISK_PER_TRADE_PCT),
                "is_exit": r["_rule"].action.type == "SELL",
                "rule": r["_rule"],
            }
            for r in ranked
        ]
    except Exception as exc:
        log.warning("Signal scoring failed, using unranked: %s", exc)
        rule_candidates = [
            {
                "symbol": str(sym).upper(),
                "trigger_symbol": sym,
                "source": "rule",
                "score": 50.0,
                "risk_pct": float(cfg.RISK_PER_TRADE_PCT),
                "is_exit": rule.action.type == "SELL",
                "rule": rule,
            }
            for rule, sym in triggered
        ]

    # ── Execute triggered rules ───────────────────────────────────────────────
    total_signals = len(rule_candidates) + len(direct_candidates)
    selected_candidates: list[dict] = []
    if cfg.AUTOPILOT_MODE != "OFF":
        try:
            from execution_brain import choose_candidates

            selected_candidates = choose_candidates(rule_candidates, direct_candidates)
            if selected_candidates:
                log.info("Execution brain selected %d / %d candidates", len(selected_candidates), total_signals)
        except Exception as exc:
            log.warning("Execution brain unavailable, falling back to ordered candidates: %s", exc)
            selected_candidates = rule_candidates + direct_candidates
    orders_placed = 0
    max_orders_per_cycle = cfg.MAX_TRADES_PER_CYCLE if hasattr(cfg, 'MAX_TRADES_PER_CYCLE') else 5
    executed_symbols: set[str] = set()
    approved_this_cycle: list[dict] = []

    # -- Fetch positions + pending orders once for risk + concentration checks
    positions: list[dict] = []
    pending_buy_orders: list[dict] = []
    cycle_net_liq: float = 0.0
    concentration_inputs_ready = True
    try:
        if not cfg.SIM_MODE:
            from ibkr_client import ibkr as _ibkr_pos
            positions = [p.__dict__ if hasattr(p, '__dict__') else p for p in (await _ibkr_pos.get_positions() or [])]
            try:
                cycle_net_liq = (await _ibkr_pos.get_account_summary()).balance or 0.0
                _record_ibkr_heartbeat()
            except Exception as exc:
                log.warning("Account summary fetch failed, using zero net_liq (will block trades): %s", exc)
                cycle_net_liq = 0.0
        else:
            from simulation import sim_engine
            positions = [p.model_dump() for p in await sim_engine.get_positions()]
            cycle_net_liq = (await sim_engine.get_account()).net_liquidation or 0.0
        # Gather pending BUY trades as additional exposure for concentration checks
        try:
            recent_trades = await get_trades(limit=20)
            for t in recent_trades:
                if getattr(t, "status", "") in ("PENDING", "SUBMITTED") and getattr(t, "action", "") == "BUY":
                    est_price = getattr(t, "limit_price", None) or getattr(t, "fill_price", None) or 100
                    pending_buy_orders.append({
                        "symbol": t.symbol,
                        "market_value": float(est_price) * float(getattr(t, "quantity", 0) or 0),
                        "side": "BUY",
                    })
        except Exception as pending_exc:
            concentration_inputs_ready = False
            log.warning("Pending BUY exposure fetch failed; skipping new trades this cycle: %s", pending_exc)
    except Exception as pos_exc:
        concentration_inputs_ready = False
        log.warning("Position fetch for concentration check failed; skipping new trades this cycle: %s", pos_exc)

    # ── Stage 1.8: Build correlation matrix once per cycle ────────────────
    cycle_corr_matrix: dict | None = None
    if cfg.ENABLE_PORTFOLIO_CONCENTRATION_ENFORCEMENT:
        try:
            corr_syms = set()
            for p in positions:
                if isinstance(p, dict) and p.get("symbol"):
                    corr_syms.add(p["symbol"].upper())
            for c in selected_candidates:
                if c.get("symbol"):
                    corr_syms.add(str(c["symbol"]).upper())
            corr_syms_list = [s for s in corr_syms if s]
            if len(corr_syms_list) >= 2:
                from portfolio_analytics import compute_correlation_matrix
                cycle_corr_matrix = compute_correlation_matrix(corr_syms_list)
                log.info("Correlation matrix built for %d symbols", len(corr_syms_list))
        except Exception as corr_exc:
            log.debug("Correlation matrix unavailable this cycle: %s", corr_exc)
    for candidate in selected_candidates:
        if orders_placed >= max_orders_per_cycle:
            log.info("Max orders per cycle (%d) reached — deferring remaining signals", max_orders_per_cycle)
            break

        source = str(candidate.get("source", "rule"))
        symbol = str(candidate.get("symbol", "")).upper()
        if not symbol or symbol in executed_symbols:
            continue

        # Prevent same-cycle exit+re-entry churn (double slippage + commissions)
        is_exit = bool(candidate.get("is_exit"))
        if not is_exit and symbol in _exited_this_cycle:
            log.info("Skipping re-entry of %s — exited this cycle (churn prevention)", symbol)
            continue

        if source == "ai_direct":
            # Concentration check for direct AI BUYs (same guard as rule path)
            preview: dict | None = None
            if cfg.ENABLE_PORTFOLIO_CONCENTRATION_ENFORCEMENT:
                _ai_action = str(candidate.get("decision", {}).get("action", "BUY")).upper()
                if _ai_action == "BUY":
                    try:
                        from direct_ai_trader import preview_direct_trade
                        from risk_manager import check_portfolio_impact
                        preview = await preview_direct_trade(dict(candidate.get("decision", {})))
                        impact = check_portfolio_impact(
                            symbol=symbol,
                            side="BUY",
                            positions=positions,
                            net_liq=cycle_net_liq,
                            candidate_notional=float(preview["notional"]),
                            pending_orders=pending_buy_orders,
                            approved_candidates=approved_this_cycle,
                            corr_matrix=cycle_corr_matrix,
                        )
                        if not impact.allowed:
                            log.warning(
                                "Concentration BLOCKED ai_direct %s: reason=%s | %s",
                                symbol, impact.reason, impact.details,
                            )
                            continue
                    except Exception as exc:
                        # HB1-04: Fail closed � do not execute when concentration check errors
                        log.warning("Concentration check FAILED for ai_direct %s -- blocking trade: %s", symbol, exc)
                        continue

            try:
                from api_contracts import AIDirectTrade
                from direct_ai_trader import execute_direct_trade

                decision = AIDirectTrade(**dict(candidate.get("decision", {})))
                _record_signal_symbol(decision.symbol)
                outcome = await execute_direct_trade(decision)
                trade_payload = outcome.get("trade", {})
                # Only count if execution actually succeeded (not error/rejected)
                if outcome.get("status") == "error" or not trade_payload:
                    log.warning("Direct AI trade for %s returned error/empty — not counting", decision.symbol)
                    continue
                direct_rule_id = f"ai-direct:{decision.symbol.upper()}"
                order_event = OrderEvent(
                    timestamp=now,
                    type=EventType.ORDER,
                    symbol=decision.symbol.upper(),
                    order_type=decision.order_type,
                    quantity=float(trade_payload.get("quantity", 0) or 0),
                    price=trade_payload.get("limit_price"),
                    direction="LONG" if decision.action == "BUY" else "SHORT",
                    rule_id=direct_rule_id,
                    stop_price=decision.stop_price,
                )
                event_bus.publish(order_event)
                event_logger.log_event(order_event)
                orders_placed += 1
                _record_order_submit()
                if decision.action == "BUY":
                    approved_this_cycle.append({
                        "symbol": decision.symbol.upper(),
                        "market_value": float(
                            preview["notional"] if preview is not None else (
                                float(trade_payload.get("quantity", 0) or 0)
                                * float(trade_payload.get("fill_price") or trade_payload.get("limit_price") or 0)
                            )
                        ),
                        "side": "BUY",
                    })
                executed_symbols.add(decision.symbol.upper())
                if trade_payload.get("fill_price") is not None:
                    fill_event = FillEvent(
                        timestamp=now,
                        type=EventType.FILL,
                        symbol=decision.symbol.upper(),
                        quantity=float(trade_payload.get("quantity", 0) or 0),
                        fill_price=float(trade_payload.get("fill_price") or 0),
                        commission=1.0,
                        direction="LONG" if decision.action == "BUY" else "SHORT",
                        rule_id=direct_rule_id,
                        order_id=trade_payload.get("order_id"),
                    )
                    event_bus.publish(fill_event)
                    event_logger.log_event(fill_event)
                    _record_fill_event()
                metrics.record("orders_placed", orders_placed)
                await _emit({
                    "type": "signal",
                    "source": "ai_direct",
                    "symbol": decision.symbol,
                    "action": decision.action,
                    "trade_id": trade_payload.get("id"),
                    "order_id": trade_payload.get("order_id"),
                    "reason": decision.reason,
                })
            except Exception as exc:
                log.warning("Direct AI trade blocked for %s: %s", symbol, exc)
            continue

        rule = candidate.get("rule")
        trigger_symbol = str(candidate.get("trigger_symbol", symbol)).upper()
        if not isinstance(rule, Rule):
            log.warning("Skipping malformed rule candidate for %s", symbol)
            continue
        _record_signal_symbol(trigger_symbol)

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
                if acct:
                    _record_ibkr_heartbeat()
            if available_cash < 100:
                log.warning("Insufficient cash ($%.2f) — skipping remaining signals", available_cash)
                break
        except Exception as exc:
            log.debug("Cash check failed, proceeding: %s", exc)

        # HB1-02: Dynamic sizing MUST run BEFORE risk check so guards see the real quantity

        # ── Dynamic position sizing: 0.5% of account NetLiquidation ─────────
        price = 0.0
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

        # Risk check � runs AFTER sizing so guards see the final quantity
        try:
            from risk_manager import check_trade_risk
            from risk_config import DEFAULT_LIMITS
        except Exception as exc:
            log.warning("Risk guard imports failed for %s; skipping trade: %s", order_rule.symbol, exc)
            continue

        try:
            if not cfg.SIM_MODE:
                risk_positions = [p.__dict__ if hasattr(p, '__dict__') else p for p in (await ibkr.get_positions() or [])]
            else:
                from simulation import sim_engine
                risk_positions = [p.model_dump() for p in await sim_engine.get_positions()]
        except Exception as exc:
            log.warning("Risk position fetch failed for %s; skipping trade: %s", order_rule.symbol, exc)
            continue

        try:
            risk_result = check_trade_risk(
                order_rule.symbol, order_rule.action.quantity,
                order_rule.action.type, risk_positions,
                available_cash, DEFAULT_LIMITS
            )
        except Exception as exc:
            log.warning("Risk check failed closed for %s; skipping trade: %s", order_rule.symbol, exc)
            continue

        if risk_result.status == "BLOCK":
            log.warning("Risk BLOCKED %s %s: %s", order_rule.action.type, order_rule.symbol, risk_result.reasons)
            continue

        # Guard: skip order if price is unknown (safety_kernel will reject anyway)
        if price <= 0 and order_rule.action.type == "BUY":
            log.warning("No price available for %s — skipping entry", order_rule.symbol)
            continue

        # -- Stage 1.9: Portfolio concentration check (after sizing, before safety kernel)
        if cfg.ENABLE_PORTFOLIO_CONCENTRATION_ENFORCEMENT:
            if not concentration_inputs_ready:
                log.warning("Concentration guard unavailable for %s; skipping trade", order_rule.symbol)
                continue
            try:
                from risk_manager import check_portfolio_impact
                _cand_notional = order_rule.action.quantity * price if price > 0 else order_rule.action.quantity * 100
                impact = check_portfolio_impact(
                    symbol=order_rule.symbol,
                    side=order_rule.action.type,
                    positions=positions,
                    net_liq=cycle_net_liq if cycle_net_liq > 0 else available_cash,
                    candidate_notional=_cand_notional,
                    pending_orders=pending_buy_orders,
                    approved_candidates=approved_this_cycle,
                    corr_matrix=cycle_corr_matrix,
                )
                if not impact.allowed:
                    log.warning(
                        "Concentration BLOCKED %s %s: reason=%s sector=%s weight=%.1f%%->%.1f%% corr_count=%s | %s",
                        order_rule.action.type, order_rule.symbol,
                        impact.reason, impact.sector,
                        impact.sector_weight_before or 0, impact.sector_weight_after or 0,
                        impact.correlated_count, impact.details,
                    )
                    continue
            except Exception as exc:
                log.warning("Portfolio impact check failed closed for %s; skipping trade: %s", order_rule.symbol, exc)
                continue
        allowed, reason = await safety_gate.evaluate_runtime_safety(
            symbol=order_rule.symbol,
            side=order_rule.action.type,
            quantity=order_rule.action.quantity,
            source="rule",
            account_equity=available_cash,
            price_estimate=price,
            is_exit=False,
            require_autopilot_authority=True,
        )
        if not allowed:
            log.warning("Runtime safety gate REJECTED %s %s: %s", order_rule.action.type, order_rule.symbol, reason)
            continue
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
            if cfg.AUTOPILOT_MODE == "LIVE" and not cfg.SIM_MODE:
                trade = await place_order(order_rule, source="rule", skip_safety=False,
                                        is_exit=is_exit, has_existing_position=is_exit)
            else:
                _fill_price = price if price > 0 else (order_rule.action.limit_price or 0)
                if _fill_price <= 0:
                    log.warning("No fill price for paper trade %s — skipping", order_rule.symbol)
                    continue
                trade = await _create_paper_trade(order_rule, _fill_price)
            # Only count as placed if trade is non-null and not ERROR
            if trade is None or getattr(trade, "status", "") == "ERROR":
                log.warning("Order for %s returned %s — not counting as placed",
                            order_rule.symbol, "None" if trade is None else "ERROR")
                continue
            orders_placed += 1
            _record_order_submit()
            # Track approved candidate for same-cycle concentration checks
            if order_rule.action.type == "BUY":
                approved_this_cycle.append({
                    "symbol": order_rule.symbol,
                    "market_value": order_rule.action.quantity * (price if price > 0 else 100),
                    "side": "BUY",
                })
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
                _record_fill_event()
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
        except Exception as exc:
            log.warning("Signal notification failed for %s/%s: %s", rule.name, trigger_symbol, exc)

        if trade:
            executed_symbols.add(trigger_symbol.upper())
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


async def _create_paper_trade(order_rule: Rule, fill_price: float | None) -> Trade:
    now_iso = datetime.now(timezone.utc).isoformat()
    trade = Trade(
        rule_id=order_rule.id,
        rule_name=order_rule.name,
        symbol=order_rule.symbol,
        action=order_rule.action.type,  # type: ignore[arg-type]
        asset_type=order_rule.action.asset_type,
        quantity=order_rule.action.quantity,
        order_type=order_rule.action.order_type,
        limit_price=order_rule.action.limit_price,
        fill_price=fill_price,
        status="FILLED",
        order_id=None,
        timestamp=now_iso,
        source="rule",
        metadata={"paper": True, "autopilot_mode": cfg.AUTOPILOT_MODE},
        mode="PAPER",
        opened_at=now_iso,
        entry_price=fill_price,
    )
    trade.position_id = trade.id
    await save_trade(trade)
    return trade


# Exit lifecycle (extracted to bot_exits.py)
from bot_exits import (  # noqa: E402
    MAX_EXIT_ATTEMPTS, EXIT_PENDING_TIMEOUT,
    _process_exits, _reconcile_pending_exit, _place_exit_order, _check_retry_cap,
    _exited_this_cycle, clear_exited_this_cycle, was_exited_this_cycle,
    set_broadcast as _set_exit_broadcast,
)


async def _emit(payload: dict) -> None:
    if _broadcast:
        await _broadcast(payload)



# _reconcile_pending_exit, _place_exit_order, _check_retry_cap — extracted to bot_exits.py




async def _emit(payload: dict) -> None:
    if _broadcast:
        await _broadcast(payload)








