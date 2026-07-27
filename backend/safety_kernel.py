"""
Safety kernel for AI-managed trading actions.

This module owns non-negotiable runtime checks for Autopilot-controlled
entries. It is intentionally conservative and shared by both AI rule
management and direct AI trade execution.

Includes consecutive failure circuit breaker (inspired by nofx):
when AI fails N times in a row, auto-activates emergency stop to protect positions.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone

from config import cfg
from db.execution_lease import validate_fencing_token
from startup import get_execution_fencing_token

# Symbol format validation (shared with api_contracts)
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")

log = logging.getLogger(__name__)

_recent_checks: dict[str, float] = {}
_dedup_lock = asyncio.Lock()
DEDUP_WINDOW = 5  # seconds

# ── Consecutive Failure Circuit Breaker ─────────────────────────────────────
# Tracks AI failures per source. After CONSECUTIVE_FAILURE_THRESHOLD failures
# without any success, auto-triggers emergency stop.

CONSECUTIVE_FAILURE_THRESHOLD = int(
    getattr(cfg, "AI_CONSECUTIVE_FAILURE_THRESHOLD", 3)
)

_failure_counts: dict[str, int] = defaultdict(int)
_last_failure_times: dict[str, float] = {}
_breaker_tripped = False


def record_ai_success(source: str) -> None:
    """Record a successful AI action — resets the failure counter for this source."""
    prev = _failure_counts.get(source, 0)
    _failure_counts[source] = 0
    if prev > 0:
        log.info("AI failure counter reset for '%s' (was %d)", source, prev)


def record_ai_failure(source: str) -> bool:
    """
    Record a failed AI action. Returns True if the circuit breaker tripped.

    When any source accumulates CONSECUTIVE_FAILURE_THRESHOLD failures without
    an intervening success, the breaker trips and emergency_stop is activated.
    """
    global _breaker_tripped
    _failure_counts[source] += 1
    _last_failure_times[source] = time.time()
    count = _failure_counts[source]

    log.warning(
        "AI failure #%d for '%s' (threshold=%d)",
        count, source, CONSECUTIVE_FAILURE_THRESHOLD,
    )

    if count >= CONSECUTIVE_FAILURE_THRESHOLD and not _breaker_tripped:
        _breaker_tripped = True
        log.critical(
            "CIRCUIT BREAKER TRIPPED: %d consecutive AI failures from '%s' — "
            "activating emergency stop to protect positions",
            count, source,
        )
        return True
    return False


async def trip_circuit_breaker(source: str, *, close_positions: bool = False) -> None:
    """Activate emergency stop due to consecutive AI failures.

    If close_positions=True, attempts to close all open positions via market orders.
    """
    try:
        from ai_guardrails import _load_guardrails_from_db, save_guardrails_to_db, log_ai_action

        config = await _load_guardrails_from_db()
        if not config.emergency_stop:
            config.emergency_stop = True
            await save_guardrails_to_db(config)
            await log_ai_action(
                action_type="circuit_breaker_trip",
                category="safety",
                description=(
                    f"Emergency stop activated: {_failure_counts[source]} consecutive "
                    f"AI failures from '{source}'"
                ),
                old_value={"emergency_stop": False},
                new_value={"emergency_stop": True, "trigger_source": source},
                reason=f"Consecutive failure threshold ({CONSECUTIVE_FAILURE_THRESHOLD}) exceeded",
                confidence=1.0,
                status="applied",
            )
            log.critical("Emergency stop ACTIVATED by circuit breaker (source=%s)", source)

        # Optionally close all positions to limit further damage
        if close_positions:
            await _emergency_close_all_positions(source)
    except Exception as exc:
        log.error("Failed to activate emergency stop via circuit breaker: %s", exc)


_TERMINAL_IB_STATUSES = {"Filled", "Cancelled", "ApiCancelled", "Inactive"}


def _wire_terminal_outcome(ib_trade, submit_payload: dict) -> None:
    """Subscribe to ib_trade.statusEvent and emit a terminal outcome log.

    The submit_payload is the "submitted" log dict; we copy its identifying
    fields onto a new payload with the terminal outcome so audit consumers
    can correlate the two log entries by position_id + symbol + ts.

    Idempotent: only the first terminal event triggers a log.
    """
    submit_ts = submit_payload.get("ts")
    fired = {"done": False}

    def _on_status(_trade) -> None:
        if fired["done"]:
            return
        try:
            status = _trade.orderStatus.status
        except Exception:
            return
        if status not in _TERMINAL_IB_STATUSES:
            return
        fired["done"] = True
        terminal_outcome = "filled" if status == "Filled" else "rejected"
        terminal_payload = dict(submit_payload)
        terminal_payload["outcome"] = terminal_outcome
        terminal_payload["reason"] = f"broker_status={status}"
        terminal_payload["ts"] = datetime.now(timezone.utc).isoformat()
        terminal_payload["submitted_ts"] = submit_ts
        try:
            avg_fill = float(_trade.orderStatus.avgFillPrice or 0.0)
            if avg_fill > 0:
                terminal_payload["avg_fill_price"] = avg_fill
        except Exception:
            pass
        log.critical("emergency_close_outcome %s", terminal_payload)

    try:
        ib_trade.statusEvent += _on_status
    except Exception as exc:
        # If subscription itself fails, the audit trail keeps the submitted
        # entry; operators investigating an incident would have to query the
        # broker for terminal state. Better than crashing the emergency loop.
        log.warning("emergency_close: failed to subscribe to statusEvent: %s", exc)


def _is_regular_trading_hours(now_utc: datetime | None = None) -> bool:
    """True if `now_utc` is inside US RTH (Mon-Fri 09:30-16:00 NYSE local).

    Used by emergency close to decide whether a naked-MKT fallback is safe.
    Localizes to America/New_York via zoneinfo so the window is correct in
    both EDT (UTC-4, ~Mar-Nov) and EST (UTC-5, ~Nov-Mar) — the previous
    static UTC window 13:30-20:00 was correct only for EDT and produced
    wrong fallback decisions for half the year.

    A more rigorous check would consult an exchange calendar (NYSE holidays),
    but for emergency-stop purposes "weekday + standard window" is enough —
    holidays just mean the broker rejects and the outcome is recorded.
    """
    from zoneinfo import ZoneInfo
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    ny = now.astimezone(ZoneInfo("America/New_York"))
    if ny.weekday() >= 5:  # Sat/Sun
        return False
    hhmm = ny.hour * 100 + ny.minute
    return 930 <= hhmm <= 1600


async def _emergency_close_all_positions(source: str) -> None:
    """Best-effort emergency close of all open positions.

    Hardened design (see plan: Batch 2):
    - Side derived from pos.qty sign as a defensive backstop on top of
      pos.side (defends against future model drift).
    - Marketable LimitOrder priced from finite IBKR ticker bid/ask/last with
      a ±2% safety band, instead of a naked MarketOrder + outsideRth=True.
    - MKT fallback ONLY when (a) no finite quote AND (b) inside RTH. Outside
      both conditions we record a CRITICAL `emergency_close_no_price` audit
      entry and SKIP the position rather than fire MKT on garbage quotes.
    - Per-position outcome is logged as a structured `emergency_close_outcome`
      audit entry: position_id, symbol, qty, close_action, outcome, reason,
      latency_ms, ts. ``outcome`` is one of:
        ``submitted`` (placeOrder returned, awaiting fill)
        ``rejected`` (broker rejected during submit)
        ``skipped_no_price`` (no finite quote and outside RTH)
        ``skipped_no_qty`` (abs(qty) == 0)
      Terminal status (filled / cancelled) requires an orderStatus
      subscription and is emitted as a separate audit entry by the
      next-iteration patch. Schema is pinned by test_emergency_close.py.
    """
    from ib_insync import LimitOrder as _LmtOrder, MarketOrder as _MktOrder

    from database import get_open_positions
    from ibkr_client import ibkr
    from config import cfg
    from market_data import finite_positive

    started = time.time()
    try:
        if cfg.SIM_MODE or not ibkr.is_connected():
            log.warning("Cannot emergency-close: SIM_MODE=%s connected=%s",
                        cfg.SIM_MODE, ibkr.is_connected() if not cfg.SIM_MODE else "N/A")
            return

        positions = await get_open_positions()
        if not positions:
            return

        log.critical(
            "EMERGENCY CLOSE: attempting to close %d positions (source=%s)",
            len(positions), source,
        )

        in_rth = _is_regular_trading_hours()

        for pos in positions:
            pos_started = time.time()
            outcome_payload: dict = {
                "type": "emergency_close_outcome",
                "position_id": pos.id,
                "symbol": pos.symbol,
                "qty": float(pos.quantity),
                "close_action": "",
                "outcome": "rejected",
                "reason": "",
                "latency_ms": 0,
                "ts": datetime.now(timezone.utc).isoformat(),
                "source": source,
            }
            try:
                # Side from qty sign (defensive) — must agree with pos.side
                if pos.quantity > 0:
                    close_action = "SELL" if pos.side == "BUY" else "BUY"
                else:
                    # Negative qty is a short; close by BUY.
                    close_action = "BUY"
                outcome_payload["close_action"] = close_action

                qty = abs(int(pos.quantity))
                if qty <= 0:
                    outcome_payload.update(outcome="skipped_no_qty", reason="abs(qty) is zero")
                    log.critical("emergency_close_outcome %s", outcome_payload)
                    continue

                contract = ibkr.make_stock_contract(pos.symbol)
                await ibkr.ib.qualifyContractsAsync(contract)

                # Resolve a finite limit price from the latest IBKR ticker.
                limit_px: float | None = None
                try:
                    [ticker] = await asyncio.wait_for(
                        ibkr.ib.reqTickersAsync(contract), timeout=5,
                    )
                    candidates = (
                        ticker.bid if close_action == "SELL" else ticker.ask,
                        ticker.last,
                        ticker.close,
                        ticker.ask if close_action == "SELL" else ticker.bid,
                    )
                    for cand in candidates:
                        px = finite_positive(cand)
                        if px is not None:
                            # ±2% safety band: cross the spread aggressively but
                            # refuse to chase a gap further than 2% away.
                            multiplier = 0.98 if close_action == "SELL" else 1.02
                            limit_px = round(px * multiplier, 2)
                            break
                except Exception as exc:
                    log.warning("emergency_close: ticker fetch failed for %s: %s", pos.symbol, exc)

                if limit_px is not None:
                    if not await validate_fencing_token(get_execution_fencing_token()):
                        outcome_payload.update(
                            outcome="rejected",
                            reason="execution_lease_lost",
                        )
                        log.error("emergency_close: execution lease invalid for %s %s", pos.symbol, close_action)
                    else:
                        order = _LmtOrder(close_action, qty, limit_px)
                        order.tif = "GTC"
                        order.outsideRth = True
                        ib_trade = ibkr.ib.placeOrder(contract, order)
                        # Outcome on the submission log is "submitted". Terminal
                        # state arrives via orderStatus; wire a one-shot handler
                        # that emits a SECOND emergency_close_outcome with the
                        # final outcome (filled / rejected / cancelled).
                        outcome_payload.update(
                            outcome="submitted",
                            reason=f"marketable_limit @ {limit_px}",
                        )
                        _wire_terminal_outcome(ib_trade, outcome_payload)
                elif in_rth:
                    # No finite quote but RTH — MKT is the last resort.
                    if not await validate_fencing_token(get_execution_fencing_token()):
                        outcome_payload.update(
                            outcome="rejected",
                            reason="execution_lease_lost",
                        )
                        log.error("emergency_close: execution lease invalid for %s %s", pos.symbol, close_action)
                    else:
                        order = _MktOrder(close_action, qty)
                        order.tif = "GTC"
                        order.outsideRth = False
                        ib_trade = ibkr.ib.placeOrder(contract, order)
                        outcome_payload.update(
                            outcome="submitted",
                            reason="mkt_fallback_rth_no_finite_quote",
                        )
                        _wire_terminal_outcome(ib_trade, outcome_payload)
                else:
                    # No finite quote AND outside RTH — refuse to send a MKT
                    # order on garbage quotes after-hours. Record and skip.
                    outcome_payload.update(
                        outcome="skipped_no_price",
                        reason="emergency_close_no_price: no finite bid/ask/last and outside RTH",
                    )
                    log.critical(
                        "emergency_close_no_price: skipping %s qty=%d action=%s "
                        "(no finite quote, outside RTH)",
                        pos.symbol, qty, close_action,
                    )

            except Exception as exc:
                outcome_payload.update(outcome="rejected", reason=f"{type(exc).__name__}: {exc}")
                log.error("Failed to emergency-close %s: %s", pos.symbol, exc)
            finally:
                outcome_payload["latency_ms"] = int((time.time() - pos_started) * 1000)
                log.critical("emergency_close_outcome %s", outcome_payload)
    except Exception as exc:
        log.error("Emergency close failed: %s", exc)
    finally:
        log.critical(
            "EMERGENCY CLOSE: complete (source=%s, latency_ms=%d)",
            source, int((time.time() - started) * 1000),
        )


def reset_circuit_breaker() -> None:
    """Manually reset the circuit breaker (e.g., after human review)."""
    global _breaker_tripped
    _breaker_tripped = False
    _failure_counts.clear()
    _last_failure_times.clear()
    log.info("Circuit breaker manually reset")


def get_failure_status() -> dict:
    """Return current failure tracking state for diagnostics."""
    return {
        "breaker_tripped": _breaker_tripped,
        "threshold": CONSECUTIVE_FAILURE_THRESHOLD,
        "counts": dict(_failure_counts),
        "last_failure_times": {
            k: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(v))
            for k, v in _last_failure_times.items()
        },
    }


class SafetyViolation(Exception):
    """Raised when an AI action violates a hard safety rule."""


async def check_all(
    symbol: str,
    side: str,
    quantity: int,
    source: str,
    account_equity: float = 0,
    price_estimate: float = 0,
    *,
    stop_price: float | None = None,
    is_exit: bool = False,
    has_existing_position: bool = False,
    require_autopilot_authority: bool = True,
) -> None:
    """Run the shared runtime safety checks for an order."""
    # Validate and normalize symbol before any action
    symbol = symbol.strip().upper()
    if not _SYMBOL_RE.match(symbol):
        raise SafetyViolation(f"Invalid symbol format: {symbol!r}")

    if require_autopilot_authority and not is_exit:
        await assert_not_killed()
        await assert_daily_loss_not_locked(is_exit=False)
    assert_no_shorts(side, is_exit=is_exit, has_existing_position=has_existing_position)
    if not is_exit:
        assert_risk_budget(quantity, price_estimate, account_equity, stop_price=stop_price)
        await assert_not_duplicate(symbol, side, source)
    log.debug(
        "Safety kernel PASS: side=%s symbol=%s qty=%s source=%s exit=%s authority=%s",
        side,
        symbol,
        quantity,
        source,
        is_exit,
        require_autopilot_authority,
    )


async def assert_not_killed() -> None:
    """Reject if the Autopilot kill switch is active."""
    try:
        from ai_guardrails import _load_guardrails_from_db

        config = await _load_guardrails_from_db()
        if config.autopilot_mode == "OFF":
            raise SafetyViolation("Autopilot is OFF")
        if config.emergency_stop:
            raise SafetyViolation("Kill switch active - all new AI entries blocked")
    except SafetyViolation:
        raise
    except Exception as exc:
        log.error("Kill switch check FAILED (DB unavailable) - blocking for safety: %s", exc)
        raise SafetyViolation("Kill switch check unavailable - blocking for safety")


async def assert_daily_loss_not_locked(*, is_exit: bool = False) -> None:
    """Reject new entries if the daily loss lock is active."""
    if is_exit:
        return
    try:
        from ai_guardrails import _load_guardrails_from_db

        config = await _load_guardrails_from_db()
        if config.daily_loss_locked:
            raise SafetyViolation("Daily loss lock active - new AI entries blocked")
    except SafetyViolation:
        raise
    except Exception as exc:
        log.error("Daily loss check FAILED (DB unavailable) - blocking for safety: %s", exc)
        raise SafetyViolation("Daily loss check unavailable - blocking for safety")


def assert_no_shorts(side: str, *, is_exit: bool = False, has_existing_position: bool = False) -> None:
    """Block any sell-to-open style action."""
    if side.upper() == "SELL" and not is_exit and not has_existing_position:
        raise SafetyViolation("Short entries are disabled")


def assert_risk_budget(
    quantity: int,
    price_estimate: float,
    account_equity: float,
    *,
    stop_price: float | None = None,
) -> None:
    """
    Enforce the 1% hard risk limit.

    For direct AI trades we expect a stop price and measure true per-share risk.
    For rule-driven entries without a concrete stop, we conservatively fall back
    to order notional.
    """
    import math
    # Non-finite inputs (NaN, inf) bypass `<= 0` and `>` comparisons silently.
    # Reject them explicitly before any arithmetic.
    if not math.isfinite(price_estimate):
        raise SafetyViolation(f"price_estimate is not finite ({price_estimate}) - cannot verify risk budget")
    if not math.isfinite(account_equity):
        raise SafetyViolation(f"account_equity is not finite ({account_equity}) - cannot verify risk budget")
    if stop_price is not None and not math.isfinite(stop_price):
        raise SafetyViolation(f"stop_price is not finite ({stop_price}) - cannot verify risk budget")
    if quantity <= 0:
        raise SafetyViolation(f"quantity is {quantity} — cannot verify risk budget")
    if account_equity <= 0:
        raise SafetyViolation(f"account_equity is ${account_equity:.2f} — cannot verify risk budget")
    if price_estimate <= 0:
        raise SafetyViolation("price_estimate is zero - cannot verify risk budget")

    max_risk = account_equity * cfg.RISK_PER_TRADE_PCT / 100
    if stop_price is not None and stop_price > 0:
        risk_amount = abs(price_estimate - stop_price) * quantity
    else:
        risk_amount = quantity * price_estimate

    if not math.isfinite(risk_amount):
        raise SafetyViolation(f"computed risk_amount is not finite ({risk_amount}) - blocking trade")

    if risk_amount > max_risk:
        raise SafetyViolation(
            f"Per-trade risk ${risk_amount:.2f} exceeds {cfg.RISK_PER_TRADE_PCT:.2f}% "
            f"of net liq (${max_risk:.2f})"
        )


async def assert_not_duplicate(symbol: str, side: str, source: str) -> None:
    """Reject duplicate orders within a small rolling window.

    Uses an asyncio.Lock to prevent two concurrent coroutines from
    both passing the dedup check for the same symbol (C3 safety fix).
    """
    key = f"{symbol.upper()}:{side.upper()}:{source}"
    now = time.time()

    async with _dedup_lock:
        stale = [k for k, ts in _recent_checks.items() if (now - ts) > DEDUP_WINDOW * 2]
        for key_to_remove in stale:
            del _recent_checks[key_to_remove]

        last = _recent_checks.get(key)
        if last and (now - last) < DEDUP_WINDOW:
            raise SafetyViolation(f"Duplicate {side} {symbol} from {source} within {DEDUP_WINDOW}s")
        _recent_checks[key] = now


def is_autopilot_live() -> bool:
    return cfg.AUTOPILOT_MODE == "LIVE"


def is_autopilot_active() -> bool:
    return cfg.AUTOPILOT_MODE in ("PAPER", "LIVE")


def get_autopilot_mode() -> str:
    return cfg.AUTOPILOT_MODE
