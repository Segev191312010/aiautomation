"""
Prometheus metrics — ULTRAPLAN v4.

Defines the process-wide metric instruments and exposes a GET /metrics
endpoint for Prometheus scraping.

Other modules import the thin helper functions below (e.g.
``record_webhook_outcome``, ``record_order_placed``) and call them at the
relevant call sites.  The instruments themselves live on the default
prometheus_client REGISTRY so a single ``generate_latest()`` renders every
series the process has touched.

Design notes
------------
- Metric names follow the ``trading_*`` prefix convention and the
  Prometheus ``_total`` suffix for counters.
- The router is not mounted by default. The application only exposes it when
  an operator explicitly selects the isolated-monitoring profile.
- Labels are bounded and never contain symbols, order contents, or PII.
- Helpers are defensive: a bad label value must never raise into a hot
  trading path, so each helper swallows instrumentation errors.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

log = logging.getLogger(__name__)


# ── Instruments ──────────────────────────────────────────────────────────────

# Orders placed, partitioned by originating source and order action.
orders_placed_total = Counter(
    "trading_orders_placed_total",
    "Total orders placed.",
    ["source", "action"],
)

# Orders that reached a filled state, partitioned by originating source.
orders_filled_total = Counter(
    "trading_orders_filled_total",
    "Total orders filled.",
    ["source"],
)

# IBKR client disconnect events.
ibkr_disconnects_total = Counter(
    "trading_ibkr_disconnects_total",
    "Total IBKR disconnect events.",
)

# Wall-clock duration of a bot run cycle.
bot_cycle_seconds = Histogram(
    "trading_bot_cycle_seconds",
    "Duration of a bot run cycle in seconds.",
)

# Times a per-symbol rate cap blocked an action. Do not label this by symbol:
# raw trading-universe labels disclose strategy and create unbounded series.
rate_cap_hits_total = Counter(
    "trading_rate_cap_hits_total",
    "Total rate-cap hits.",
)

# Current autopilot mode: 0=OFF, 1=PAPER, 2=LIVE.
autopilot_mode = Gauge(
    "trading_autopilot_mode",
    "Current autopilot mode (0=OFF, 1=PAPER, 2=LIVE).",
)

# Claude worker LLM calls, partitioned by outcome.
claude_calls_total = Counter(
    "trading_claude_calls_total",
    "Total Claude worker calls.",
    ["outcome"],
)

# Cumulative Claude spend in USD.
claude_cost_usd_total = Counter(
    "trading_claude_cost_usd_total",
    "Cumulative Claude cost in USD.",
)

# TradingView webhook events, partitioned by outcome.
webhook_events_total = Counter(
    "trading_webhook_events_total",
    "Total webhook events.",
    ["outcome"],
)

websocket_events_total = Counter(
    "trading_websocket_events_total",
    "Total general WebSocket lifecycle and routing outcomes.",
    ["outcome"],
)

web_push_delivery_total = Counter(
    "trading_web_push_delivery_total",
    "Total browser Web Push delivery outcomes.",
    ["outcome"],
)

web_push_enabled = Gauge(
    "trading_web_push_enabled",
    "Whether browser Web Push is enabled by environment configuration.",
)

web_push_ready = Gauge(
    "trading_web_push_ready",
    "Whether browser Web Push is enabled and configuration-valid.",
)

# Seconds since the last accepted (non-rejected) signal was processed.
seconds_since_last_accepted_signal = Gauge(
    "trading_seconds_since_last_accepted_signal",
    "Seconds since the last accepted signal.",
)

# Valid webhook outcomes — keeps label cardinality bounded and documents the
# contract for the orchestrator wiring the webhook route.
WEBHOOK_OUTCOMES = (
    "accepted",
    "ip_reject",
    "secret_reject",
    "freshness_reject",
    "duplicate",
)

WEBSOCKET_OUTCOMES = (
    "connected",
    "disconnected",
    "public_delivered",
    "private_delivered",
    "public_rejected",
    "private_rejected",
    "private_missing_owner",
    "unknown_event",
    "send_failed",
)

WEB_PUSH_OUTCOMES = (
    "delivered",
    "expired",
    "failed",
    "blocked",
    "disabled",
    "not_ready",
    "preference_skipped",
    "no_subscription",
)

# Mapping from textual autopilot mode to the gauge's numeric encoding.
_AUTOPILOT_MODE_VALUES = {"OFF": 0, "PAPER": 1, "LIVE": 2}


# ── Helpers (imported + called by other modules; wired by orchestrator) ──────

def record_order_placed(source: str, action: str) -> None:
    """Increment the orders-placed counter for ``source``/``action``."""
    try:
        orders_placed_total.labels(source=source, action=action).inc()
    except Exception:  # never break a trading path on instrumentation
        log.debug("record_order_placed failed", exc_info=True)


def record_order_filled(source: str) -> None:
    """Increment the orders-filled counter for ``source``."""
    try:
        orders_filled_total.labels(source=source).inc()
    except Exception:
        log.debug("record_order_filled failed", exc_info=True)


def record_ibkr_disconnect() -> None:
    """Increment the IBKR disconnect counter."""
    try:
        ibkr_disconnects_total.inc()
    except Exception:
        log.debug("record_ibkr_disconnect failed", exc_info=True)


def observe_bot_cycle_seconds(seconds: float) -> None:
    """Observe a bot-cycle duration sample (seconds)."""
    try:
        bot_cycle_seconds.observe(seconds)
    except Exception:
        log.debug("observe_bot_cycle_seconds failed", exc_info=True)


def record_rate_cap_hit(symbol: str) -> None:
    """Increment the aggregate rate-cap-hit counter.

    ``symbol`` stays in the call signature for compatibility but is
    deliberately not exported as a Prometheus label.
    """
    try:
        rate_cap_hits_total.inc()
    except Exception:
        log.debug("record_rate_cap_hit failed", exc_info=True)


def set_autopilot_mode(mode: str | int) -> None:
    """Set the autopilot-mode gauge.

    Accepts either the textual mode ("OFF"/"PAPER"/"LIVE", case-insensitive)
    or the already-numeric encoding (0/1/2).
    """
    try:
        if isinstance(mode, str):
            value = _AUTOPILOT_MODE_VALUES.get(mode.strip().upper())
            if value is None:
                log.debug("set_autopilot_mode: unknown mode %r", mode)
                return
        else:
            value = int(mode)
        autopilot_mode.set(value)
    except Exception:
        log.debug("set_autopilot_mode failed", exc_info=True)


def record_claude_call(outcome: str) -> None:
    """Increment the Claude-calls counter for ``outcome``."""
    try:
        claude_calls_total.labels(outcome=outcome).inc()
    except Exception:
        log.debug("record_claude_call failed", exc_info=True)


def record_claude_cost(usd: float) -> None:
    """Add ``usd`` to the cumulative Claude-cost counter."""
    try:
        if usd < 0:  # counters are monotonic; guard against bad inputs
            log.debug("record_claude_cost: negative cost %r ignored", usd)
            return
        claude_cost_usd_total.inc(usd)
    except Exception:
        log.debug("record_claude_cost failed", exc_info=True)


def record_webhook_outcome(outcome: str) -> None:
    """Increment the webhook-events counter for ``outcome``.

    ``outcome`` should be one of :data:`WEBHOOK_OUTCOMES`.
    """
    try:
        if outcome not in WEBHOOK_OUTCOMES:
            log.debug("record_webhook_outcome: unknown outcome %r", outcome)
            return
        webhook_events_total.labels(outcome=outcome).inc()
    except Exception:
        log.debug("record_webhook_outcome failed", exc_info=True)


def record_websocket_outcome(outcome: str) -> None:
    """Increment a bounded WebSocket lifecycle or routing outcome."""
    try:
        if outcome not in WEBSOCKET_OUTCOMES:
            log.debug("record_websocket_outcome: unknown outcome %r", outcome)
            return
        websocket_events_total.labels(outcome=outcome).inc()
    except Exception:
        log.debug("record_websocket_outcome failed", exc_info=True)


def record_web_push_delivery(outcome: str) -> None:
    """Increment a bounded browser Web Push delivery outcome."""
    try:
        if outcome not in WEB_PUSH_OUTCOMES:
            log.debug("record_web_push_delivery: unknown outcome %r", outcome)
            return
        web_push_delivery_total.labels(outcome=outcome).inc()
    except Exception:
        log.debug("record_web_push_delivery failed", exc_info=True)


def set_web_push_readiness(*, enabled: bool, ready: bool) -> None:
    """Publish global Web Push feature and readiness state."""
    try:
        web_push_enabled.set(1 if enabled else 0)
        web_push_ready.set(1 if ready else 0)
    except Exception:
        log.debug("set_web_push_readiness failed", exc_info=True)


def set_seconds_since_last_accepted_signal(seconds: float) -> None:
    """Set the staleness gauge for the last accepted signal."""
    try:
        seconds_since_last_accepted_signal.set(seconds)
    except Exception:
        log.debug("set_seconds_since_last_accepted_signal failed", exc_info=True)


# ── Router ───────────────────────────────────────────────────────────────────

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics() -> Response:
    """Render all process metrics in Prometheus text exposition format."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
